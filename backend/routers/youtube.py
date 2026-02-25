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

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy.orm import Session

from backend.database import YouTubeTask, get_db
from backend.schemas import (
    YouTubeAnalyzeRequest,
    YouTubeTaskResponse,
    YouTubeTaskDetailResponse,
)

import sys, os
import uuid
import shutil
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


@router.post("/analyze/upload", response_model=YouTubeTaskResponse, status_code=201)
async def analyze_youtube_upload(
    file: UploadFile = File(...),
    model: str = Form(DEFAULT_MODEL),
    language: str = Form(DEFAULT_LANGUAGE),
    db: Session = Depends(get_db)
):
    """上傳本地影音檔案進行 ASR 字幕分析"""
    
    # 驗證模型和語言
    if model not in MODELS:
        raise HTTPException(status_code=400, detail=f"不支援的模型: {model}")
    if language not in LANGUAGES:
        raise HTTPException(status_code=400, detail=f"不支援的語言: {language}")

    # Generate a local video id
    video_id = f"local_{uuid.uuid4().hex[:11]}"
    safe_name = file.filename.replace("/", "_").replace("\\", "_")
    
    # Save the file locally
    # We will use the original extension for playback compatibility
    ext = os.path.splitext(safe_name)[1]
    save_path = TEMP_DIR / f"{video_id}{ext}"
    
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # 建立任務
    task = YouTubeTask(
        video_id=video_id,
        video_title=file.filename,
        status="pending",
        model=model,
        language=language,
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
        args=(task_id, video_id, model, language),
        daemon=True,
    ).start()

    return task


@router.get("/media/{video_id}")
def get_youtube_media(video_id: str):
    """取得上傳的本地影音檔案"""
    if not video_id.startswith("local_"):
        raise HTTPException(status_code=400, detail="僅支援本地上傳檔案")
    
    # Find the file with any extension matching the video_id
    for file_path in TEMP_DIR.glob(f"{video_id}.*"):
        if file_path.is_file():
            # stream the file with FileResponse allowing bytes range request
            return FileResponse(file_path)
            
    raise HTTPException(status_code=404, detail="找不到媒體檔案")


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

    # If it's a local file, delete it from storage
    if task.video_id and task.video_id.startswith("local_"):
        for file_path in TEMP_DIR.glob(f"{task.video_id}.*"):
            try:
                file_path.unlink()
            except Exception as e:
                print(f"Error deleting local media {file_path}: {e}")

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

@router.get("/{task_id}/export/{format_type}")
def export_youtube_task(task_id: int, format_type: str, db: Session = Depends(get_db)):
    """
    匯出 YouTube 任務結果
    format_type: txt / srt
    """
    import io
    import urllib.parse
    task = db.query(YouTubeTask).filter(YouTubeTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="任務尚未完成")

    sentences = task.get_sentences()
    base_name = task.video_title or f"youtube_{task.video_id}"
    # Sanitizing filename
    base_name = "".join([c for c in base_name if c.isalpha() or c.isdigit() or c==' ']).rstrip()
    if not base_name:
        base_name = "export"

    buf = io.StringIO()

    if format_type == "txt":
        for sent in sentences:
            start = ASREngine.format_time(sent["start"])
            end = ASREngine.format_time(sent["end"])
            buf.write(f"{start} → {end}\n{sent['text']}\n\n")
        filename = f"{base_name}_subtitle.txt"
        media_type = "text/plain"

    elif format_type == "srt":
        for idx, seg in enumerate(sentences, 1):
            start_srt = ASREngine.format_srt_time(seg["start"])
            end_srt = ASREngine.format_srt_time(seg["end"])
            text = seg["text"]
            buf.write(f"{idx}\n{start_srt} --> {end_srt}\n{text}\n\n")
        filename = f"{base_name}.srt"
        media_type = "text/srt"
    else:
        raise HTTPException(status_code=400, detail=f"不支援的格式: {format_type}")

    buf.seek(0)
    content = buf.getvalue().encode("utf-8-sig")

    encoded_filename = urllib.parse.quote(filename)
    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"},
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

        # ── 1. 下載或取得音頻 ──
        is_local = video_id.startswith("local_")
        
        if is_local:
            on_progress(10, "正在分析本地媒體檔案...")
            # Find the local audio/video file
            audio_path = None
            for file_path in TEMP_DIR.glob(f"{video_id}.*"):
                if file_path.is_file() and file_path.suffix != ".wav": # don't pick up generated wav if exists
                    audio_path = str(file_path)
                    break
            
            if not audio_path:
                raise Exception("找不到上傳的媒體檔案")
            
            # Use original file for ASR, model can usually handle common video/audio formats directly via ffmpeg inside funasr
        else:
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
            if not is_local and Path(audio_path).exists():
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
