# -*- coding: utf-8 -*-
"""Model status and proactive download operations for the Manager GUI."""
import os
import json
import subprocess
import threading
from typing import Callable, Iterable

from backend.model_cache import ModelCacheStatus, inspect_model_cache
from backend.model_registry import MODEL_SPECS, get_model_spec
from manager.config import PROJECT_ROOT, get_venv_python, is_venv_exists
from manager.process_manager import _service_creation_flags, terminate_process_tree


OutputCallback = Callable[[str], None]
EventCallback = Callable[[dict], None]
EVENT_PREFIX = "OMNI_MODEL_EVENT "


class ModelDownloadController:
    """Own the active worker process and provide thread-safe cancellation."""

    def __init__(self):
        self._lock = threading.RLock()
        self._active = False
        self._cancelled = threading.Event()
        self._process: subprocess.Popen | None = None

    def begin(self) -> bool:
        with self._lock:
            if self._active:
                return False
            self._active = True
            self._cancelled.clear()
            self._process = None
            return True

    def attach(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._process = process
            if self._cancelled.is_set() and process.poll() is None:
                terminate_process_tree(process, timeout=5)

    def detach(self, process: subprocess.Popen) -> None:
        with self._lock:
            if self._process is process:
                self._process = None

    def cancel(self) -> bool:
        with self._lock:
            if not self._active:
                return False
            self._cancelled.set()
            process = self._process
            if process is not None and process.poll() is None:
                try:
                    terminate_process_tree(process, timeout=10)
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    pass
            return True

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def finish(self) -> None:
        with self._lock:
            self._process = None
            self._active = False


_download_controller = ModelDownloadController()


def _load_project_env() -> None:
    """Keep Manager cache checks and worker downloads on the same configured paths."""
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass


_load_project_env()


def get_models_status() -> dict[str, ModelCacheStatus]:
    return {spec.key: _inspect(spec) for spec in MODEL_SPECS}


def _inspect(spec) -> ModelCacheStatus:
    return inspect_model_cache(spec.model_id)


def _creation_flags() -> int:
    return _service_creation_flags()


def cancel_model_download() -> bool:
    return _download_controller.cancel()


def is_model_download_active() -> bool:
    return _download_controller.is_active()


def parse_worker_event(line: str) -> dict | None:
    if not line.startswith(EVENT_PREFIX):
        return None
    try:
        event = json.loads(line[len(EVENT_PREFIX):])
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) and isinstance(event.get("type"), str) else None


def download_models(
    model_keys: Iterable[str],
    on_output: OutputCallback | None = None,
    on_event: EventCallback | None = None,
    *,
    missing_only: bool = False,
) -> bool:
    """Download model snapshots serially so large weights do not compete for disk/network."""
    def out(message: str) -> None:
        if on_output:
            on_output(message)

    def emit(event: dict) -> None:
        if on_event:
            try:
                on_event(event)
            except Exception:
                pass

    keys = list(dict.fromkeys(model_keys))
    if not keys:
        out("ℹ️ 沒有需要下載的模型")
        return True
    if not is_venv_exists():
        out("❌ .venv 不存在或已損壞，請先建立環境並安裝 Python 依賴")
        return False
    if not _download_controller.begin():
        out("⚠️ 已有模型下載作業正在進行")
        return False

    try:
        child_env = os.environ.copy()
        child_env.pop("HF_HUB_OFFLINE", None)
        child_env.pop("TRANSFORMERS_OFFLINE", None)
        child_env["PYTHONIOENCODING"] = "utf-8"
        success = True

        for index, key in enumerate(keys, start=1):
            if _download_controller.is_cancelled():
                out("⏹️ 模型下載已取消")
                emit({"type": "cancelled"})
                success = False
                break
            try:
                spec = get_model_spec(key)
            except ValueError as exc:
                out(f"❌ {exc}")
                success = False
                continue

            current = _inspect(spec)
            if missing_only and current.cached:
                out(f"⏭️ [{index}/{len(keys)}] {spec.label} 已存在，跳過")
                continue

            suffix = "（需要 HF_TOKEN 與模型授權）" if spec.gated else ""
            out(f"📥 [{index}/{len(keys)}] 正在下載 {spec.label} {suffix}")
            cmd = [
                str(get_venv_python()),
                "-m",
                "manager.model_downloader",
                spec.model_id,
                "--source",
                spec.source,
            ]
            process = None
            try:
                process = subprocess.Popen(
                    cmd,
                    cwd=str(PROJECT_ROOT),
                    env=child_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=_creation_flags(),
                )
                _download_controller.attach(process)
                assert process.stdout is not None
                for line in process.stdout:
                    line = line.rstrip()
                    if line:
                        event = parse_worker_event(line)
                        if event is not None:
                            event.update({"key": spec.key, "label": spec.label})
                            emit(event)
                        else:
                            out(f"   {line}")
                return_code = process.wait()
            except OSError as exc:
                out(f"❌ 無法啟動模型下載工作器: {exc}")
                if process is not None and process.poll() is None:
                    try:
                        process.terminate()
                        process.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                success = False
                continue
            finally:
                if process is not None:
                    _download_controller.detach(process)

            if _download_controller.is_cancelled():
                out(f"⏹️ {spec.label} 下載已取消；下次可從部分快取繼續")
                emit({"type": "cancelled", "key": spec.key, "label": spec.label})
                success = False
                break

            status = _inspect(spec)
            if return_code == 0 and status.cached:
                out(f"✅ {spec.label} 下載並驗證完成")
            else:
                out(f"❌ {spec.label} 下載失敗：{status.detail}")
                success = False

        return success
    finally:
        _download_controller.finish()
