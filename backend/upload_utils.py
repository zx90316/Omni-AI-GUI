# -*- coding: utf-8 -*-
"""Streaming validation helpers shared by ASR upload routes."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, UploadFile


ASR_MEDIA_EXTENSIONS = frozenset(
    {
        ".aac",
        ".flac",
        ".m4a",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".ogg",
        ".opus",
        ".wav",
        ".webm",
        ".wma",
    }
)


def validated_upload_name(
    upload: UploadFile,
    allowed_extensions: Iterable[str] = ASR_MEDIA_EXTENSIONS,
) -> tuple[str, str]:
    """Return a safe display name and normalized extension or raise HTTP 400."""
    original_name = Path(upload.filename or "").name.strip()
    if not original_name:
        raise HTTPException(status_code=400, detail="上傳檔案缺少檔名")

    extension = Path(original_name).suffix.lower()
    allowed = frozenset(item.lower() for item in allowed_extensions)
    if extension not in allowed:
        supported = ", ".join(sorted(allowed))
        raise HTTPException(
            status_code=400,
            detail=f"不支援的媒體格式: {extension or '(無副檔名)'}；支援 {supported}",
        )
    return original_name, extension


async def save_upload_limited(
    upload: UploadFile,
    destination: Path,
    max_bytes: int,
    chunk_size: int = 1024 * 1024,
) -> int:
    """Stream an upload to disk with deterministic empty/oversize cleanup."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with destination.open("xb") as output:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"檔案超過 {max_bytes // (1024 * 1024)} MB 上限",
                    )
                output.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="上傳檔案是空的")
        return total
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
