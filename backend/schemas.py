# -*- coding: utf-8 -*-
"""
Pydantic schemas — API 請求/回應模型
"""
from datetime import datetime
from typing import Optional, List, Any

from pydantic import BaseModel


# ── 任務建立 ──

class TaskCreate(BaseModel):
    """建立任務時的可選參數（隨檔案一起 multipart 傳送）"""
    model: str = "1.7B (高品質)"
    language: str = "中文"
    enable_diarization: bool = True
    to_traditional: bool = True


# ── 任務回應 ──

class TaskResponse(BaseModel):
    id: int
    task_type: str
    video_id: Optional[str] = None
    filename: str
    status: str
    model: str
    language: str
    enable_diarization: bool
    to_traditional: bool
    progress: float
    progress_message: str
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TaskDetailResponse(TaskResponse):
    """含完整辨識結果"""
    merged_result: Optional[List[Any]] = None
    raw_text: Optional[str] = None
    sentences: Optional[List[Any]] = None
    diarization_result: Optional[List[Any]] = None

    model_config = {"from_attributes": True}


# ── 配置資訊 ──

class ConfigResponse(BaseModel):
    models: dict
    languages: dict
    device: dict


class SentenceUpdateRequest(BaseModel):
    """User-edited subtitle sentences for a completed task."""

    sentences: List[dict[str, Any]]


# ── YouTube SubSync ──

class YouTubeAnalyzeRequest(BaseModel):
    url: str
    model: str = "1.7B (高品質)"
    language: str = "中文"


