# -*- coding: utf-8 -*-
"""OCR HTTP API with streaming progress and provider discovery."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

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
    unload_ocr_models,
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
_END = object()


def _next_item(iterator):
    return next(iterator, _END)


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
):
    """Recognize an image/PDF and stream one SSE event per page.

    Tasks: ``document``, ``text``, ``table``, ``formula`` and ``extract``.
    Providers: in-process ``local`` (default), OpenAI-compatible ``openai`` and
    optional legacy ``ollama``.
    """

    extension = Path(file.filename or "").suffix.lower()
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
        raise HTTPException(status_code=400, detail="欄位萃取至少需要一個 JSON 欄位")
    if normalized_task != "extract":
        fields_dict = {}

    normalized_provider = provider.strip().lower()
    if normalized_provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的 provider: {provider}；可用值為 {', '.join(SUPPORTED_PROVIDERS)}",
        )
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的 output_format: {output_format}",
        )

    max_retries = max(1, min(max_retries, 5))
    dpi = max(72, min(dpi, 300))
    file_bytes = await file.read(MAX_FILE_SIZE + 1)
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="檔案大小超過 50 MB 限制")
    if not file_bytes:
        raise HTTPException(status_code=400, detail="上傳檔案是空的")

    async def event_generator():
        results = []
        extraction_pages = []
        iterator = iter(
            process_file_stream(
                file_bytes=file_bytes,
                filename=file.filename or "upload",
                fields=fields_dict,
                provider=normalized_provider,
                task=normalized_task,
                model=model or os.getenv("OCR_MODEL", DEFAULT_MODEL),
                max_retries=max_retries,
                enable_layout=enable_layout and normalized_task == "document",
                output_format=output_format,
                dpi=dpi,
            )
        )
        while True:
            if await request.is_disconnected():
                return
            # Model inference is blocking.  Running generator advancement in a
            # worker thread keeps unrelated FastAPI requests responsive.
            item = await asyncio.to_thread(_next_item, iterator)
            if item is _END:
                return

            if item.get("error"):
                payload = {
                    "page": item["page"],
                    "total": item["total"],
                    "percent": item["percent"],
                    "error": item["error"],
                    "done": True,
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return

            result = item["result"]
            page_result: dict[str, Any] = {
                "page": item["page"],
                "total": item["total"],
                "percent": item["percent"],
                **result,
                "done": item["done"],
            }
            results.append(dict(page_result))
            if normalized_task == "extract" and result.get("data") is not None:
                extraction_pages.append(result["data"])

            if item["done"]:
                page_result["all_results"] = results
                page_result["document"] = merge_document_results(results)
                if auto_merge and len(extraction_pages) > 1:
                    page_result["merged"] = merge_page_results(extraction_pages)

            yield f"data: {json.dumps(page_result, ensure_ascii=False)}\n\n"

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
    """Release the in-process model and GPU memory."""

    unloaded = await asyncio.to_thread(unload_ocr_models)
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
