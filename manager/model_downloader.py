# -*- coding: utf-8 -*-
"""Small .venv worker used by Manager to download and verify one model."""
import argparse
import json
import os
from pathlib import Path
import sys
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
        completed = self.total if force_complete and self.total else self.completed
        percent = min(100.0, completed / self.total * 100) if self.total else None
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


def download_model(model_id: str, max_workers: int = 4) -> Path:
    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm

    _load_project_env()
    token = os.environ.get("HF_TOKEN", "").strip() or None
    total = 0
    completed = 0
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

    snapshot_path = snapshot_download(
        repo_id=model_id,
        token=token,
        max_workers=max(1, max_workers),
        tqdm_class=JsonProgressBar,
    )
    emit_event("phase", model_id=model_id, phase="verify", message="驗證本機 snapshot")
    verified_path = snapshot_download(
        repo_id=model_id,
        token=token,
        local_files_only=True,
    )
    progress.emit(force_complete=True)
    emit_event("complete", model_id=model_id, path=str(verified_path))
    return Path(snapshot_path)


def download_paddlex_model(model_name: str) -> None:
    """Use PaddleX's public API so files land in the cache used by GLM-OCR."""
    _load_project_env()
    from paddlex import create_model

    emit_event("start", model_id=model_name, source="paddlex", total_bytes=None)
    emit_event("phase", model_id=model_name, phase="download", message="PaddleX 正在取得官方推論模型")
    model = create_model(model_name=model_name, device="cpu")
    del model
    emit_event("phase", model_id=model_name, phase="verify", message="檢查 PaddleX 推論檔案")
    emit_event("complete", model_id=model_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下載 Omni AI 使用的本機模型")
    parser.add_argument("model_id")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--source", choices=("huggingface", "paddlex"), default="huggingface")
    args = parser.parse_args(argv)
    try:
        if args.source == "paddlex":
            download_paddlex_model(args.model_id)
        else:
            download_model(args.model_id, args.max_workers)
        return 0
    except Exception as exc:
        emit_event("error", model_id=args.model_id, message=str(exc))
        print(f"MODEL_DOWNLOAD_ERROR {args.model_id}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
