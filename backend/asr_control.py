# -*- coding: utf-8 -*-
"""Thread-safe cooperative cancellation registry for ASR background tasks."""
from __future__ import annotations

import threading


_events: dict[int, threading.Event] = {}
_events_lock = threading.Lock()


def get_or_create_cancel_event(task_id: int) -> threading.Event:
    """Return the stable cancellation event associated with a task."""
    with _events_lock:
        event = _events.get(task_id)
        if event is None:
            event = threading.Event()
            _events[task_id] = event
        return event


def request_cancel(task_id: int) -> bool:
    """Request cancellation, creating an event when the worker has not started yet."""
    event = get_or_create_cancel_event(task_id)
    already_cancelled = event.is_set()
    event.set()
    return not already_cancelled


def clear_cancel_event(task_id: int) -> None:
    """Release registry state after the background worker reaches a terminal state."""
    with _events_lock:
        _events.pop(task_id, None)


def active_control_count() -> int:
    """Expose registry size for diagnostics and leak regression tests."""
    with _events_lock:
        return len(_events)
