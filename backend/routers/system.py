# -*- coding: utf-8 -*-
"""
系統狀態 API — 網路偵測與模型快取狀態
"""
from fastapi import APIRouter

from backend.network_utils import (
    refresh_online_status,
    get_all_models_status,
    is_offline_mode,
)
from backend.asr_engine import get_asr_runtime_status
from backend.config import ASR_MAX_UPLOAD_MB, ASR_TASK_TIMEOUT_SECONDS

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/status")
def system_status():
    """
    回傳系統網路狀態與各 HuggingFace 模型的本地快取狀態。

    Response:
        {
          "online": bool,
          "models": {
            "asr_1.7b": {"model_id": "...", "cached": bool},
            ...
          }
        }
    """
    online = refresh_online_status()
    models = get_all_models_status()
    return {
        "online": online,
        "models": models,
        "asr": {
            **get_asr_runtime_status(),
            "task_timeout_seconds": ASR_TASK_TIMEOUT_SECONDS,
            "max_upload_mb": ASR_MAX_UPLOAD_MB,
        },
    }
