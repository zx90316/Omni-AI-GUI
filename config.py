# -*- coding: utf-8 -*-
"""
Qwen-ASR-GUI 配置管理
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 載入 .env 檔案
load_dotenv()

# HuggingFace Token（從 .env 或環境變數讀取）
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# 路徑配置
BASE_DIR = Path(__file__).parent
RESULT_DIR = BASE_DIR / "results"
RESULT_DIR.mkdir(exist_ok=True)

# 音訊處理
AUDIO_SAMPLE_RATE = 16000

# FFmpeg 路徑（yt-dlp 音頻轉檔用）
FFMPEG_DIR = str(BASE_DIR / "ffmpeg-master-latest-win64-gpl-shared" / "bin")

# ASR 模型配置
MODELS = {
    "1.7B (高品質)": "Qwen/Qwen3-ASR-1.7B",
    "0.6B (輕量)": "Qwen/Qwen3-ASR-0.6B",
}
DEFAULT_MODEL = "1.7B (高品質)"
FORCED_ALIGNER = "Qwen/Qwen3-ForcedAligner-0.6B"

# 語者分離模型
DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"

# 語言選項
LANGUAGES = {
    "中文": "Chinese",
    "英文": "English",
    "日文": "Japanese",
    "韓文": "Korean",
    "德文": "German",
    "法文": "French",
}
DEFAULT_LANGUAGE = "中文"
