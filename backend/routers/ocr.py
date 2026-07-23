# -*- coding: utf-8 -*-
"""Persistent OCR task API with optional SSE progress streaming."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.auth_utils import get_current_user
from backend.database import SessionLocal, Task, get_db
from backend.model_availability import require_models
from backend.ocr_contract import build_ocr_stream_payload
from backend.ocr_engine import (
    DEFAULT_MODEL,
    SUPPORTED_OUTPUT_FORMATS,
    SUPPORTED_PROVIDERS,
    SUPPORTED_TASKS,
    get_correction_map,
    get_ocr_capabilities,
    merge_document_results,
    merge_page_results,
    process_file_stream,
    unload_ocr_resources,
    update_correction_map,
)

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

ALLOWED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tiff",
    ".tif",
    ".webp",
    ".gif",
    ".pdf",
}
MAX_FILE_SIZE = 50 * 1024 * 1024
UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


def _start_ocr_thread(args: tuple) -> None:
    """Dispatch seam used by production and API tests."""

    threading.Thread(target=_run_ocr_task, args=args, daemon=True).start()


def _task_snapshot(task: Task) -> dict[str, Any]:
    return build_ocr_stream_payload(
        task_id=task.id,
        status=task.status,
        progress=task.progress or 0,
        progress_message=task.progress_message,
        error_message=task.error_message,
        result_data=task.get_result_data(),
    )


def _load_task_snapshot(task_id: int, owner_id: str) -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        task = db.query(Task).filter(
            Task.id == task_id,
            Task.owner_id == owner_id,
        ).first()
        return _task_snapshot(task) if task is not None else None
    finally:
        db.close()


def _run_ocr_task(task_id: int, file_path: str, options: dict[str, Any]) -> None:
    """Run OCR independently of the client connection and persist every page."""

    db = SessionLocal()
    task = None
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task is None:
            return

        task.status = "processing"
        task.progress_message = "正在辨識..."
        db.commit()

        file_bytes = Path(file_path).read_bytes()
        results: list[dict[str, Any]] = []
        extraction_pages: list[dict[str, Any]] = []
        completed = False

        for item in process_file_stream(
            file_bytes=file_bytes,
            filename=task.filename,
            fields=options["fields"],
            provider=options["provider"],
            task=options["task"],
            model=options["model"],
            max_retries=options["max_retries"],
            enable_layout=options["enable_layout"],
            output_format=options["output_format"],
            dpi=options["dpi"],
        ):
            if item.get("error"):
                task.status = "failed"
                task.progress = float(item.get("percent", task.progress or 0))
                task.progress_message = "辨識失敗"
                task.error_message = str(item["error"])
                task.set_result_data({"results": results})
                task.completed_at = datetime.now(timezone.utc)
                db.commit()
                return

            result = item["result"]
            page_result: dict[str, Any] = {
                "page": item["page"],
                "total": item["total"],
                "percent": item["percent"],
                **result,
                "done": item["done"],
            }
            results.append(page_result)
            if options["task"] == "extract" and result.get("data") is not None:
                extraction_pages.append(result["data"])

            persisted: dict[str, Any] = {"results": results}
            if item["done"]:
                persisted["document"] = merge_document_results(results)
                if options["auto_merge"] and len(extraction_pages) > 1:
                    persisted["merged"] = merge_page_results(extraction_pages)

            task.progress = float(item["percent"])
            task.progress_message = f"已完成第 {item['page']} / {item['total']} 頁"
            task.set_result_data(persisted)
            if item["done"]:
                task.status = "completed"
                task.progress = 100.0
                task.progress_message = "辨識完成"
                task.completed_at = datetime.now(timezone.utc)
                completed = True
            db.commit()

        if not completed:
            task.status = "failed"
            task.progress_message = "辨識失敗"
            task.error_message = "OCR 未產生任何結果"
            task.completed_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as exc:
        db.rollback()
        if task is not None:
            try:
                task.status = "failed"
                task.progress_message = "辨識失敗"
                task.error_message = str(exc)
                task.completed_at = datetime.now(timezone.utc)
                db.commit()
            except Exception:
                db.rollback()
    finally:
        db.close()


@router.get("/capabilities")
async def capabilities():
    """Describe installed providers, tasks and optional pipeline features."""

    return get_ocr_capabilities()


@router.post("/process")
async def ocr_process(
    request: Request,
    file: UploadFile = File(...),
    fields: str = Form("{}"),
    task: str = Form("auto"),
    provider: str = Form(os.getenv("OCR_PROVIDER", "local")),
    model: str = Form(os.getenv("OCR_MODEL", DEFAULT_MODEL)),
    max_retries: int = Form(3),
    auto_merge: bool = Form(False),
    enable_layout: bool = Form(False),
    output_format: str = Form("both"),
    dpi: int = Form(200),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create a persistent OCR task and stream database-backed snapshots."""

    original_filename = Path(file.filename or "").name
    extension = Path(original_filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的檔案格式: {extension}；支援 {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    try:
        fields_dict = json.loads(fields or "{}")
        if not isinstance(fields_dict, dict):
            raise ValueError("fields 必須是 JSON 物件")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"fields 格式錯誤: {exc}") from exc

    normalized_task = task.strip().lower()
    if normalized_task == "auto":
        normalized_task = "extract" if fields_dict else "text"
    if normalized_task not in SUPPORTED_TASKS:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的 task: {task}；可用值為 {', '.join(SUPPORTED_TASKS)}",
        )
    if normalized_task == "extract" and not fields_dict:
        raise HTTPException(status_code=400, detail="欄位擷取需要提供 JSON 欄位")
    if normalized_task != "extract":
        fields_dict = {}

    normalized_provider = provider.strip().lower()
    if normalized_provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的 provider: {provider}；可用值為 {', '.join(SUPPORTED_PROVIDERS)}",
        )
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise HTTPException(status_code=400, detail=f"不支援的 output_format: {output_format}")

    required_models = []
    if normalized_provider == "local":
        required_models.append("glm_ocr")
    layout_enabled = enable_layout and normalized_task == "document"
    if layout_enabled:
        required_models.append("pp_doclayout")
    require_models(required_models)

    max_retries = max(1, min(max_retries, 5))
    dpi = max(72, min(dpi, 300))
    file_bytes = await file.read(MAX_FILE_SIZE + 1)
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="檔案大小不可超過 50 MB")
    if not file_bytes:
        raise HTTPException(status_code=400, detail="上傳檔案不可為空")

    video_id = f"ocr_{uuid.uuid4().hex[:16]}"
    save_path = UPLOAD_DIR / f"{video_id}{extension}"
    save_path.write_bytes(file_bytes)
    options = {
        "fields": fields_dict,
        "task": normalized_task,
        "provider": normalized_provider,
        "model": model or os.getenv("OCR_MODEL", DEFAULT_MODEL),
        "max_retries": max_retries,
        "auto_merge": auto_merge,
        "enable_layout": layout_enabled,
        "output_format": output_format,
        "dpi": dpi,
    }

    persistent_task = Task(
        owner_id=current_user["owner_id"],
        task_type="ocr",
        video_id=video_id,
        filename=original_filename,
        status="pending",
        model=options["model"],
        language=normalized_task,
        enable_diarization=False,
        to_traditional=True,
        progress=0.0,
        progress_message="等待中",
    )
    persistent_task.set_task_options(options)
    persistent_task.set_result_data({"results": []})
    try:
        db.add(persistent_task)
        db.commit()
        db.refresh(persistent_task)
    except Exception:
        db.rollback()
        save_path.unlink(missing_ok=True)
        raise

    try:
        _start_ocr_thread((persistent_task.id, str(save_path), options))
    except Exception as exc:
        persistent_task.status = "failed"
        persistent_task.progress_message = "啟動失敗"
        persistent_task.error_message = f"無法啟動 OCR 背景工作: {exc}"
        persistent_task.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=500, detail=persistent_task.error_message) from exc

    async def event_generator():
        last_payload = None
        while True:
            if await request.is_disconnected():
                return
            snapshot = await asyncio.to_thread(
                _load_task_snapshot,
                persistent_task.id,
                current_user["owner_id"],
            )
            if snapshot is None:
                return
            payload = json.dumps(snapshot, ensure_ascii=False)
            if payload != last_payload:
                last_payload = payload
                yield f"data: {payload}\n\n"
            if snapshot["done"]:
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/unload")
async def unload_model():
    """Release GLM-OCR and PP-DocLayout immediately."""

    unloaded = await asyncio.to_thread(unload_ocr_resources)
    return {"ok": True, "unloaded": unloaded}


@router.get("/correction-map")
async def get_map():
    return get_correction_map()


@router.put("/correction-map")
async def put_map(payload: dict):
    try:
        update_correction_map(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "count": len(payload)}
