# -*- coding: utf-8 -*-
"""
網路偵測與 HuggingFace 離線模式工具

提供：
- HuggingFace Hub 可達性檢測
- 離線模式環境變數設定
- 模型本地快取狀態查詢
"""
import logging
import os
import socket
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

REQUIRED_MODELS = {
    "asr_1.7b": "Qwen/Qwen3-ASR-1.7B",
    "asr_0.6b": "Qwen/Qwen3-ASR-0.6B",
    "forced_aligner": "Qwen/Qwen3-ForcedAligner-0.6B",
    "diarization": "pyannote/speaker-diarization-community-1",
    "clip": "openai/clip-vit-large-patch14",
    "bge_reranker": "BAAI/bge-reranker-v2-m3",
    "bge_embedding": "BAAI/bge-m3",
}

_is_offline: bool | None = None


def check_huggingface_reachable(timeout: int = 5) -> bool:
    """測試 huggingface.co 是否可達（TCP 連線測試）。"""
    try:
        sock = socket.create_connection(("huggingface.co", 443), timeout=timeout)
        sock.close()
        return True
    except (socket.timeout, OSError):
        return False


def is_offline_mode() -> bool:
    """回傳目前是否處於離線模式。"""
    global _is_offline
    if _is_offline is None:
        _is_offline = not check_huggingface_reachable()
    return _is_offline


def refresh_online_status() -> bool:
    """重新偵測網路狀態並更新快取值。回傳 True 表示在線。"""
    global _is_offline
    _is_offline = not check_huggingface_reachable()
    if _is_offline:
        set_hf_offline_env()
    else:
        unset_hf_offline_env()
    return not _is_offline


def set_hf_offline_env():
    """設定 HuggingFace Hub 離線環境變數，讓 from_pretrained 不嘗試連線。"""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    logger.info("已設定 HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1（離線模式）")


def unset_hf_offline_env():
    """移除離線環境變數。"""
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)


def _get_hf_cache_dir() -> Path:
    """取得 HuggingFace Hub 快取目錄。"""
    hf_home = os.environ.get("HF_HOME", "")
    if hf_home:
        return Path(hf_home) / "hub"
    cache_dir = os.environ.get("HUGGINGFACE_HUB_CACHE", "")
    if cache_dir:
        return Path(cache_dir)
    return Path.home() / ".cache" / "huggingface" / "hub"


def is_model_cached(model_id: str) -> bool:
    """
    檢查指定 HuggingFace 模型是否已存在於本地快取。

    快取目錄結構為 models--{org}--{name}/snapshots/... ，
    只要 snapshots 資料夾下有內容即視為已快取。
    """
    cache_dir = _get_hf_cache_dir()
    safe_id = model_id.replace("/", "--")
    model_dir = cache_dir / f"models--{safe_id}"
    snapshots_dir = model_dir / "snapshots"
    if not snapshots_dir.exists():
        return False
    return any(snapshots_dir.iterdir())


def get_all_models_status() -> Dict[str, dict]:
    """
    回傳所有必要模型的快取狀態。

    Returns:
        {
          "asr_1.7b": {"model_id": "Qwen/Qwen3-ASR-1.7B", "cached": True},
          ...
        }
    """
    result = {}
    for key, model_id in REQUIRED_MODELS.items():
        result[key] = {
            "model_id": model_id,
            "cached": is_model_cached(model_id),
        }
    return result


def make_offline_error_message(model_id: str) -> str:
    """產生離線時模型未快取的友善錯誤訊息。"""
    return (
        f"模型 {model_id} 尚未下載至本機快取。"
        f"請先在有網路的環境中啟動系統並執行一次相關功能以下載模型，之後即可離線使用。"
    )
