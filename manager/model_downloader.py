# -*- coding: utf-8 -*-
"""Small .venv worker used by Manager to download and verify one model."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time


EVENT_PREFIX = "OMNI_MODEL_EVENT "


def emit_event(event_type: str, **payload) -> None:
    event = {"type": event_type, **payload}
    print(EVENT_PREFIX + json.dumps(event, ensure_ascii=False), flush=True)


class _DownloadProgress:
    def __init__(self, model_id: str, total: int = 0, completed: int = 0):
        self.model_id = model_id
        self.total = max(0, int(total))
        self.completed = max(0, int(completed))
        self._lock = threading.Lock()
        self._last_emit = 0.0

    def advance(self, amount: int) -> None:
        with self._lock:
            self.completed += max(0, int(amount))
            now = time.monotonic()
            if now - self._last_emit < 0.2:
                return
            self._last_emit = now
            self.emit()

    def emit(self, *, force_complete: bool = False) -> None:
        if self.total:
            completed = self.total if force_complete else min(self.completed, self.total)
            # Hugging Face may report both downloaded and reconstructed bytes on
            # Windows. Reserve 100% for the verified local snapshot so the UI
            # never claims completion while reconstruction is still running.
            percent = 100.0 if force_complete else min(99.0, completed / self.total * 100)
        else:
            completed = self.completed
            percent = None
        emit_event(
            "progress",
            model_id=self.model_id,
            completed_bytes=completed,
            total_bytes=self.total or None,
            percent=round(percent, 2) if percent is not None else None,
        )


def _load_project_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass


def _snapshot_root_from_plan(plan) -> Path | None:
    """Resolve the cache snapshot root from Hugging Face dry-run metadata."""
    for item in plan or []:
        commit_hash = str(getattr(item, "commit_hash", "") or "")
        local_path = getattr(item, "local_path", None)
        if not commit_hash or not local_path:
            continue
        current = Path(local_path)
        while current != current.parent:
            if current.name == commit_hash:
                return current
            current = current.parent
    return None


def download_model(model_id: str, max_workers: int = 4) -> Path:
    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm

    _load_project_env()
    token = os.environ.get("HF_TOKEN", "").strip() or None
    total = 0
    completed = 0
    plan = []
    try:
        plan = snapshot_download(repo_id=model_id, token=token, dry_run=True)
        if isinstance(plan, list):
            for item in plan:
                size = int(getattr(item, "file_size", 0) or 0)
                total += size
                if bool(getattr(item, "is_cached", False)):
                    completed += size
    except (TypeError, AttributeError):
        # Older huggingface_hub versions do not expose dry_run metadata.
        pass

    progress = _DownloadProgress(model_id, total=total, completed=completed)
    emit_event("start", model_id=model_id, source="huggingface", total_bytes=total or None)
    progress.emit()

    class JsonProgressBar(tqdm):
        def update(self, n=1):
            result = super().update(n)
            progress.advance(n or 0)
            return result

    try:
        snapshot_path = snapshot_download(
            repo_id=model_id,
            token=token,
            max_workers=max(1, max_workers),
            tqdm_class=JsonProgressBar,
        )
    except OSError as exc:
        snapshot_root = _snapshot_root_from_plan(plan)
        if getattr(exc, "winerror", None) != 1314 or snapshot_root is None:
            raise
        emit_event(
            "phase",
            model_id=model_id,
            phase="windows_copy_fallback",
            message="Windows 不允許建立 symlink，改以普通檔案建立 snapshot",
        )
        with tempfile.TemporaryDirectory(prefix="omni_hf_snapshot_") as temp_dir:
            local_path = snapshot_download(
                repo_id=model_id,
                token=token,
                local_dir=temp_dir,
                max_workers=max(1, max_workers),
            )
            snapshot_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                local_path,
                snapshot_root,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".cache"),
            )
        snapshot_path = str(snapshot_root)
    emit_event("phase", model_id=model_id, phase="verify", message="驗證本機 snapshot")
    verified_path = snapshot_download(
        repo_id=model_id,
        token=token,
        local_files_only=True,
    )
    progress.emit(force_complete=True)
    emit_event("complete", model_id=model_id, path=str(verified_path))
    return Path(snapshot_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下載 Omni AI 使用的本機模型")
    parser.add_argument("model_id")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--source", choices=("huggingface",), default="huggingface")
    args = parser.parse_args(argv)
    try:
        download_model(args.model_id, args.max_workers)
        return 0
    except Exception as exc:
        emit_event("error", model_id=args.model_id, message=str(exc))
        print(f"MODEL_DOWNLOAD_ERROR {args.model_id}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
