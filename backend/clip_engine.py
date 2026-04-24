# -*- coding: utf-8 -*-
"""
CLIP 以圖搜頁引擎 — 使用 openai/clip-vit-large-patch14 比對 PDF 頁面與參考圖片
支援 image-image 相似度 + text-image 加權過濾、閾值篩選、Top-K 排序
"""
import base64
import gc
import io
from typing import Any, Dict, Generator, List

import torch

from backend.ocr_engine import _call_glm_ocr, pdf_pages_to_images, _pil_to_base64

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from PIL import Image
except ImportError:
    Image = None

# ── 全域 singleton ──
_clip_model = None
_clip_processor = None
_device = None


def load_clip_model():
    """懶載入 CLIP 模型 (singleton)，含離線保護"""
    global _clip_model, _clip_processor, _device

    if _clip_model is not None:
        return _clip_model, _clip_processor

    from transformers import CLIPModel, CLIPProcessor
    from backend.network_utils import is_offline_mode, is_model_cached, make_offline_error_message

    model_name = "openai/clip-vit-large-patch14"

    if is_offline_mode() and not is_model_cached(model_name):
        raise RuntimeError(make_offline_error_message(model_name))

    print(f"[CLIP] 正在載入模型 {model_name} ...")

    _device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        _clip_model = CLIPModel.from_pretrained(model_name).to(_device)
        _clip_model.eval()
        _clip_processor = CLIPProcessor.from_pretrained(model_name)
    except (OSError, ConnectionError, Exception) as e:
        err_str = str(e).lower()
        if any(kw in err_str for kw in ["connection", "proxy", "timeout", "resolve", "offline"]):
            raise RuntimeError(make_offline_error_message(model_name)) from e
        raise

    print(f"[CLIP] 模型已載入至 {_device}")
    return _clip_model, _clip_processor


def unload_clip_model():
    """釋放 CLIP 模型，回收 VRAM"""
    global _clip_model, _clip_processor, _device

    if _clip_model is not None:
        del _clip_model
        _clip_model = None
    if _clip_processor is not None:
        del _clip_processor
        _clip_processor = None
    _device = None

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    print("[CLIP] 模型已卸載，VRAM 已釋放")

# ── 核心比對 ──

def extract_image_feature(image_bytes: bytes) -> tuple[list[float], int]:
    """
    自給定圖片提取 CLIP 特徵向量。
    向量會作 L2 正規化，以供餘弦相似度直接運算。
    
    Args:
        image_bytes: 圖片的 bytes 內容
        
    Returns:
        (feature_vector, dimension)
    """
    if Image is None:
        raise RuntimeError("Pillow 未安裝，請執行 pip install Pillow")

    model, processor = load_clip_model()
    
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as e:
        raise ValueError(f"無法讀取圖片: {e}")

    with torch.no_grad():
        inputs = processor(images=[img], return_tensors="pt", padding=True).to(_device)
        features = model.get_image_features(**inputs)
        # 正規化
        features = features / features.norm(p=2, dim=-1, keepdim=True)
        
    vector = features.cpu().numpy().flatten().tolist()
    
    # 執行完畢釋放記憶體 (可選，但為了保留原先 `clip_engine` 隨用隨清的風格，這裡我們也執行 unload)
    # 如果系統需要高頻呼叫 API，您也可以把這行改成由 client 決定是否 unload
    unload_clip_model()
    
    return vector, len(vector)

def search_similar_pages(
    pdf_bytes: bytes,
    ref_images_bytes: List[bytes],
    must_include: str = "",
    must_exclude: str = "",
    threshold: float = 0.5,
    top_k: int = 5,
    dpi: int = 150,
) -> Generator[Dict[str, Any], None, None]:
    """
    以 generator 逐步回傳搜尋進度與結果。

    每次 yield:
      - 進度: {"type": "progress", "page": int, "total": int, "percent": float}
      - 結果: {"type": "results", "data": [...], "total_pages": int}
    """
    if Image is None:
        yield {"type": "error", "error": "Pillow 未安裝，請執行 pip install Pillow"}
        return

    # 1. 載入模型
    yield {"type": "progress", "page": 0, "total": 0, "percent": 0, "status": "載入 CLIP 模型中..."}
    model, processor = load_clip_model()

    # 2. PDF → images
    yield {"type": "progress", "page": 0, "total": 0, "percent": 5, "status": "解析 PDF 頁面中..."}
    try:
        pdf_images = pdf_pages_to_images(pdf_bytes, dpi=dpi)
    except Exception as e:
        yield {"type": "error", "error": f"PDF 解析失敗: {e}"}
        return

    total_pages = len(pdf_images)
    if total_pages == 0:
        yield {"type": "error", "error": "PDF 無頁面"}
        return

    # 3. 載入參考圖片
    ref_pil_images = []
    for img_bytes in ref_images_bytes:
        try:
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            ref_pil_images.append(img)
        except Exception as e:
            yield {"type": "error", "error": f"參考圖片讀取失敗: {e}"}
            return

    if len(ref_pil_images) == 0:
        yield {"type": "error", "error": "至少需要一張參考圖片"}
        return

    # 4. 計算參考圖片的特徵向量
    yield {"type": "progress", "page": 0, "total": total_pages, "percent": 10, "status": "提取參考圖片特徵..."}
    with torch.no_grad():
        ref_inputs = processor(images=ref_pil_images, return_tensors="pt", padding=True).to(_device)
        ref_features = model.get_image_features(**ref_inputs)
        ref_features = ref_features / ref_features.norm(p=2, dim=-1, keepdim=True)

    # 5. 只用圖片特徵比對逐頁計算相似度
    page_scores: List[Dict[str, Any]] = []

    # 取多一點候選頁以供 OCR 過濾
    # 如果有文字條件，多取一些；否則就照原本數量
    candidate_k = top_k * 3 if (must_include.strip() or must_exclude.strip()) else top_k

    for i, page_img in enumerate(pdf_images):
        progress_pct = 10 + ((i + 1) / total_pages) * 40  # 10% ~ 50%
        yield {
            "type": "progress",
            "page": i + 1,
            "total": total_pages,
            "percent": progress_pct,
            "status": f"比對圖片第 {i + 1}/{total_pages} 頁...",
        }

        with torch.no_grad():
            page_inputs = processor(images=[page_img], return_tensors="pt").to(_device)
            page_features = model.get_image_features(**page_inputs)
            page_features = page_features / page_features.norm(p=2, dim=-1, keepdim=True)

            # image-image 相似度 (取與所有參考圖的最大值)
            sim_matrix = (page_features @ ref_features.T)  # [1, num_refs]
            img_sim = sim_matrix.max().item()

        if img_sim >= threshold:
            page_scores.append({
                "page": i + 1,
                "similarity": float(img_sim),
                "image": page_img,  # 暫存原始圖片供後續 OCR 使用
            })

    # 排序並取候選 top M
    page_scores.sort(key=lambda x: x["similarity"], reverse=True)
    candidates = page_scores[:candidate_k]

    # 卸載 CLIP 模型以挪出 VRAM 供 OCR 使用
    unload_clip_model()

    # 6. OCR 文字過濾 (若有 must_include / must_exclude)
    final_results = []
    
    include_texts = [t.strip().lower() for t in must_include.split(",") if t.strip()]
    exclude_texts = [t.strip().lower() for t in must_exclude.split(",") if t.strip()]

    import os
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    # 這裡使用一個很泛用的 prompt 來把圖轉為文字，方便關鍵字比對
    ocr_prompt = "OCR"

    for idx, cand in enumerate(candidates):
        page_img = cand.pop("image")  # 取出圖片
        
        # 保持原尺寸高品質
        img_b64 = _pil_to_base64(page_img, "PNG")
        cand["image_base64"] = img_b64
        cand["similarity"] = round(cand["similarity"], 4)

        # 如果需要進行文字過濾，加上進度與 OCR
        if include_texts or exclude_texts:
            progress_pct = 50 + ((idx + 1) / len(candidates)) * 40  # 50% ~ 90%
            yield {
                "type": "progress",
                "page": cand["page"],
                "total": total_pages,
                "percent": progress_pct,
                "status": f"正在進行 OCR 文字辨識與過濾 (第 {idx + 1}/{len(candidates)} 個候選頁)...",
            }
            
            # 使用高解析度圖片呼叫 OCR
            ocr_result = _call_glm_ocr(img_b64, ocr_prompt, raw_mode=True)
            
            # 因為我們設定 raw_mode=True，原始回覆一定在 raw 中。
            page_text = ocr_result.get("raw", "").lower()

            # 判斷包含詞
            has_all_includes = all(t in page_text for t in include_texts) if include_texts else True
            # 判斷不可包含詞
            has_no_excludes = not any(t in page_text for t in exclude_texts) if exclude_texts else True

            if has_all_includes and has_no_excludes:
                final_results.append(cand)
            
            # 如果已經收集夠了，就提早終止 OCR 判斷
            if len(final_results) >= top_k:
                break
        else:
            final_results.append(cand)

    # 7. 整理最終結果，若是沒進入 OCR 提早終止，也必須截斷至 top_k
    final_results = final_results[:top_k]

    yield {
        "type": "progress",
        "page": total_pages,
        "total": total_pages,
        "percent": 100,
        "status": "處理完成",
    }

    yield {
        "type": "results",
        "data": final_results,
        "total_pages": total_pages,
        "matched_count": len(final_results),
    }
