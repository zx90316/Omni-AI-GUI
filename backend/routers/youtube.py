# -*- coding: utf-8 -*-
"""
YouTube SubSync — 影片字幕同步 API 路由
"""
import asyncio
import re
import threading
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.database import YouTubeTask, get_db
from backend.schemas import (
    YouTubeAnalyzeRequest,
    YouTubeTaskResponse,
    YouTubeTaskDetailResponse,
)

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MODELS, LANGUAGES, DEFAULT_MODEL, DEFAULT_LANGUAGE, FFMPEG_DIR
from asr_engine import ASREngine

router = APIRouter(prefix="/api/youtube", tags=["youtube"])

# ── 進度追蹤（記憶體內） ──
_yt_progress_store: dict[int, dict] = {}

# ── 暫存目錄 ──
TEMP_DIR = Path(__file__).resolve().parent.parent.parent / "uploads" / "youtube"
TEMP_DIR.mkdir(parents=True, exist_ok=True)


# ============================================
# 工具函式
# ============================================

_YT_URL_PATTERNS = [
    re.compile(r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})'),
    re.compile(r'(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})'),
    re.compile(r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})'),
    re.compile(r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})'),
]


def extract_video_id(url: str) -> Optional[str]:
    """從 YouTube URL 提取 video ID"""
    for pattern in _YT_URL_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def download_audio(video_id: str, output_dir: Path, on_progress=None) -> dict:
    """
    使用 yt-dlp 下載 YouTube 音頻

    Returns:
        {"audio_path": str, "title": str, "video_id": str}
    """
    import yt_dlp

    url = f"https://www.youtube.com/watch?v={video_id}"
    output_template = str(output_dir / f"{video_id}.%(ext)s")

    info = {"title": "", "audio_path": ""}

    def progress_hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0 and on_progress:
                pct = min(downloaded / total * 20, 20)  # 下載佔 0-20%
                on_progress(pct, f"下載音頻中... {downloaded // 1024}KB / {total // 1024}KB")
        elif d["status"] == "finished":
            if on_progress:
                on_progress(20, "音頻下載完成，準備轉錄...")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
            "preferredquality": "192",
        }],
        "progress_hooks": [progress_hook],
        "ffmpeg_location": FFMPEG_DIR,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        meta = ydl.extract_info(url, download=True)
        info["title"] = meta.get("title", "未知標題")
        # yt-dlp 轉檔後檔名為 .wav
        info["audio_path"] = str(output_dir / f"{video_id}.wav")

    return info


# ============================================
# API 端點
# ============================================

@router.get("", response_model=list[YouTubeTaskResponse])
def get_youtube_tasks(status: Optional[str] = None, db: Session = Depends(get_db)):
    """列出所有 YouTube 任務"""
    query = db.query(YouTubeTask)
    if status:
        query = query.filter(YouTubeTask.status == status)
    # 依建立時間反序（最新的在最前）
    tasks = query.order_by(YouTubeTask.created_at.desc()).all()
    return tasks

@router.post("/analyze", response_model=YouTubeTaskResponse, status_code=201)
def analyze_youtube(req: YouTubeAnalyzeRequest, db: Session = Depends(get_db)):
    """提交 YouTube 影片進行 ASR 字幕分析"""

    # 驗證 URL
    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="無效的 YouTube 網址")

    # 驗證模型和語言
    if req.model not in MODELS:
        raise HTTPException(status_code=400, detail=f"不支援的模型: {req.model}")
    if req.language not in LANGUAGES:
        raise HTTPException(status_code=400, detail=f"不支援的語言: {req.language}")

    # 建立任務
    task = YouTubeTask(
        video_id=video_id,
        video_title=None,
        status="pending",
        model=req.model,
        language=req.language,
        progress=0.0,
        progress_message="等待中",
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # 啟動背景處理
    task_id = task.id
    threading.Thread(
        target=_run_youtube_task,
        args=(task_id, video_id, req.model, req.language),
        daemon=True,
    ).start()

    return task


@router.get("/{task_id}", response_model=YouTubeTaskDetailResponse)
def get_youtube_task(task_id: int, db: Session = Depends(get_db)):
    """查詢 YouTube 字幕任務詳情"""
    task = db.query(YouTubeTask).filter(YouTubeTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")

    return YouTubeTaskDetailResponse(
        id=task.id,
        video_id=task.video_id,
        video_title=task.video_title,
        status=task.status,
        model=task.model,
        language=task.language,
        progress=task.progress,
        progress_message=task.progress_message,
        error_message=task.error_message,
        created_at=task.created_at,
        completed_at=task.completed_at,
        sentences=task.get_sentences(),
    )


@router.delete("/{task_id}", status_code=204)
def delete_youtube_task(task_id: int, db: Session = Depends(get_db)):
    """刪除 YouTube 任務"""
    task = db.query(YouTubeTask).filter(YouTubeTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")

    db.delete(task)
    db.commit()
    return None

@router.get("/{task_id}/progress")
async def youtube_task_progress(task_id: int, db: Session = Depends(get_db)):
    """SSE 即時進度推送"""
    task = db.query(YouTubeTask).filter(YouTubeTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")

    async def event_generator():
        import json
        last_progress = -1
        while True:
            progress_data = _yt_progress_store.get(task_id)
            if progress_data:
                current = progress_data.get("percent", 0)
                if current != last_progress:
                    last_progress = current
                    data = json.dumps(progress_data, ensure_ascii=False)
                    yield f"data: {data}\n\n"

                if progress_data.get("done"):
                    break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================
# 背景處理
# ============================================

def _run_youtube_task(task_id: int, video_id: str, model_key: str, language_key: str):
    """在背景執行 YouTube 音頻下載 + ASR"""
    from backend.database import SessionLocal

    db = SessionLocal()
    try:
        task = db.query(YouTubeTask).filter(YouTubeTask.id == task_id).first()
        if not task:
            return

        task.status = "processing"
        task.progress_message = "開始處理..."
        db.commit()

        def on_progress(percent: float, message: str):
            _yt_progress_store[task_id] = {
                "percent": percent,
                "message": message,
                "done": False,
            }
            task.progress = percent
            task.progress_message = message
            db.commit()

        # ── 1. 下載音頻 ──
        on_progress(1, "正在下載 YouTube 音頻...")
        audio_info = download_audio(video_id, TEMP_DIR, on_progress=on_progress)

        task.video_title = audio_info["title"]
        db.commit()

        audio_path = audio_info["audio_path"]

        # ── 2. ASR 轉錄 ──
        model_name = MODELS[model_key]
        lang_code = LANGUAGES[language_key]

        def on_asr_progress(percent: float, message: str):
            # ASR 進度映射到 20-95%
            mapped = 20 + percent * 0.75
            on_progress(mapped, message)

        engine = ASREngine(
            model_name=model_name,
            device="auto",
            on_progress=on_asr_progress,
        )

        result = engine.run(
            audio_path,
            language=lang_code,
            enable_diarization=False,  # YouTube 不需要語者分離
            to_traditional=True,
        )

        # ── 3. 儲存結果 ──
        task.status = "completed"
        task.set_sentences(result["sentences"])
        task.progress = 100.0
        task.progress_message = "完成"
        task.completed_at = datetime.now(timezone.utc)
        db.commit()

        _yt_progress_store[task_id] = {"percent": 100, "message": "完成", "done": True}

        # 清理暫存音頻
        try:
            Path(audio_path).unlink(missing_ok=True)
        except Exception:
            pass

    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        task.progress_message = "處理失敗"
        db.commit()
        _yt_progress_store[task_id] = {"percent": 0, "message": f"失敗: {e}", "done": True}
    finally:
        db.close()
