# -*- coding: utf-8 -*-
"""Schema, defaults and validation for the project ``.env`` file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import secrets
from typing import Mapping

from manager.config import PROJECT_ROOT


@dataclass(frozen=True)
class EnvField:
    key: str
    label: str
    group: str
    description: str
    required: bool = False
    default: str = ""
    example: str = ""
    choices: tuple[str, ...] = ()
    editable_choices: bool = False
    secret: bool = False


@dataclass(frozen=True)
class EnvValidationResult:
    valid: bool
    errors: tuple[str, ...]
    missing_keys: tuple[str, ...]

    @property
    def summary(self) -> str:
        if self.valid:
            return ".env 設定完整"
        if self.missing_keys:
            return f"缺少必要設定：{', '.join(self.missing_keys)}"
        return self.errors[0] if self.errors else ".env 設定不完整"


ENV_FIELDS = (
    EnvField(
        "OCR_PROVIDER", "OCR 執行方式", "OCR",
        "選擇專案內本機推論或外部相容服務。",
        required=True, default="local", example="local",
        choices=("local", "openai", "ollama"),
    ),
    EnvField(
        "OCR_MODEL", "OCR 模型", "OCR",
        "本機 Hugging Face model ID，或外部服務提供的模型名稱。",
        required=True, default="zai-org/GLM-OCR", example="zai-org/GLM-OCR",
        choices=("zai-org/GLM-OCR",), editable_choices=True,
    ),
    EnvField(
        "OCR_DEVICE", "OCR 裝置", "OCR",
        "auto 會自動選擇；多 GPU 可輸入 cuda:1 等裝置。",
        required=True, default="auto", example="cuda:0",
        choices=("auto", "cpu", "cuda", "cuda:0"), editable_choices=True,
    ),
    EnvField(
        "GLMOCR_LAYOUT_DEVICE", "版面模型裝置", "OCR",
        "可留空沿用自動選擇，或指定 CPU/GPU。",
        example="cuda:0", choices=("", "cpu", "cuda", "cuda:0"),
        editable_choices=True,
    ),
    EnvField(
        "OCR_API_URL", "OCR API URL", "OCR",
        "使用 openai provider 時必填。",
        example="http://127.0.0.1:8080/v1/chat/completions",
    ),
    EnvField(
        "OCR_API_KEY", "OCR API Key", "OCR",
        "外部 OCR 服務需要驗證時填寫。",
        example="sk-your-ocr-service-key", secret=True,
    ),
    EnvField(
        "OCR_MAX_WORKERS", "OCR Worker 數", "OCR",
        "外部 OCR 的最大並行工作數，允許 1–128。",
        required=True, default="8", example="8",
        choices=("1", "2", "4", "8", "16"), editable_choices=True,
    ),
    EnvField(
        "OLLAMA_HOST", "Ollama 位址", "OCR",
        "使用 ollama provider 時使用的服務位址。",
        default="http://localhost:11434",
        example="http://localhost:11434",
    ),
    EnvField(
        "HF_TOKEN", "Hugging Face Token", "ASR",
        "下載或使用 gated 的 Pyannote 語者分離模型時需要。",
        example="hf_xxxxxxxxxxxxxxxxxxxx", secret=True,
    ),
    EnvField(
        "ASR_MAX_NEW_TOKENS", "ASR 最大 Tokens", "ASR",
        "單段 ASR 生成上限，允許 64–8192。",
        required=True, default="512", example="512",
        choices=("256", "512", "1024", "2048"), editable_choices=True,
    ),
    EnvField(
        "ASR_ATTN_IMPLEMENTATION", "Attention 實作", "ASR",
        "留空使用 Transformers 預設；FlashAttention 需先安裝相容套件。",
        example="flash_attention_2",
        choices=("", "eager", "sdpa", "flash_attention_2"),
    ),
    EnvField(
        "ASR_MAX_UPLOAD_MB", "ASR 上傳上限 (MB)", "ASR",
        "允許 1–102400 MB。",
        required=True, default="2048", example="2048",
        choices=("512", "1024", "2048", "4096"), editable_choices=True,
    ),
    EnvField(
        "ASR_TASK_TIMEOUT_SECONDS", "ASR 逾時秒數", "ASR",
        "長音訊工作逾時，允許 60–604800 秒。",
        required=True, default="14400", example="14400",
        choices=("3600", "7200", "14400", "28800"), editable_choices=True,
    ),
    EnvField(
        "SECRET_KEY", "系統簽章金鑰", "認證與郵件",
        "JWT 簽章使用；空白時編輯器會自動產生安全隨機值。",
        required=True, example="至少 32 字元的隨機字串", secret=True,
    ),
    EnvField(
        "SMTP_HOST", "SMTP 主機", "認證與郵件",
        "寄送登入驗證碼的 SMTP 伺服器。",
        required=True, default="smtp.gmail.com", example="smtp.gmail.com",
    ),
    EnvField(
        "SMTP_PORT", "SMTP Port", "認證與郵件",
        "Gmail SSL 通常使用 465，STARTTLS 通常使用 587。",
        required=True, default="465", example="465",
        choices=("465", "587"),
    ),
    EnvField(
        "SMTP_USER", "SMTP 帳號", "認證與郵件",
        "寄件服務登入帳號。",
        required=True, example="your-email@gmail.com",
    ),
    EnvField(
        "SMTP_PASSWORD", "SMTP 密碼", "認證與郵件",
        "Gmail 請使用應用程式密碼，不要使用一般登入密碼。",
        required=True, example="xxxx xxxx xxxx xxxx", secret=True,
    ),
    EnvField(
        "SMTP_FROM_EMAIL", "寄件者信箱", "認證與郵件",
        "驗證信顯示的寄件者；通常與 SMTP 帳號相同。",
        required=True, example="noreply@example.com",
    ),
)

ENV_FIELDS_BY_KEY = {field.key: field for field in ENV_FIELDS}
DEPRECATED_ENV_KEYS = ("OPENAI_API_KEY", "GEMINI_API_KEY")

_INTEGER_RANGES = {
    "OCR_MAX_WORKERS": (1, 128),
    "ASR_MAX_NEW_TOKENS": (64, 8192),
    "ASR_MAX_UPLOAD_MB": (1, 102400),
    "ASR_TASK_TIMEOUT_SECONDS": (60, 604800),
    "SMTP_PORT": (1, 65535),
}
_PLACEHOLDER_VALUES = {
    "your_super_secret_jwt_key_here",
    "your-email@gmail.com",
    "your-google-app-password",
    "noreply@your-domain.com",
}


def _clean(value) -> str:
    return "" if value is None else str(value).strip()


def initial_env_values(existing: Mapping[str, object] | None = None) -> dict[str, str]:
    """Return form values, filling safe defaults without mutating ``.env``."""
    source = existing or {}
    values: dict[str, str] = {}
    for field in ENV_FIELDS:
        current = _clean(source.get(field.key))
        values[field.key] = current or field.default

    if not values["SECRET_KEY"]:
        values["SECRET_KEY"] = secrets.token_urlsafe(48)
    if not values["SMTP_FROM_EMAIL"] and "@" in values["SMTP_USER"]:
        values["SMTP_FROM_EMAIL"] = values["SMTP_USER"]
    return values


def validate_env_values(values: Mapping[str, object]) -> EnvValidationResult:
    """Validate explicit values. Defaults are intentionally not applied here."""
    cleaned = {key: _clean(value) for key, value in values.items()}
    errors: list[str] = []
    missing: list[str] = []

    for field in ENV_FIELDS:
        value = cleaned.get(field.key, "")
        if field.required and not value:
            missing.append(field.key)
            continue
        if value and field.choices and not field.editable_choices and value not in field.choices:
            errors.append(f"{field.key} 必須是：{', '.join(v or '(空白)' for v in field.choices)}")

    provider = cleaned.get("OCR_PROVIDER", "")
    if provider == "openai" and not cleaned.get("OCR_API_URL"):
        missing.append("OCR_API_URL")
    if provider == "ollama" and not cleaned.get("OLLAMA_HOST"):
        missing.append("OLLAMA_HOST")

    for key, (minimum, maximum) in _INTEGER_RANGES.items():
        value = cleaned.get(key, "")
        if not value:
            continue
        try:
            number = int(value)
        except ValueError:
            errors.append(f"{key} 必須是整數")
            continue
        if not minimum <= number <= maximum:
            errors.append(f"{key} 必須介於 {minimum}–{maximum}")

    secret_key = cleaned.get("SECRET_KEY", "")
    if secret_key and (
        len(secret_key) < 32 or secret_key.lower() in _PLACEHOLDER_VALUES
    ):
        errors.append("SECRET_KEY 必須是至少 32 字元且不可使用範例值")

    for key in ("SMTP_USER", "SMTP_FROM_EMAIL"):
        value = cleaned.get(key, "")
        if value and (
            value.lower() in _PLACEHOLDER_VALUES
            or re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) is None
        ):
            errors.append(f"{key} 必須是有效且非範例的電子郵件地址")
    if cleaned.get("SMTP_PASSWORD", "").lower() in _PLACEHOLDER_VALUES:
        errors.append("SMTP_PASSWORD 不可使用範例值")

    missing = list(dict.fromkeys(missing))
    return EnvValidationResult(not missing and not errors, tuple(errors), tuple(missing))


def read_env_values(env_path: Path | None = None) -> dict[str, str]:
    path = env_path or (PROJECT_ROOT / ".env")
    if not path.is_file():
        return {}
    try:
        from dotenv import dotenv_values

        return {
            key: _clean(value)
            for key, value in dotenv_values(path).items()
            if key
        }
    except ImportError:
        # Keep startup validation functional even before Manager dependencies
        # are repaired. The editor itself still uses python-dotenv for writes.
        values: dict[str, str] = {}
        try:
            for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if key:
                    values[key] = value
        except (OSError, UnicodeError):
            return {}
        return values
    except (OSError, ValueError):
        return {}


def validate_env_file(env_path: Path | None = None) -> EnvValidationResult:
    path = env_path or (PROJECT_ROOT / ".env")
    if not path.is_file():
        return EnvValidationResult(False, ("找不到 .env 檔案",), tuple(
            field.key for field in ENV_FIELDS if field.required
        ))
    return validate_env_values(read_env_values(path))
