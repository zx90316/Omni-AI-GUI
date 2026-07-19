# -*- coding: utf-8 -*-
"""OCR orchestration for images and PDFs.

This module owns file handling, task prompts, retries, post-processing and the
stable response contract.  Inference implementations live in
``backend.ocr_providers`` and the complete document pipeline comes from the
official ``glmocr`` PyPI package instead of a vendored source-tree copy.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, Iterator, List, Optional, Tuple

from PIL import Image

from backend.ocr_providers import (
    DEFAULT_MODEL,
    GenerationOptions,
    InProcessSDKClient,
    OCRProvider,
    OCRProviderError,
    SUPPORTED_PROVIDERS,
    get_ocr_provider,
    provider_statuses,
    retry_recognition,
    unload_ocr_models,
)

try:
    from opencc import OpenCC
except ImportError:  # pragma: no cover - dependency error is reported in status
    OpenCC = None

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - dependency error is reported on PDF use
    fitz = None


SUPPORTED_TASKS = ("document", "text", "table", "formula", "extract")
SUPPORTED_OUTPUT_FORMATS = ("both", "markdown", "json")
TASK_PROMPTS = {
    "text": "Text Recognition:",
    "table": "Table Recognition:",
    "formula": "Formula Recognition:",
}

_CORRECTION_MAP_PATH = Path(__file__).parent / "ocr_correction_map.json"
_correction_lock = threading.RLock()
_cc = OpenCC("s2t") if OpenCC is not None else None


def load_correction_map() -> Dict[str, str]:
    """Load the user-maintained OCR replacement map."""

    if not _CORRECTION_MAP_PATH.exists():
        return {}
    with _CORRECTION_MAP_PATH.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("OCR correction map must be a JSON object")
    return {str(key): str(replacement) for key, replacement in value.items()}


_correction_map: Dict[str, str] = load_correction_map()


def save_correction_map(mapping: Dict[str, str]) -> None:
    """Atomically persist the replacement map."""

    temporary = _CORRECTION_MAP_PATH.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(mapping, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, _CORRECTION_MAP_PATH)


def get_correction_map() -> Dict[str, str]:
    with _correction_lock:
        return dict(_correction_map)


def update_correction_map(new_map: Dict[str, str]) -> None:
    """Validate and update the in-memory and on-disk replacement map."""

    if not isinstance(new_map, dict):
        raise ValueError("修正字典必須是 JSON 物件")
    normalized: Dict[str, str] = {}
    for wrong, right in new_map.items():
        if not isinstance(wrong, str) or not isinstance(right, str):
            raise ValueError("修正字典的鍵和值都必須是字串")
        if not wrong:
            raise ValueError("修正字典不能包含空白鍵")
        normalized[wrong] = right
    global _correction_map
    with _correction_lock:
        save_correction_map(normalized)
        _correction_map = normalized


def postprocess_value(text: str) -> str:
    """Convert Simplified Chinese and apply the replacement map."""

    if not text or not text.strip():
        return ""
    result = text.strip()
    if _cc is not None:
        result = _cc.convert(result)
    with _correction_lock:
        for wrong, right in _correction_map.items():
            result = result.replace(wrong, right)
    return result


def postprocess_result(value: Any) -> Any:
    """Recursively post-process structured extraction results."""

    if isinstance(value, dict):
        return {
            postprocess_value(str(key)): postprocess_result(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [postprocess_result(child) for child in value]
    if isinstance(value, str):
        return postprocess_value(value)
    return value


def merge_page_results(all_page_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge extraction fields, clearing values that conflict across pages."""

    field_values: Dict[str, List[Any]] = OrderedDict()
    for page_data in all_page_data:
        if not isinstance(page_data, dict):
            continue
        for key, value in page_data.items():
            field_values.setdefault(key, [])
            if value not in (None, "", [], {}):
                field_values[key].append(value)

    merged: Dict[str, Any] = OrderedDict()
    for key, values in field_values.items():
        if not values:
            merged[key] = ""
            continue
        signatures = {
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            for value in values
        }
        merged[key] = values[0] if len(signatures) == 1 else ""
    return merged


def merge_document_results(results: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Build one exportable multi-page document result."""

    successful = [result for result in results if result.get("success")]
    markdown_pages = [
        result.get("markdown") or result.get("raw") or "" for result in successful
    ]
    json_pages = [result.get("json") for result in successful]
    return {
        "markdown": "\n\n---\n\n".join(page for page in markdown_pages if page),
        "json": json_pages,
        "page_count": len(successful),
    }


def pdf_pages_to_images(pdf_bytes: bytes, dpi: int = 200) -> List[Image.Image]:
    """Render all PDF pages; retained for CLIP workflow compatibility."""

    total, pages = _iter_file_pages(pdf_bytes, "document.pdf", dpi=dpi)
    return list(pages) if total else []


def pdf_to_images(pdf_bytes: bytes, dpi: int = 200) -> List[Image.Image]:
    """Backward-compatible alias."""

    return pdf_pages_to_images(pdf_bytes, dpi=dpi)


def _iter_file_pages(
    file_bytes: bytes,
    filename: str,
    *,
    dpi: int,
) -> Tuple[int, Iterator[Image.Image]]:
    """Return page count and a lazy iterator to cap PDF memory usage."""

    is_pdf = Path(filename).suffix.lower() == ".pdf"
    if not is_pdf:
        try:
            image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            image.load()
        except Exception as exc:
            raise ValueError(f"圖片解析失敗: {exc}") from exc
        return 1, iter((image,))

    if fitz is None:
        raise ImportError("PDF 處理需要 PyMuPDF，請重新安裝專案依賴。")
    try:
        document = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"PDF 解析失敗: {exc}") from exc
    total = len(document)

    def render() -> Iterator[Image.Image]:
        try:
            matrix = fitz.Matrix(dpi / 72, dpi / 72)
            for page in document:
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
                image.load()
                yield image
        finally:
            document.close()

    return total, render()


def _pil_to_base64(image: Image.Image, fmt: str = "PNG") -> str:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _detect_mime(filename: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(Path(filename).suffix.lower(), "image/png")


def build_ocr_prompt(
    fields: Optional[Dict[str, Any]] = None,
    task: str = "text",
) -> str:
    """Build one of the prompt forms officially supported by GLM-OCR."""

    if fields:
        schema = json.dumps(fields, ensure_ascii=False, indent=2)
        return f"请按下列JSON格式输出图中信息:\n{schema}"
    normalized = "text" if task in {"auto", "document", "extract"} else task
    if normalized not in TASK_PROMPTS:
        raise ValueError(f"不支援的 OCR 任務: {task}")
    return TASK_PROMPTS[normalized]


def _parse_json_response(text: str) -> Optional[Dict[str, Any]]:
    """Extract a JSON object from plain text or a fenced response."""

    candidates = [text.strip()]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:json)?\s*\n?(.*?)\n?\s*```",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
    )
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def _normalize_sdk_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


class _WholePageLayoutDetector:
    """Treat each page as one text region when layout analysis is disabled.

    glmocr 0.1.5 always runs its three-stage pipeline.  Supplying this tiny
    detector preserves the official formatter/pipeline contract without
    loading PP-DocLayoutV3 or its optional OpenCV/Torch dependencies.
    """

    batch_size = 1

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def process(
        self,
        images: List[Image.Image],
        *,
        save_visualization: bool = False,
        global_start_idx: int = 0,
        use_polygon: bool = False,
        **_: Any,
    ) -> Tuple[List[List[Dict[str, Any]]], Dict[int, Image.Image]]:
        del save_visualization, global_start_idx, use_polygon
        pages = [
            [
                {
                    "index": 0,
                    "label": "text",
                    "score": 1.0,
                    "bbox_2d": [0, 0, 1000, 1000],
                    "polygon": None,
                    "task_type": "text",
                }
            ]
            for _image in images
        ]
        return pages, {}


def _run_document_pipeline(
    image: Image.Image,
    provider: OCRProvider,
    options: GenerationOptions,
    *,
    enable_layout: bool,
    output_format: str,
) -> Tuple[Any, str]:
    """Run the official SDK pipeline with the in-process provider adapter."""

    try:
        from glmocr.config import load_config
        from glmocr.pipeline import Pipeline
    except ImportError as exc:
        raise OCRProviderError(
            "完整文件解析需要官方 glmocr 套件；請安裝 requirements-ocr.txt。"
        ) from exc

    try:
        config_model = load_config(
            mode="selfhosted",
            model=options.model,
            timeout=options.timeout,
            layout_device=os.getenv("GLMOCR_LAYOUT_DEVICE") or None,
        )
        pipeline_config = config_model.pipeline
        # glmocr <=0.1.1 exposed ``enable_layout``; 0.1.5 always runs the
        # layout stage and accepts a custom detector instead.
        if hasattr(pipeline_config, "enable_layout"):
            pipeline_config.enable_layout = enable_layout
        if hasattr(pipeline_config, "max_workers"):
            pipeline_config.max_workers = (
                1
                if provider.name == "local"
                else max(1, int(os.getenv("OCR_MAX_WORKERS", "8")))
            )
        if hasattr(pipeline_config, "result_formatter"):
            pipeline_config.result_formatter.output_format = output_format

        layout_detector = None if enable_layout else _WholePageLayoutDetector()
        pipeline = Pipeline(config=pipeline_config, layout_detector=layout_detector)
        pipeline.ocr_client = InProcessSDKClient(provider, options)
        pipeline.start()
        try:
            request_data = {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": TASK_PROMPTS["text"]},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{_pil_to_base64(image)}"
                                },
                            },
                        ],
                    }
                ]
            }
            parsed_results = list(pipeline.process(request_data))
        finally:
            pipeline.stop()
    except OCRProviderError:
        raise
    except Exception as exc:
        hint = (
            "；版面模式需要 glmocr[selfhosted] 與 PP-DocLayoutV3 模型"
            if enable_layout
            else ""
        )
        raise OCRProviderError(f"GLM-OCR 文件管線失敗: {exc}{hint}") from exc

    if not parsed_results:
        raise OCRProviderError("GLM-OCR 文件管線沒有產生結果")
    result = parsed_results[0]
    return _normalize_sdk_json(result.json_result), result.markdown_result or ""


def recognize_image(
    image: Image.Image,
    *,
    fields: Optional[Dict[str, Any]] = None,
    task: str = "text",
    provider_name: str = "local",
    model: str = DEFAULT_MODEL,
    max_retries: int = 3,
    enable_layout: bool = False,
    output_format: str = "both",
    max_tokens: int = 8192,
) -> Dict[str, Any]:
    """Recognize one image and return the stable application result schema."""

    started = time.perf_counter()
    normalized_task = "extract" if fields else task
    if normalized_task == "auto":
        normalized_task = "extract" if fields else "text"
    if normalized_task not in SUPPORTED_TASKS:
        return _error_result(
            f"不支援的 OCR 任務: {normalized_task}", provider_name, normalized_task
        )
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        return _error_result(
            f"不支援的輸出格式: {output_format}", provider_name, normalized_task
        )

    try:
        provider = get_ocr_provider(provider_name)
        options = GenerationOptions(model=model or DEFAULT_MODEL, max_tokens=max_tokens)

        if normalized_task == "document":
            last_error: Optional[Exception] = None
            attempts = max(1, min(int(max_retries), 5))
            for attempt in range(attempts):
                try:
                    json_result, markdown_result = _run_document_pipeline(
                        image,
                        provider,
                        options,
                        enable_layout=enable_layout,
                        output_format=output_format,
                    )
                    processed_json = postprocess_result(json_result)
                    processed_markdown = postprocess_value(markdown_result)
                    return {
                        "success": True,
                        "data": processed_json,
                        "json": processed_json,
                        "markdown": processed_markdown,
                        "raw": processed_markdown,
                        "error": None,
                        "provider": provider.name,
                        "task": normalized_task,
                        "model": options.model,
                        "layout": enable_layout,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                except OCRProviderError as exc:
                    last_error = exc
                    if not exc.retryable or attempt == attempts - 1:
                        raise
                    time.sleep(min(0.5 * (2**attempt), 4.0))
            raise last_error or OCRProviderError("GLM-OCR 文件解析失敗")

        prompt = build_ocr_prompt(fields, normalized_task)
        response = retry_recognition(
            provider,
            image,
            prompt,
            options,
            max_attempts=max_retries,
        )
        parsed: Optional[Dict[str, Any]] = None
        parse_error: Optional[str] = None
        if normalized_task == "extract":
            parsed = _parse_json_response(response)
            if parsed is None:
                parse_error = "模型未依指定 schema 回傳有效 JSON"
            else:
                parsed = postprocess_result(parsed)
        processed_text = postprocess_value(response)
        return {
            "success": parsed is not None if normalized_task == "extract" else True,
            "data": parsed,
            "json": parsed,
            "markdown": processed_text,
            "raw": processed_text,
            "error": parse_error,
            "provider": provider.name,
            "task": normalized_task,
            "model": options.model,
            "layout": False,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except (OCRProviderError, ValueError) as exc:
        result = _error_result(str(exc), provider_name, normalized_task)
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return result


def _error_result(error: str, provider: str, task: str) -> Dict[str, Any]:
    return {
        "success": False,
        "data": None,
        "json": None,
        "markdown": "",
        "raw": "",
        "error": error,
        "provider": provider,
        "task": task,
        "layout": False,
    }


def _call_glm_ocr(
    image_base64: str,
    prompt: str,
    raw_mode: bool = False,
    *,
    provider: str = "local",
    model: str = DEFAULT_MODEL,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Backward-compatible entrypoint used by the CLIP workflows."""

    try:
        image = Image.open(io.BytesIO(base64.b64decode(image_base64))).convert("RGB")
        image.load()
    except Exception as exc:
        return _error_result(f"圖片解碼失敗: {exc}", provider, "text")
    fields = None if raw_mode else _parse_json_schema_from_prompt(prompt)
    task = "extract" if fields else "text"
    if not fields and prompt in TASK_PROMPTS.values():
        task = next(key for key, value in TASK_PROMPTS.items() if value == prompt)
    if fields:
        return recognize_image(
            image,
            fields=fields,
            task="extract",
            provider_name=provider,
            model=model,
            max_retries=max_retries,
        )

    # Preserve custom prompts used by older callers while still using the new
    # provider layer.
    try:
        selected_provider = get_ocr_provider(provider)
        options = GenerationOptions(model=model or DEFAULT_MODEL)
        response = retry_recognition(
            selected_provider,
            image,
            TASK_PROMPTS.get(task, prompt or TASK_PROMPTS["text"]),
            options,
            max_attempts=max_retries,
        )
        processed = postprocess_value(response)
        return {
            "success": True,
            "data": None,
            "json": None,
            "markdown": processed,
            "raw": processed,
            "error": None,
            "provider": selected_provider.name,
            "task": task,
            "model": options.model,
            "layout": False,
        }
    except OCRProviderError as exc:
        return _error_result(str(exc), provider, task)


def _parse_json_schema_from_prompt(prompt: str) -> Optional[Dict[str, Any]]:
    if "JSON" not in prompt.upper():
        return None
    return _parse_json_response(prompt)


def process_file_stream(
    file_bytes: bytes,
    filename: str,
    fields: Optional[Dict[str, Any]] = None,
    ollama_host: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    max_retries: int = 3,
    *,
    provider: str = "local",
    task: str = "auto",
    enable_layout: bool = False,
    output_format: str = "both",
    dpi: int = 200,
) -> Generator[Dict[str, Any], None, None]:
    """Process an image/PDF lazily and yield page progress for SSE clients.

    ``ollama_host`` is accepted only for old internal callers.  Endpoint
    selection now belongs to the provider configuration and environment.
    """

    del ollama_host
    normalized_task = "extract" if fields else ("text" if task == "auto" else task)
    try:
        total, pages = _iter_file_pages(file_bytes, filename, dpi=dpi)
    except (ImportError, ValueError) as exc:
        yield {
            "page": 0,
            "total": 0,
            "percent": 0,
            "result": None,
            "done": True,
            "error": str(exc),
        }
        return

    if total == 0:
        yield {
            "page": 0,
            "total": 0,
            "percent": 100,
            "result": None,
            "done": True,
            "error": "PDF 無頁面",
        }
        return

    for index, image in enumerate(pages, start=1):
        result = recognize_image(
            image,
            fields=fields,
            task=normalized_task,
            provider_name=provider,
            model=model,
            max_retries=max_retries,
            enable_layout=enable_layout,
            output_format=output_format,
        )
        yield {
            "page": index,
            "total": total,
            "percent": (index / total) * 100,
            "result": result,
            "done": index == total,
        }


def get_ocr_capabilities() -> Dict[str, Any]:
    """Return a UI-friendly summary of supported GLM-OCR functionality."""

    glmocr_installed = False
    glmocr_version: Optional[str] = None
    try:
        import glmocr

        glmocr_installed = True
        glmocr_version = getattr(glmocr, "__version__", None)
    except ImportError:
        pass
    return {
        "default_provider": os.getenv("OCR_PROVIDER", "local"),
        "default_model": os.getenv("OCR_MODEL", DEFAULT_MODEL),
        "providers": provider_statuses(),
        "tasks": [
            {"id": "document", "label": "完整文件解析", "layout": True},
            {"id": "text", "label": "文字辨識", "prompt": TASK_PROMPTS["text"]},
            {"id": "table", "label": "表格辨識", "prompt": TASK_PROMPTS["table"]},
            {"id": "formula", "label": "公式辨識", "prompt": TASK_PROMPTS["formula"]},
            {"id": "extract", "label": "JSON 欄位萃取", "schema": True},
        ],
        "input_formats": ["png", "jpg", "jpeg", "bmp", "tiff", "webp", "gif", "pdf"],
        "output_formats": list(SUPPORTED_OUTPUT_FORMATS),
        "glmocr": {"installed": glmocr_installed, "version": glmocr_version},
        "features": {
            "pdf": fitz is not None,
            "multi_page": True,
            "streaming_progress": True,
            "layout_analysis": glmocr_installed,
            "structured_json": True,
            "markdown": True,
            "correction_map": True,
            "model_unload": True,
        },
    }


__all__ = [
    "DEFAULT_MODEL",
    "SUPPORTED_OUTPUT_FORMATS",
    "SUPPORTED_PROVIDERS",
    "SUPPORTED_TASKS",
    "TASK_PROMPTS",
    "_call_glm_ocr",
    "_parse_json_response",
    "_pil_to_base64",
    "build_ocr_prompt",
    "get_correction_map",
    "get_ocr_capabilities",
    "merge_document_results",
    "merge_page_results",
    "pdf_pages_to_images",
    "postprocess_result",
    "process_file_stream",
    "recognize_image",
    "unload_ocr_models",
    "update_correction_map",
]
