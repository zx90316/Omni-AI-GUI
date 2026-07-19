# -*- coding: utf-8 -*-
"""System readiness and local model state API."""
from fastapi import APIRouter

from backend.network_utils import get_all_models_status
from backend.asr_engine import get_asr_runtime_status
from backend.config import ASR_MAX_UPLOAD_MB, ASR_TASK_TIMEOUT_SECONDS

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/status")
def system_status():
    """
    回傳各模型的本地快取狀態；網路狀態不影響功能可用性。

    Response:
        {
          "models": {
            "asr_1.7b": {"model_id": "...", "cached": bool},
            ...
          }
        }
    """
    models = get_all_models_status()
    return {
        "models": models,
        "asr": {
            **get_asr_runtime_status(),
            "task_timeout_seconds": ASR_TASK_TIMEOUT_SECONDS,
            "max_upload_mb": ASR_MAX_UPLOAD_MB,
        },
    }
