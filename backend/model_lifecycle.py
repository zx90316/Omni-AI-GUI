"""Shared idle-time lifecycle management for non-ASR in-process models.

The Manager passes ``MODEL_IDLE_TIMEOUT_MINUTES`` to the backend.  Every
registered resource keeps its weights loaded while requests are active and is
unloaded after the configured period with no active users, matching Ollama's
keep-alive behaviour.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Optional


logger = logging.getLogger(__name__)
DEFAULT_MODEL_IDLE_TIMEOUT_MINUTES = 5
MIN_MODEL_IDLE_TIMEOUT_MINUTES = 1
MAX_MODEL_IDLE_TIMEOUT_MINUTES = 24 * 60


def get_model_idle_timeout_minutes() -> int:
    """Return the validated model keep-alive duration supplied by Manager."""

    raw_value = os.getenv(
        "MODEL_IDLE_TIMEOUT_MINUTES",
        str(DEFAULT_MODEL_IDLE_TIMEOUT_MINUTES),
    )
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = DEFAULT_MODEL_IDLE_TIMEOUT_MINUTES
    return max(
        MIN_MODEL_IDLE_TIMEOUT_MINUTES,
        min(value, MAX_MODEL_IDLE_TIMEOUT_MINUTES),
    )


@dataclass
class _ResourceState:
    unload: Callable[[], object]
    active: int = 0
    loaded: bool = False
    unloading: bool = False
    generation: int = 0
    last_used: Optional[float] = None
    timer: Optional[threading.Timer] = None


class ModelIdleManager:
    """Thread-safe keep-alive timers for independently unloadable resources."""

    def __init__(self, timeout_seconds: Optional[float] = None):
        self.timeout_seconds = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(get_model_idle_timeout_minutes() * 60)
        )
        self._condition = threading.Condition(threading.RLock())
        self._resources: dict[str, _ResourceState] = {}
        self._closed = False

    def register(self, name: str, unload: Callable[[], object]) -> None:
        """Register or replace an idempotent unload callback."""

        with self._condition:
            state = self._resources.get(name)
            if state is None:
                self._resources[name] = _ResourceState(unload=unload)
            else:
                state.unload = unload

    @contextmanager
    def activity(self, name: str) -> Iterator[None]:
        """Prevent a resource from being unloaded for the duration of a use."""

        self._begin(name)
        try:
            yield
        finally:
            self._end(name)

    def mark_loaded(self, name: str) -> None:
        """Record successful loading and schedule expiry when currently idle."""

        with self._condition:
            state = self._require(name)
            while state.unloading:
                self._condition.wait()
            state.loaded = True
            state.last_used = time.monotonic()
            state.generation += 1
            self._cancel_timer(state)
            if state.active == 0 and not self._closed:
                self._schedule(name, state)

    def touch(self, name: str) -> None:
        """Restart a loaded resource's idle timer without entering an activity."""

        with self._condition:
            state = self._require(name)
            if not state.loaded:
                return
            state.last_used = time.monotonic()
            state.generation += 1
            self._cancel_timer(state)
            if state.active == 0 and not self._closed:
                self._schedule(name, state)

    def unload_now(self, name: str) -> bool:
        """Wait for active users, then synchronously unload one resource."""

        with self._condition:
            state = self._require(name)
            while state.active > 0 or state.unloading:
                self._condition.wait()
            if not self._prepare_unload(state):
                return False
        return self._run_unload(name, state)

    def shutdown(self) -> None:
        """Cancel timers and synchronously release every registered resource."""

        with self._condition:
            self._closed = True
            for state in self._resources.values():
                self._cancel_timer(state)

        for name in tuple(self._resources):
            try:
                self.unload_now(name)
            except Exception:
                logger.exception("關閉時卸載模型資源 %s 失敗", name)

    def status(self) -> dict[str, dict[str, object]]:
        """Return a diagnostic snapshot without exposing callbacks or timers."""

        with self._condition:
            now = time.monotonic()
            return {
                name: {
                    "loaded": state.loaded,
                    "active": state.active,
                    "unloading": state.unloading,
                    "idle_seconds": (
                        max(0.0, now - state.last_used)
                        if state.last_used is not None and state.active == 0
                        else 0.0
                    ),
                }
                for name, state in self._resources.items()
            }

    def _require(self, name: str) -> _ResourceState:
        try:
            return self._resources[name]
        except KeyError as exc:
            raise KeyError(f"尚未註冊模型資源：{name}") from exc

    def _begin(self, name: str) -> None:
        with self._condition:
            if self._closed:
                raise RuntimeError("模型生命週期管理器已關閉")
            state = self._require(name)
            while state.unloading:
                self._condition.wait()
            state.active += 1
            state.generation += 1
            self._cancel_timer(state)

    def _end(self, name: str) -> None:
        with self._condition:
            state = self._require(name)
            state.active = max(0, state.active - 1)
            state.last_used = time.monotonic()
            state.generation += 1
            if state.active == 0:
                self._condition.notify_all()
                if state.loaded and not self._closed:
                    self._schedule(name, state)

    @staticmethod
    def _cancel_timer(state: _ResourceState) -> None:
        timer = state.timer
        state.timer = None
        if timer is not None:
            timer.cancel()

    def _schedule(self, name: str, state: _ResourceState) -> None:
        generation = state.generation
        timer = threading.Timer(
            self.timeout_seconds,
            self._expire,
            args=(name, generation),
        )
        timer.daemon = True
        state.timer = timer
        timer.start()

    def _expire(self, name: str, generation: int) -> None:
        with self._condition:
            state = self._resources.get(name)
            if (
                state is None
                or self._closed
                or state.generation != generation
                or state.active > 0
                or not state.loaded
                or state.unloading
            ):
                return
            state.timer = None
            if not self._prepare_unload(state):
                return
        self._run_unload(name, state)

    def _prepare_unload(self, state: _ResourceState) -> bool:
        """Mark an unload while holding the condition lock."""

        self._cancel_timer(state)
        if not state.loaded:
            return False

        state.unloading = True
        state.generation += 1
        return True

    def _run_unload(self, name: str, state: _ResourceState) -> bool:
        """Invoke user cleanup outside the manager lock to avoid lock inversion."""

        succeeded = False
        try:
            state.unload()
            succeeded = True
        except Exception:
            logger.exception("閒置卸載模型資源 %s 失敗", name)
        with self._condition:
            if succeeded:
                state.loaded = False
                state.last_used = None
            else:
                state.last_used = time.monotonic()
            state.generation += 1
            state.unloading = False
            if not succeeded and not self._closed:
                self._schedule(name, state)
            self._condition.notify_all()

        if succeeded:
            logger.info("模型資源 %s 已在閒置逾時後卸載", name)
        return succeeded


model_idle_manager = ModelIdleManager()
