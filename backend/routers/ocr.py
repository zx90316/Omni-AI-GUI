# -*- coding: utf-8 -*-
"""
OCR Router — 上傳圖片/PDF 並透過 Ollama glm-ocr 進行 OCR 辨識
"""
import json
import asyncio
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse

from backend.auth_utils import get_current_user
from backend.ocr_engine import process_file_stream

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".gif", ".pdf"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


@router.post("/process")
async def ocr_process(
    file: UploadFile = File(...),
    fields: str = Form(...),
    model: str = Form("glm-ocr"),
    max_retries: int = Form(3),
    current_user: dict = Depends(get_current_user),
):
    """
    上傳圖片或 PDF 進行 OCR 辨識。

    - file: 圖片 (PNG/JPG/BMP/TIFF/WEBP) 或 PDF
    - fields: JSON 字串，定義要擷取的欄位，例如 {"製作日期": "YYYYMMDD", "報告編號": ""}
    - model: Ollama 模型名稱，預設 glm-ocr
    - max_retries: 最大重試次數，預設 3
    """
    # 驗證檔案類型
    import pathlib
    ext = pathlib.Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的檔案格式: {ext}，支援: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    # 解析 fields JSON
    try:
        fields_dict = json.loads(fields)
        if not isinstance(fields_dict, dict):
            raise ValueError("fields 必須是 JSON 物件")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"fields 格式錯誤: {e}")

    # 讀取檔案
    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="檔案大小超過 50 MB 限制")

    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")

    # SSE Generator
    async def event_generator():
        results = []
        gen = process_file_stream(
            file_bytes=file_bytes,
            filename=file.filename or "upload",
            fields=fields_dict,
            ollama_host=ollama_host,
            model=model,
            max_retries=max_retries,
        )
        for item in gen:
            # 如果有錯誤 (例如 PDF 解析失敗)
            if "error" in item and item["error"]:
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
            page_data = {
                "page": item["page"],
                "total": item["total"],
                "percent": item["percent"],
                "success": result["success"],
                "data": result["data"],
                "raw": result["raw"],
                "error": result["error"],
                "done": item["done"],
            }
            results.append({**page_data})

            if item["done"]:
                # 最後一筆：附帶所有結果
                page_data["all_results"] = results

            yield f"data: {json.dumps(page_data, ensure_ascii=False)}\n\n"

            # 避免阻塞 Event Loop
            await asyncio.sleep(0.01)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
