# -*- coding: utf-8 -*-
"""
YouTube SubSync — 影片字幕同步 API 路由
"""
import asyncio
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy.orm import Session

from backend.database import Task, get_db
from backend.schemas import (
    YouTubeAnalyzeRequest,
    TaskResponse,
    TaskDetailResponse,
)
from backend.auth_utils import get_current_user

import uuid
import shutil

from backend.config import (
    ASR_MAX_UPLOAD_MB,
    ASR_TASK_TIMEOUT_SECONDS,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    FFMPEG_DIR,
    LANGUAGES,
    MODELS,
)
from backend.asr_control import (
    clear_cancel_event,
    get_or_create_cancel_event,
    request_cancel,
)
from backend.asr_engine import ASRCancelledError, ASREngine, ASRTimeoutError
from backend.model_availability import require_asr_models
from backend.upload_utils import save_upload_limited, validated_upload_name

router = APIRouter(prefix="/api/youtube", tags=["youtube"])
logger = logging.getLogger(__name__)

# ── 進度追蹤（記憶體內） ──
_yt_progress_store: dict[int, dict] = {}


def _start_youtube_thread(args: tuple) -> None:
    """Single dispatch seam for reliable startup handling and API tests."""
    threading.Thread(target=_run_youtube_task, args=args, daemon=True).start()

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


def download_audio(
    video_id: str,
    output_dir: Path,
    on_progress=None,
    should_cancel=None,
) -> dict:
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
        if should_cancel is not None and should_cancel():
            raise ASRCancelledError("YouTube 音訊下載已取消")
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
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            meta = ydl.extract_info(url, download=True)
    except Exception as exc:
        if should_cancel is not None and should_cancel():
            raise ASRCancelledError("YouTube 音訊下載已取消") from exc
        raise
    if should_cancel is not None and should_cancel():
        raise ASRCancelledError("YouTube 音訊下載已取消")
    if meta:
        info["title"] = meta.get("title", "未知標題")
        # yt-dlp 轉檔後檔名為 .wav
        info["audio_path"] = str(output_dir / f"{video_id}.wav")

    if not Path(info["audio_path"]).is_file():
        raise RuntimeError("YouTube 音訊下載完成，但找不到轉換後的 WAV 檔案")

    return info


# ============================================
# API 端點
# ============================================

@router.get("", response_model=list[TaskResponse])
def get_youtube_tasks(status: Optional[str] = None, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """列出所有 YouTube 任務"""
    query = db.query(Task).filter(Task.owner_id == current_user["owner_id"], Task.task_type.in_(["youtube", "subsync_upload"]))
    if status:
        query = query.filter(Task.status == status)
    # 依建立時間反序（最新的在最前）
    tasks = query.order_by(Task.created_at.desc()).all()
    return tasks

@router.post("/analyze", response_model=TaskResponse, status_code=201)
def analyze_youtube(req: YouTubeAnalyzeRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
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
    require_asr_models(MODELS[req.model])

    task = Task(
        owner_id=current_user["owner_id"],
        task_type="youtube",
        video_id=video_id,
        filename=req.url, # fallback filename
        status="pending",
        model=req.model,
        language=req.language,
        progress=0.0,
        enable_diarization=False,
        to_traditional=True,
        progress_message="等待中",
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # 啟動背景處理
    task_id = task.id
    get_or_create_cancel_event(task_id)
    _yt_progress_store[task_id] = {"percent": 0, "message": "等待中", "done": False}
    try:
        _start_youtube_thread((task_id, video_id, req.model, req.language))
    except Exception as exc:
        clear_cancel_event(task_id)
        task.status = "failed"
        task.error_message = f"無法啟動 YouTube 背景工作: {exc}"
        task.progress_message = "啟動失敗"
        task.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=500, detail=task.error_message) from exc

    return task


@router.post("/analyze/upload", response_model=TaskResponse, status_code=201)
async def analyze_youtube_upload(
    file: UploadFile = File(...),
    model: str = Form(DEFAULT_MODEL),
    language: str = Form(DEFAULT_LANGUAGE),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """上傳本地影音檔案進行 ASR 字幕分析"""
    
    # 驗證模型和語言
    if model not in MODELS:
        raise HTTPException(status_code=400, detail=f"不支援的模型: {model}")
    if language not in LANGUAGES:
        raise HTTPException(status_code=400, detail=f"不支援的語言: {language}")

    # Generate a local video id
    require_asr_models(MODELS[model])

    video_id = f"local_{uuid.uuid4().hex[:11]}"
    original_name, ext = validated_upload_name(file)
    
    # Save the file locally
    # We will use the original extension for playback compatibility
    save_path = TEMP_DIR / f"{video_id}{ext}"
    await save_upload_limited(
        file,
        save_path,
        max_bytes=ASR_MAX_UPLOAD_MB * 1024 * 1024,
    )

    # 建立任務
    task = Task(
        owner_id=current_user["owner_id"],
        task_type="subsync_upload",
        video_id=video_id,
        filename=original_name,
        status="pending",
        model=model,
        language=language,
        enable_diarization=False,
        to_traditional=True,
        progress=0.0,
        progress_message="等待中",
    )
    try:
        db.add(task)
        db.commit()
        db.refresh(task)
    except Exception:
        db.rollback()
        save_path.unlink(missing_ok=True)
        raise

    # 啟動背景處理
    task_id = task.id
    get_or_create_cancel_event(task_id)
    _yt_progress_store[task_id] = {"percent": 0, "message": "等待中", "done": False}
    try:
        _start_youtube_thread((task_id, video_id, model, language))
    except Exception as exc:
        clear_cancel_event(task_id)
        task.status = "failed"
        task.error_message = f"無法啟動 YouTube 背景工作: {exc}"
        task.progress_message = "啟動失敗"
        task.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=500, detail=task.error_message) from exc

    return task


@router.get("/media/{video_id}")
def get_youtube_media(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """取得上傳的本地影音檔案"""
    if not video_id.startswith("local_"):
        raise HTTPException(status_code=400, detail="僅支援本地上傳檔案")
    owned_task = db.query(Task).filter(
        Task.video_id == video_id,
        Task.owner_id == current_user["owner_id"],
        Task.task_type == "subsync_upload",
    ).first()
    if owned_task is None:
        raise HTTPException(status_code=404, detail="找不到媒體檔案")
    
    # Find the file with any extension matching the video_id
    for file_path in TEMP_DIR.glob(f"{video_id}.*"):
        if file_path.is_file():
            # stream the file with FileResponse allowing bytes range request
            return FileResponse(file_path)
            
    raise HTTPException(status_code=404, detail="找不到媒體檔案")


@router.get("/{task_id}", response_model=TaskDetailResponse)
def get_youtube_task(task_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """查詢 YouTube 字幕任務詳情"""
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == current_user["owner_id"]).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")

    return TaskDetailResponse(
        id=task.id,
        task_type=task.task_type,
        video_id=task.video_id,
        filename=task.filename,
        status=task.status,
        model=task.model,
        language=task.language,
        enable_diarization=task.enable_diarization,
        to_traditional=task.to_traditional,
        progress=task.progress,
        progress_message=task.progress_message,
        error_message=task.error_message,
        created_at=task.created_at,
        completed_at=task.completed_at,
        raw_text=task.raw_text,
        merged_result=task.get_sentences(),
        sentences=task.get_sentences(),
        diarization_result=task.get_diarization_result(),
    )


from pydantic import BaseModel
class ResegmentRequest(BaseModel):
    max_sentence_chars: int = 30
    force_cut_chars: int = 50

@router.post("/{task_id}/resegment")
def resegment_youtube_task(
    task_id: int, 
    req: ResegmentRequest,
    db: Session = Depends(get_db), 
    current_user: dict = Depends(get_current_user)
):
    """重新計算分句，無需重新執行 ASR 模型"""
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == current_user["owner_id"]).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="任務尚未完成，無法重新分句")
        
    chars = task.get_chars()
    if not chars:
        raise HTTPException(status_code=400, detail="此任務缺少 raw chars 資訊，請重新上傳分析")
        
    engine = ASREngine(device="cpu") # We don't need load_model() for merge
    diar_segments = [{"start": 0.0, "end": 999999.0, "speaker": "UNKNOWN"}]
    
    new_sentences = engine.build_sentences_from_chars(
        chars=chars,
        diar_segments=diar_segments,
        mode="subtitle",
        max_sentence_chars=req.max_sentence_chars,
        force_cut_chars=req.force_cut_chars,
        to_traditional=True, # For simplicity
    )
    
    task.set_sentences(new_sentences)
    db.commit()
    
    return {"message": "success", "sentences": new_sentences}


@router.delete("/{task_id}", status_code=204)
def delete_youtube_task(task_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """刪除 YouTube 任務"""
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == current_user["owner_id"]).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")
    if task.status in {"pending", "processing", "cancelling"}:
        raise HTTPException(status_code=409, detail="任務仍在執行，請先取消後再刪除")

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


@router.post("/{task_id}/cancel")
def cancel_youtube_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Cooperatively cancel a queued/download/running YouTube ASR task."""
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.owner_id == current_user["owner_id"],
        Task.task_type.in_(("youtube", "subsync_upload")),
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")
    if task.status == "cancelled":
        return {"status": "cancelled", "message": "任務已取消"}
    if task.status in {"completed", "failed"}:
        raise HTTPException(status_code=409, detail="已結束的任務無法取消")

    request_cancel(task_id)
    task.status = "cancelling"
    task.progress_message = "正在安全取消..."
    db.commit()
    _yt_progress_store[task_id] = {
        "percent": task.progress or 0,
        "message": task.progress_message,
        "done": False,
    }
    return {"status": "cancelling", "message": task.progress_message}

@router.get("/{task_id}/progress")
async def youtube_task_progress(task_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """SSE 即時進度推送"""
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == current_user["owner_id"]).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")

    async def event_generator():
        import json
        last_state = None
        while True:
            progress_data = _yt_progress_store.get(task_id)
            if progress_data is None:
                db.expire_all()
                current_task = db.query(Task).filter(
                    Task.id == task_id,
                    Task.owner_id == current_user["owner_id"],
                ).first()
                if current_task is None:
                    progress_data = {"percent": 0, "message": "任務不存在", "done": True}
                else:
                    done = current_task.status in {"completed", "failed", "cancelled"}
                    message = current_task.progress_message or current_task.status
                    if current_task.status == "failed" and current_task.error_message:
                        message = f"失敗: {current_task.error_message}"
                    progress_data = {
                        "percent": current_task.progress or 0,
                        "message": message,
                        "done": done,
                    }

            current_state = (
                progress_data.get("percent", 0),
                progress_data.get("message", ""),
                progress_data.get("done", False),
            )
            if current_state != last_state:
                last_state = current_state
                data = json.dumps(progress_data, ensure_ascii=False)
                yield f"data: {data}\n\n"

            if progress_data.get("done"):
                _yt_progress_store.pop(task_id, None)
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
def export_youtube_task(task_id: int, format_type: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """
    匯出 YouTube 任務結果
    format_type: txt / srt
    """
    import io
    import urllib.parse
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == current_user["owner_id"]).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="任務尚未完成")

    sentences = task.get_sentences()
    base_name = task.filename or f"youtube_{task.video_id}"
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
    task = None
    download_work_dir = None
    cancel_event = get_or_create_cancel_event(task_id)
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            _yt_progress_store.pop(task_id, None)
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
            try:
                db.commit()
            except Exception:
                db.rollback()
                logger.warning("YouTube 任務 %s 進度寫入失敗，ASR 將繼續", task_id, exc_info=True)

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
            # 同一影片可被同時提交；每個任務使用獨立下載目錄避免互相覆寫。
            download_work_dir = TEMP_DIR / f"task_{task_id}"
            download_work_dir.mkdir(parents=True, exist_ok=True)
            audio_info = download_audio(
                video_id,
                download_work_dir,
                on_progress=on_progress,
                should_cancel=cancel_event.is_set,
            )

            task.filename = audio_info["title"]
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
            should_cancel=cancel_event.is_set,
            timeout_seconds=ASR_TASK_TIMEOUT_SECONDS,
        )

        result = engine.run(
            audio_path,
            language=lang_code,
            enable_diarization=False,  # YouTube 不需要語者分離
            to_traditional=True,
        )
        if cancel_event.is_set():
            raise ASRCancelledError("YouTube ASR 任務已取消（結果寫入前）")

        # ── 3. 儲存結果 ──
        task.status = "completed"
        task.raw_text = result["raw_text"]
        task.set_sentences(result["sentences"])
        task.set_chars(result.get("chars", []))
        task.set_diar_segments(result.get("diar_segments", []))
        task.progress = 100.0
        warnings = result.get("warnings", [])
        task.progress_message = f"完成（{'；'.join(warnings)}）" if warnings else "完成"
        task.completed_at = datetime.now(timezone.utc)
        db.commit()

        # 終態已持久化，移除記憶體快取；SSE 會從資料庫讀取可靠終態。
        _yt_progress_store.pop(task_id, None)

    except ASRCancelledError as e:
        logger.info("YouTube ASR 任務 %s 已取消: %s", task_id, e)
        db.rollback()
        terminal_persisted = False
        task = db.query(Task).filter(Task.id == task_id).first()
        if task is not None:
            task.status = "cancelled"
            task.error_message = None
            task.progress_message = "已取消"
            task.completed_at = datetime.now(timezone.utc)
            try:
                db.commit()
                terminal_persisted = True
            except Exception:
                db.rollback()
                logger.exception("YouTube ASR 任務 %s 的取消狀態無法寫入資料庫", task_id)
        if terminal_persisted:
            _yt_progress_store.pop(task_id, None)
        else:
            _yt_progress_store[task_id] = {
                "percent": 0,
                "message": "已取消",
                "done": True,
            }
    except ASRTimeoutError as e:
        logger.error("YouTube ASR 任務 %s 逾時: %s", task_id, e)
        db.rollback()
        terminal_persisted = False
        task = db.query(Task).filter(Task.id == task_id).first()
        if task is not None:
            task.status = "failed"
            task.error_message = str(e)
            task.progress_message = "處理逾時"
            task.completed_at = datetime.now(timezone.utc)
            try:
                db.commit()
                terminal_persisted = True
            except Exception:
                db.rollback()
                logger.exception("YouTube ASR 任務 %s 的逾時狀態無法寫入資料庫", task_id)
        if terminal_persisted:
            _yt_progress_store.pop(task_id, None)
        else:
            _yt_progress_store[task_id] = {
                "percent": 0,
                "message": f"失敗: {e}",
                "done": True,
            }
    except Exception as e:
        logger.exception("YouTube ASR 任務 %s 失敗", task_id)
        db.rollback()
        failure_persisted = False
        task = db.query(Task).filter(Task.id == task_id).first()
        if task is not None:
            task.status = "failed"
            task.error_message = str(e)
            task.progress_message = "處理失敗"
            task.completed_at = datetime.now(timezone.utc)
            try:
                db.commit()
                failure_persisted = True
            except Exception:
                db.rollback()
                logger.exception("YouTube ASR 任務 %s 失敗狀態無法寫入資料庫", task_id)
        if failure_persisted:
            _yt_progress_store.pop(task_id, None)
        else:
            _yt_progress_store[task_id] = {"percent": 0, "message": f"失敗: {e}", "done": True}
    finally:
        clear_cancel_event(task_id)
        if download_work_dir is not None:
            try:
                shutil.rmtree(download_work_dir)
            except FileNotFoundError:
                pass
            except Exception:
                logger.warning("無法清理 YouTube 任務 %s 暫存目錄", task_id, exc_info=True)
        db.close()
