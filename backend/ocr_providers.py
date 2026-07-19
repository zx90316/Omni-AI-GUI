"""GLM-OCR inference providers.

The application owns this small adapter layer.  Model serving is deliberately
kept separate from the official ``glmocr`` document-pipeline package so users
can choose in-process Transformers inference, an OpenAI-compatible server, or
the legacy Ollama endpoint without changing the OCR workflow code.
"""

from __future__ import annotations

import base64
import gc
import importlib.util
import io
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

from PIL import Image

from backend.model_registry import MODEL_IDS


DEFAULT_MODEL = MODEL_IDS["glm_ocr"]
SUPPORTED_PROVIDERS = ("local", "openai", "ollama")


class OCRProviderError(RuntimeError):
    """Provider failure with a hint about whether retrying may help."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class GenerationOptions:
    model: str = DEFAULT_MODEL
    max_tokens: int = 8192
    temperature: float = 0.0
    top_p: float = 0.00001
    timeout: int = 300


class OCRProvider(ABC):
    name: str

    @abstractmethod
    def recognize(
        self,
        image: Image.Image,
        prompt: str,
        options: GenerationOptions,
    ) -> str:
        """Recognize one image and return the model's text response."""

    def unload(self) -> bool:
        return False

    def status(self) -> Dict[str, Any]:
        return {"name": self.name, "available": True}


def _image_data_uri(image: Image.Image, image_format: str = "PNG") -> str:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format=image_format)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/{image_format.lower()};base64,{encoded}"


class TransformersOCRProvider(OCRProvider):
    """Run GLM-OCR directly in the backend process with Transformers."""

    name = "local"

    def __init__(self) -> None:
        self._processor = None
        self._model = None
        self._loaded_model_name: Optional[str] = None
        self._load_lock = threading.RLock()
        # ``generate`` and model unload must not race.  A single in-process
        # model is intentionally serialized; vLLM/SGLang should be used when
        # concurrent GPU batching is required.
        self._inference_lock = threading.RLock()

    @staticmethod
    def _normalized_model_name(model: str) -> str:
        if not model or model in {"glm-ocr", "glm-ocr:latest"}:
            return DEFAULT_MODEL
        return model

    def _load(self, model_name: str) -> None:
        model_name = self._normalized_model_name(model_name)
        with self._load_lock:
            if self._model is not None and self._loaded_model_name == model_name:
                return
            if self._model is not None:
                self.unload()
            from backend.network_utils import (
                is_model_cached,
                is_offline_mode,
                make_offline_error_message,
            )

            if is_offline_mode() and not is_model_cached(model_name):
                raise OCRProviderError(make_offline_error_message(model_name))
            try:
                from transformers import AutoModelForImageTextToText, AutoProcessor
            except (ImportError, AttributeError) as exc:
                raise OCRProviderError(
                    "本機 OCR 需要 transformers>=5.3.0；請重新安裝 OCR 依賴。"
                ) from exc

            kwargs: Dict[str, Any] = {"torch_dtype": "auto"}
            device = os.getenv("OCR_DEVICE", "auto").strip().lower()
            if device == "auto":
                kwargs["device_map"] = "auto"
            try:
                self._processor = AutoProcessor.from_pretrained(model_name)
                self._model = AutoModelForImageTextToText.from_pretrained(
                    model_name,
                    **kwargs,
                )
                if device not in {"", "auto"}:
                    self._model = self._model.to(device)
                self._model.eval()
                self._loaded_model_name = model_name
            except Exception as exc:
                self._processor = None
                self._model = None
                self._loaded_model_name = None
                raise OCRProviderError(
                    f"無法載入本機 GLM-OCR 模型 {model_name}: {exc}"
                ) from exc

    def recognize(
        self,
        image: Image.Image,
        prompt: str,
        options: GenerationOptions,
    ) -> str:
        self._load(options.model)
        data_uri = _image_data_uri(image)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "url": data_uri},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        with self._inference_lock:
            try:
                inputs = self._processor.apply_chat_template(
                    messages,
                    tokenize=True,
                    add_generation_prompt=True,
                    return_dict=True,
                    return_tensors="pt",
                ).to(self._model.device)
                inputs.pop("token_type_ids", None)
                generation_kwargs: Dict[str, Any] = {
                    "max_new_tokens": options.max_tokens,
                    "do_sample": options.temperature > 0,
                }
                if options.temperature > 0:
                    generation_kwargs.update(
                        temperature=options.temperature,
                        top_p=options.top_p,
                    )
                generated_ids = self._model.generate(**inputs, **generation_kwargs)
                prompt_length = inputs["input_ids"].shape[1]
                text = self._processor.decode(
                    generated_ids[0][prompt_length:],
                    skip_special_tokens=True,
                )
                return text.strip()
            except Exception as exc:
                raise OCRProviderError(f"本機 GLM-OCR 推論失敗: {exc}") from exc

    def unload(self) -> bool:
        with self._inference_lock, self._load_lock:
            had_model = self._model is not None
            self._model = None
            self._processor = None
            self._loaded_model_name = None
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
            return had_model

    def status(self) -> Dict[str, Any]:
        transformers_present = importlib.util.find_spec("transformers") is not None
        return {
            "name": self.name,
            "available": transformers_present,
            "loaded": self._model is not None,
            "model": self._loaded_model_name,
            "description": "專案內直接使用 Transformers 推論，不需要 Ollama",
        }


class HTTPOCRProvider(OCRProvider):
    """OpenAI-compatible or Ollama-native remote inference provider."""

    def __init__(self, mode: str) -> None:
        if mode not in {"openai", "ollama"}:
            raise ValueError(f"Unsupported HTTP OCR mode: {mode}")
        try:
            import requests
        except ImportError as exc:
            raise OCRProviderError(
                "遠端 OCR provider 需要 requests，請重新安裝專案依賴。"
            ) from exc
        self.name = mode
        self._requests = requests
        self._session = requests.Session()

    def _settings(self) -> tuple[str, Optional[str]]:
        if self.name == "ollama":
            base = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
            return os.getenv("OCR_OLLAMA_URL", f"{base}/api/generate"), None
        return (
            os.getenv("OCR_API_URL", "http://127.0.0.1:8080/v1/chat/completions"),
            os.getenv("OCR_API_KEY") or None,
        )

    def recognize(
        self,
        image: Image.Image,
        prompt: str,
        options: GenerationOptions,
    ) -> str:
        url, api_key = self._settings()
        data_uri = _image_data_uri(image)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        if self.name == "ollama":
            image_b64 = data_uri.split(",", 1)[1]
            payload = {
                "model": options.model if options.model != DEFAULT_MODEL else "glm-ocr:latest",
                "prompt": prompt,
                "images": [image_b64],
                "stream": False,
                "options": {
                    "num_predict": options.max_tokens,
                    "temperature": options.temperature,
                    "top_p": options.top_p,
                },
            }
        else:
            payload = {
                "model": options.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
                "max_tokens": options.max_tokens,
                "temperature": options.temperature,
                "top_p": options.top_p,
            }
        try:
            response = self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=options.timeout,
            )
        except self._requests.RequestException as exc:
            raise OCRProviderError(
                f"無法連線至 {self.name} OCR 服務 {url}: {exc}",
                retryable=True,
            ) from exc

        if response.status_code != 200:
            preview = response.text[:500]
            raise OCRProviderError(
                f"{self.name} OCR 回傳 HTTP {response.status_code}: {preview}",
                retryable=response.status_code in {408, 429, 500, 502, 503, 504},
            )
        try:
            body = response.json()
            if self.name == "ollama":
                output = body["response"]
            else:
                output = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise OCRProviderError(f"{self.name} OCR 回覆格式錯誤: {exc}") from exc
        return str(output).strip()

    def status(self) -> Dict[str, Any]:
        url, _ = self._settings()
        return {
            "name": self.name,
            "available": True,
            "endpoint": url,
            "description": (
                "vLLM/SGLang 等 OpenAI 相容服務"
                if self.name == "openai"
                else "舊版 Ollama 相容模式（非必要依賴）"
            ),
        }


_PROVIDERS: Dict[str, OCRProvider] = {}
_PROVIDERS_LOCK = threading.Lock()


def get_ocr_provider(name: str) -> OCRProvider:
    normalized = (name or "local").strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise OCRProviderError(
            f"不支援的 OCR provider: {name}；可用值為 {', '.join(SUPPORTED_PROVIDERS)}"
        )
    with _PROVIDERS_LOCK:
        if normalized not in _PROVIDERS:
            _PROVIDERS[normalized] = (
                TransformersOCRProvider()
                if normalized == "local"
                else HTTPOCRProvider(normalized)
            )
        return _PROVIDERS[normalized]


def provider_statuses() -> list[Dict[str, Any]]:
    statuses = []
    for name in SUPPORTED_PROVIDERS:
        try:
            statuses.append(get_ocr_provider(name).status())
        except OCRProviderError as exc:
            statuses.append({"name": name, "available": False, "error": str(exc)})
    return statuses


def unload_ocr_models() -> bool:
    provider = get_ocr_provider("local")
    return provider.unload()


class InProcessSDKClient:
    """Adapter that lets the official glmocr pipeline call an OCRProvider.

    The official SDK's pipeline expects an OpenAI-shaped ``OCRClient``.  This
    adapter supplies that small interface and keeps recognition in the current
    project process, avoiding a loopback HTTP server or Ollama dependency.
    """

    def __init__(self, provider: OCRProvider, options: GenerationOptions):
        self.provider = provider
        self.options = options

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def is_alive(self, timeout: float = 5.0) -> bool:
        return True

    @staticmethod
    def _decode_image(value: Any) -> Image.Image:
        if isinstance(value, dict):
            value = value.get("url", "")
        if not isinstance(value, str) or not value.startswith("data:"):
            raise OCRProviderError("glmocr SDK 傳入了不支援的圖片格式")
        try:
            payload = value.split(",", 1)[1]
            return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")
        except Exception as exc:
            raise OCRProviderError(f"無法解碼 glmocr SDK 圖片: {exc}") from exc

    def process(self, request_data: Dict[str, Any]) -> tuple[Dict[str, Any], int]:
        prompt = "Text Recognition:"
        image_value: Any = None
        for message in request_data.get("messages", []):
            if message.get("role") != "user":
                continue
            content = message.get("content", [])
            if isinstance(content, str):
                prompt = content or prompt
                continue
            for item in content:
                if item.get("type") == "text" and item.get("text"):
                    prompt = item["text"]
                elif item.get("type") in {"image", "image_url"}:
                    image_value = item.get("url", item.get("image_url"))
        if image_value is None:
            return {"error": "OCR request did not contain an image"}, 400
        try:
            image = self._decode_image(image_value)
            output = self.provider.recognize(image, prompt, self.options)
            return {"choices": [{"message": {"content": output}}]}, 200
        except OCRProviderError as exc:
            return {"error": str(exc)}, 503 if exc.retryable else 500


def retry_recognition(
    provider: OCRProvider,
    image: Image.Image,
    prompt: str,
    options: GenerationOptions,
    max_attempts: int,
) -> str:
    """Call a provider with bounded exponential backoff."""

    attempts = max(1, min(int(max_attempts), 5))
    for attempt in range(attempts):
        try:
            return provider.recognize(image, prompt, options)
        except OCRProviderError as exc:
            if not exc.retryable or attempt == attempts - 1:
                raise
            time.sleep(min(0.5 * (2**attempt), 4.0))
    raise AssertionError("unreachable")
