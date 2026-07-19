# -*- coding: utf-8 -*-
"""
Omni AI 配置管理
"""
import os
from pathlib import Path
from dotenv import load_dotenv

from backend.model_registry import MODEL_IDS


def _positive_int_env(name: str, default: int, minimum: int = 1) -> int:
    """Read a positive integer without making application import fragile."""
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default

# 載入 .env 檔案
load_dotenv()

# HuggingFace Token（從 .env 或環境變數讀取）
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# 路徑配置（此檔案位於 backend/，BASE_DIR 指向專案根目錄）
BASE_DIR = Path(__file__).parent.parent
RESULT_DIR = BASE_DIR / "results"
RESULT_DIR.mkdir(exist_ok=True)

# 音訊處理
AUDIO_SAMPLE_RATE = 16000
ASR_MAX_UPLOAD_MB = _positive_int_env("ASR_MAX_UPLOAD_MB", 2048)
ASR_TASK_TIMEOUT_SECONDS = _positive_int_env("ASR_TASK_TIMEOUT_SECONDS", 4 * 60 * 60, 60)

# FFmpeg 路徑（yt-dlp 音頻轉檔用）
FFMPEG_DIR = str(BASE_DIR / "ffmpeg-master-latest-win64-gpl-shared" / "bin")

# ASR 模型配置
MODELS = {
    "1.7B (高品質)": MODEL_IDS["asr_1.7b"],
    "0.6B (輕量)": MODEL_IDS["asr_0.6b"],
}
DEFAULT_MODEL = "1.7B (高品質)"
FORCED_ALIGNER = MODEL_IDS["forced_aligner"]

# 語者分離模型
DIARIZATION_MODEL = MODEL_IDS["diarization"]

# 語言選項
LANGUAGES = {
    "中文": "Chinese",
    "自動偵測": None,
    "英文": "English",
    "粵語": "Cantonese",
    "日文": "Japanese",
    "韓文": "Korean",
    "德文": "German",
    "法文": "French",
    "西班牙文": "Spanish",
    "葡萄牙文": "Portuguese",
    "義大利文": "Italian",
    "俄文": "Russian",
}
DEFAULT_LANGUAGE = "中文"
