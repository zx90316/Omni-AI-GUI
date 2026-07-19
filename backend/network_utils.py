# -*- coding: utf-8 -*-
"""
網路偵測與本機模型快取工具

提供：
- HuggingFace Hub 可達性檢測（含代理偵測）
- 離線模式環境變數設定
- Hugging Face / PaddleX 模型本地快取狀態查詢
"""
import logging
import os
import socket
import urllib.request
from typing import Dict

from backend.model_cache import get_hf_cache_dir, inspect_model_cache, inspect_paddlex_model
from backend.model_registry import MODEL_SPECS

logger = logging.getLogger(__name__)

_is_offline: bool | None = None


def _get_configured_proxy() -> str | None:
    """取得系統設定的 HTTPS 代理位址。"""
    for key in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return None


def _check_proxy_reachable(timeout: int = 3) -> bool:
    """當有設定代理時，檢查代理本身是否可達。"""
    proxy = _get_configured_proxy()
    if not proxy:
        return True

    try:
        from urllib.parse import urlparse
        parsed = urlparse(proxy if "://" in proxy else f"http://{proxy}")
        host = parsed.hostname
        port = parsed.port or 8118
        if not host:
            return True
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except (socket.timeout, OSError, Exception):
        logger.warning(f"代理 {proxy} 不可達")
        return False


def check_huggingface_reachable(timeout: int = 5) -> bool:
    """
    測試 HuggingFace Hub 是否可達。

    先檢查代理（若有設定）是否可達，再透過 HTTP HEAD 請求驗證
    huggingface.co 的實際連通性。使用 urllib 會自動走代理設定，
    與 HuggingFace 函式庫的行為一致。
    """
    if not _check_proxy_reachable(timeout=min(timeout, 3)):
        return False

    try:
        req = urllib.request.Request(
            "https://huggingface.co/api/whoami-v2",
            method="HEAD",
        )
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def is_offline_mode() -> bool:
    """回傳目前是否處於離線模式（優先檢查環境變數）。"""
    if os.environ.get("HF_HUB_OFFLINE") == "1":
        return True
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


def _patch_hf_hub_offline(offline: bool):
    """
    直接修改已載入的 huggingface_hub / transformers 模組中的離線常數。

    huggingface_hub.constants.HF_HUB_OFFLINE 在 import 時就從環境變數讀取，
    之後不再重讀。若在 import 之後才設定環境變數，必須手動 patch 這個值。
    """
    import sys

    for mod_name in (
        "huggingface_hub.constants",
        "huggingface_hub",
    ):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "HF_HUB_OFFLINE"):
            mod.HF_HUB_OFFLINE = offline

    tf_utils = sys.modules.get("transformers.utils.hub")
    if tf_utils and hasattr(tf_utils, "_is_offline_mode"):
        tf_utils._is_offline_mode = offline


def set_hf_offline_env():
    """設定 HuggingFace Hub 離線環境變數，讓 from_pretrained 不嘗試連線。"""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    _patch_hf_hub_offline(True)
    logger.info("已設定 HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1（離線模式）")


def unset_hf_offline_env():
    """移除離線環境變數並還原模組內常數。"""
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    _patch_hf_hub_offline(False)


def _get_hf_cache_dir():
    """Backward-compatible alias for the shared cache resolver."""
    return get_hf_cache_dir()


def is_model_cached(model_id: str) -> bool:
    """
    檢查指定 Hugging Face 模型是否有可用且完整的本地 snapshot。
    """
    return inspect_model_cache(model_id).cached


def get_all_models_status() -> Dict[str, dict]:
    """
    回傳所有必要模型的快取狀態。

    Returns:
        {
          "asr_1.7b": {"model_id": "Qwen/Qwen3-ASR-1.7B-hf", "cached": True},
          ...
        }
    """
    result = {}
    for spec in MODEL_SPECS:
        status = (
            inspect_paddlex_model(spec.model_id)
            if spec.source == "paddlex"
            else inspect_model_cache(spec.model_id)
        )
        result[spec.key] = status.to_dict()
    return result


def make_offline_error_message(model_id: str) -> str:
    """產生離線時模型未快取的友善錯誤訊息。"""
    return (
        f"模型 {model_id} 尚未下載至本機快取。"
        f"請先在有網路時透過 Manager 的「模型管理」下載並驗證模型，之後即可離線使用。"
    )
