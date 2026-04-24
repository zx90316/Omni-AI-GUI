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
    return {"online": online, "models": models}
