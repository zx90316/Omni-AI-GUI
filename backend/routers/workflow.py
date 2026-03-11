# -*- coding: utf-8 -*-
"""
Workflow Router — 以圖搜頁 (Top 1) + OCR 欄位擷取
"""
import json
import asyncio
import os
import pathlib
from typing import List

from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse
from backend.clip_engine import search_similar_pages
from backend.ocr_engine import build_ocr_prompt, _call_glm_ocr

router = APIRouter(prefix="/api/workflow", tags=["workflow"])

ALLOWED_PDF_EXT = {".pdf"}
ALLOWED_IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".gif"}
MAX_PDF_SIZE = 100 * 1024 * 1024   # 100 MB
MAX_IMG_SIZE = 20 * 1024 * 1024    # 20 MB per image

@router.post("/clip-ocr-top1")
async def clip_ocr_top1(
    pdf_file: UploadFile = File(...),
    ref_images: List[UploadFile] = File(...),
    must_include: str = Form(""),
    must_exclude: str = Form(""),
    threshold: float = Form(0.5),
    fields: str = Form(...),
    model: str = Form("glm-ocr"),
    max_retries: int = Form(3),
):
    # PDF check
    pdf_ext = pathlib.Path(pdf_file.filename or "").suffix.lower()
    if pdf_ext not in ALLOWED_PDF_EXT:
        raise HTTPException(status_code=400, detail=f"僅支援 PDF 檔案，收到: {pdf_ext}")

    pdf_bytes = await pdf_file.read()
    if len(pdf_bytes) > MAX_PDF_SIZE:
        raise HTTPException(status_code=400, detail="PDF 檔案大小超過 100 MB 限制")

    # Images check
    ref_images_bytes = []
    for img_file in ref_images:
        img_ext = pathlib.Path(img_file.filename or "").suffix.lower()
        if img_ext not in ALLOWED_IMG_EXT:
            raise HTTPException(status_code=400, detail=f"不支援的圖片格式: {img_ext}")
        img_bytes = await img_file.read()
        if len(img_bytes) > MAX_IMG_SIZE:
            raise HTTPException(status_code=400, detail=f"圖片 {img_file.filename} 大小超過 20 MB 限制")
        ref_images_bytes.append(img_bytes)

    if len(ref_images_bytes) == 0:
        raise HTTPException(status_code=400, detail="至少需要上傳一張參考圖片")

    # fields check
    try:
        fields_dict = json.loads(fields)
        if not isinstance(fields_dict, dict):
            raise ValueError("fields 必須是 JSON 物件")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"fields 格式錯誤: {e}")

    threshold = max(0.0, min(1.0, threshold))
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")

    prompt = build_ocr_prompt(fields_dict)
    raw_mode = len(fields_dict) == 0

    async def event_generator():
        # Step 1: CLIP Search
        gen = search_similar_pages(
            pdf_bytes=pdf_bytes,
            ref_images_bytes=ref_images_bytes,
            must_include=must_include,
            must_exclude=must_exclude,
            threshold=threshold,
            top_k=1,
        )
        
        top_page_data = None
        for item in gen:
            if item.get("type") == "error":
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                return
            elif item.get("type") == "progress":
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.01)
            elif item.get("type") == "results":
                results_data = item.get("data", [])
                if not results_data:
                    yield f"data: {json.dumps({'type': 'error', 'error': '沒有找到符合的結果'}, ensure_ascii=False)}\n\n"
                    return
                top_page_data = results_data[0]
        
        if not top_page_data:
            return

        # Step 2: OCR
        ocr_progress = {
            "type": "progress",
            "percent": 100,
            "status": "找到目標頁面，正在進行 OCR 擷取...",
            "page": top_page_data.get("page", 0)
        }
        yield f"data: {json.dumps(ocr_progress, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.01)

        b64_image = top_page_data["image_base64"]
        ocr_result = _call_glm_ocr(
            image_base64=b64_image,
            prompt=prompt,
            raw_mode=raw_mode
        )

        final_payload = {
            "type": "workflow_result",
            "page": top_page_data["page"],
            "similarity": top_page_data["similarity"],
            "image_base64": b64_image,
            "ocr_success": ocr_result.get("success", False),
            "ocr_data": ocr_result.get("data"),
            "ocr_raw": ocr_result.get("raw"),
            "ocr_error": ocr_result.get("error")
        }
        
        yield f"data: {json.dumps(final_payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
