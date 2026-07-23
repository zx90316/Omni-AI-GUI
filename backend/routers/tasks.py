# -*- coding: utf-8 -*-
"""
任務 API 路由
"""
import asyncio
import io
import logging
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.database import Task, get_db
from backend.schemas import (
    ConfigResponse,
    SentenceUpdateRequest,
    TaskDetailResponse,
    TaskResponse,
)
from backend.auth_utils import get_current_user
from backend.model_availability import require_asr_models

from backend.config import (
    ASR_MAX_UPLOAD_MB,
    ASR_TASK_TIMEOUT_SECONDS,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    LANGUAGES,
    MODELS,
)
from backend.asr_control import (
    clear_cancel_event,
    get_or_create_cancel_event,
    request_cancel,
)
from backend.asr_engine import (
    ASRCancelledError,
    ASREngine,
    ASRTimeoutError,
    detect_device,
)
from backend.upload_utils import save_upload_limited, validated_upload_name

router = APIRouter(prefix="/api", tags=["tasks"])
logger = logging.getLogger(__name__)

# ── 上傳目錄 ──
UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# ── 進度追蹤（記憶體內） ──
_progress_store: dict[int, dict] = {}


def _task_response(task: Task) -> dict:
    return {
        "id": task.id,
        "task_type": task.task_type,
        "video_id": task.video_id,
        "filename": task.filename,
        "status": task.status,
        "model": task.model,
        "language": task.language,
        "enable_diarization": task.enable_diarization,
        "to_traditional": task.to_traditional,
        "progress": task.progress,
        "progress_message": task.progress_message,
        "error_message": task.error_message,
        "task_options": task.get_task_options(),
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    }


def _start_asr_thread(args: tuple) -> None:
    """Single dispatch seam for reliable startup handling and API tests."""
    threading.Thread(target=_run_asr_task, args=args, daemon=True).start()


# ============================================
# 配置端點
# ============================================

@router.get("/config", response_model=ConfigResponse)
def get_config():
    """取得可用的模型、語言、裝置選項"""
    device_info = detect_device()
    # 將 torch.dtype 轉為字串，避免 Pydantic 序列化失敗
    safe_device = {
        "device": str(device_info.get("device", "cpu")),
        "dtype": str(device_info.get("dtype", "")),
        "label": str(device_info.get("label", "")),
    }
    return ConfigResponse(
        models=MODELS,
        languages=LANGUAGES,
        device=safe_device,
    )


# ============================================
# 任務 CRUD
# ============================================

@router.post("/tasks", response_model=TaskResponse, status_code=201)
async def create_task(
    file: UploadFile = File(...),
    model: str = Form(DEFAULT_MODEL),
    language: str = Form(DEFAULT_LANGUAGE),
    enable_diarization: bool = Form(True),
    to_traditional: bool = Form(True),
    start_time: Optional[float] = Form(None),
    end_time: Optional[float] = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """上傳音訊並建立 ASR 任務"""
    # 驗證模型和語言
    if model not in MODELS:
        raise HTTPException(status_code=400, detail=f"不支援的模型: {model}")
    if language not in LANGUAGES:
        raise HTTPException(status_code=400, detail=f"不支援的語言: {language}")

    if start_time is not None and (not math.isfinite(start_time) or start_time < 0):
        raise HTTPException(status_code=400, detail="片段開頭不可小於 0 秒")
    if end_time is not None and (not math.isfinite(end_time) or end_time <= 0):
        raise HTTPException(status_code=400, detail="片段結尾必須大於 0 秒")
    if start_time is not None and end_time is not None and end_time <= start_time:
        raise HTTPException(status_code=400, detail="片段結尾必須晚於片段開頭")

    require_asr_models(MODELS[model], diarization=enable_diarization)

    import uuid
    # Generate a local video id
    video_id = f"local_{uuid.uuid4().hex[:11]}"
    original_name, ext = validated_upload_name(file)
    save_name = f"{video_id}{ext}"
    save_path = UPLOAD_DIR / save_name
    await save_upload_limited(
        file,
        save_path,
        max_bytes=ASR_MAX_UPLOAD_MB * 1024 * 1024,
    )

    # 建立任務紀錄
    task = Task(
        owner_id=current_user["owner_id"],
        task_type="local",
        video_id=video_id,
        filename=original_name,
        status="pending",
        model=model,
        language=language,
        enable_diarization=enable_diarization,
        to_traditional=to_traditional,
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

    # 啟動背景 ASR 處理
    task_id = task.id
    audio_path = str(save_path)
    get_or_create_cancel_event(task_id)
    _progress_store[task_id] = {"percent": 0, "message": "等待中", "done": False}
    try:
        _start_asr_thread((
            task_id,
            audio_path,
            model,
            language,
            enable_diarization,
            to_traditional,
            start_time,
            end_time,
        ))
    except Exception as exc:
        clear_cancel_event(task_id)
        task.status = "failed"
        task.error_message = f"無法啟動 ASR 背景工作: {exc}"
        task.progress_message = "啟動失敗"
        task.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=500, detail=task.error_message) from exc

    return task


@router.get("/tasks", response_model=list[TaskResponse])
def list_tasks(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """查詢任務清單"""
    query = db.query(Task).filter(Task.owner_id == current_user["owner_id"])
    if status:
        query = query.filter(Task.status == status)
    tasks = query.order_by(Task.created_at.desc()).offset(skip).limit(limit).all()
    return [_task_response(task) for task in tasks]


@router.get("/tasks/media/{video_id}")
def get_task_media(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """取得上傳的本地影音檔案"""
    if not video_id.startswith(("local_", "ocr_")):
        raise HTTPException(status_code=400, detail="僅支援本地上傳檔案")
    task_type = "ocr" if video_id.startswith("ocr_") else "local"
    owned_task = db.query(Task).filter(
        Task.video_id == video_id,
        Task.owner_id == current_user["owner_id"],
        Task.task_type == task_type,
    ).first()
    if owned_task is None:
        raise HTTPException(status_code=404, detail="找不到媒體檔案")
    
    from fastapi.responses import FileResponse
    # Find the file with any extension matching the video_id
    for file_path in UPLOAD_DIR.glob(f"{video_id}.*"):
        if file_path.is_file():
            # stream the file with FileResponse allowing bytes range request
            return FileResponse(file_path)
            
    raise HTTPException(status_code=404, detail="找不到媒體檔案")


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
def get_task(task_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """查詢單一任務詳情（含結果）"""
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == current_user["owner_id"]).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")

    # 手動構建 dict，先反序列化 JSON 字串欄位
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
        task_options=task.get_task_options(),
        created_at=task.created_at,
        completed_at=task.completed_at,
        merged_result=task.get_sentences(),
        raw_text=task.raw_text,
        sentences=task.get_sentences(),
        diarization_result=task.get_diarization_result(),
        result_data=task.get_result_data(),
    )


@router.put("/tasks/{task_id}/sentences")
def update_task_sentences(
    task_id: int,
    payload: SentenceUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Persist manual subtitle edits without routing content through an LLM API."""
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.owner_id == current_user["owner_id"],
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="找不到任務")
    if task.status in {"pending", "processing", "cancelling"}:
        raise HTTPException(status_code=409, detail="任務執行期間無法修改字幕")
    if len(payload.sentences) > 100_000:
        raise HTTPException(status_code=413, detail="字幕段落數量過多")

    for index, sentence in enumerate(payload.sentences):
        text = sentence.get("text")
        if not isinstance(text, str):
            raise HTTPException(
                status_code=422,
                detail=f"第 {index + 1} 段字幕缺少有效文字",
            )
        if len(text) > 20_000:
            raise HTTPException(
                status_code=422,
                detail=f"第 {index + 1} 段字幕文字過長",
            )

    task.set_sentences(payload.sentences)
    db.commit()
    db.refresh(task)
    return {"message": "字幕已儲存", "sentences": task.get_sentences()}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """刪除任務"""
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == current_user["owner_id"]).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")
    if task.status in {"pending", "processing", "cancelling"}:
        raise HTTPException(status_code=409, detail="任務仍在執行，請先取消後再刪除")
    # If it's a local file, delete it from storage
    if task.video_id and task.video_id.startswith(("local_", "ocr_")):
        for file_path in UPLOAD_DIR.glob(f"{task.video_id}.*"):
            try:
                file_path.unlink()
            except Exception as e:
                print(f"Error deleting local media {file_path}: {e}")

    db.delete(task)
    db.commit()
    return {"message": "已刪除"}


@router.post("/tasks/{task_id}/cancel")
def cancel_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Cooperatively cancel a queued or running ASR task."""
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.owner_id == current_user["owner_id"],
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")
    if task.task_type == "ocr":
        raise HTTPException(status_code=409, detail="OCR 任務目前不支援取消")
    if task.status == "cancelled":
        return {"status": "cancelled", "message": "任務已取消"}
    if task.status in {"completed", "failed"}:
        raise HTTPException(status_code=409, detail="已結束的任務無法取消")

    request_cancel(task_id)
    task.status = "cancelling"
    task.progress_message = "正在安全取消..."
    db.commit()
    _progress_store[task_id] = {
        "percent": task.progress or 0,
        "message": task.progress_message,
        "done": False,
    }
    return {"status": "cancelling", "message": task.progress_message}


# ============================================
# SSE 進度推送
# ============================================

@router.get("/tasks/{task_id}/progress")
async def task_progress(task_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """SSE 即時進度推送"""
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == current_user["owner_id"]).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")

    async def event_generator():
        import json
        last_state = None
        while True:
            progress_data = _progress_store.get(task_id)
            if progress_data is None:
                # 記憶體進度會在服務重啟時消失；以資料庫狀態作為可靠 fallback。
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
                _progress_store.pop(task_id, None)
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
# 匯出
# ============================================

@router.get("/tasks/{task_id}/export/{format_type}")
def export_task(task_id: int, format_type: str, variant: str = "merged", db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """
    匯出結果
    format_type: txt / srt
    variant: merged / raw / subtitle
    """
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == current_user["owner_id"]).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="任務尚未完成")

    segments = task.get_diarization_result() or task.get_sentences()
    sentences = task.get_sentences()
    raw_text = task.raw_text or ""
    base_name = Path(task.filename).stem

    buf = io.StringIO()

    if format_type == "txt":
        if variant == "merged":
            for seg in segments:
                spk = seg.get("speaker", "")
                start = ASREngine.format_time(seg["start"])
                end = ASREngine.format_time(seg["end"])
                text = seg.get("combined_text", seg.get("text", ""))
                prefix = f"[{spk}] " if spk else ""
                buf.write(f"{prefix}({start} → {end})\n{text}\n\n")
            filename = f"{base_name}_merged.txt"
        elif variant == "raw":
            buf.write(raw_text)
            filename = f"{base_name}_raw.txt"
        elif variant == "subtitle":
            for sent in sentences:
                start = ASREngine.format_time(sent["start"])
                end = ASREngine.format_time(sent["end"])
                buf.write(f"{start} → {end}\n{sent['text']}\n\n")
            filename = f"{base_name}_subtitle.txt"
        else:
            raise HTTPException(status_code=400, detail=f"不支援的 variant: {variant}")
        media_type = "text/plain"

    elif format_type == "srt":
        if variant == "merged":
            # 語者歸組用 diarization 資料
            for idx, seg in enumerate(segments, 1):
                start_srt = ASREngine.format_srt_time(seg["start"])
                end_srt = ASREngine.format_srt_time(seg["end"])
                spk = seg.get("speaker", "")
                text = seg.get("combined_text", seg.get("text", ""))
                if spk:
                    text = f"[{spk}] {text}"
                buf.write(f"{idx}\n{start_srt} --> {end_srt}\n{text}\n\n")
        else:
            # 字幕用 sentences 資料
            for idx, seg in enumerate(sentences, 1):
                start_srt = ASREngine.format_srt_time(seg["start"])
                end_srt = ASREngine.format_srt_time(seg["end"])
                buf.write(f"{idx}\n{start_srt} --> {end_srt}\n{seg['text']}\n\n")
        filename = f"{base_name}_{variant}.srt"
        media_type = "text/srt"
    else:
        raise HTTPException(status_code=400, detail=f"不支援的格式: {format_type}")

    buf.seek(0)
    content = buf.getvalue().encode("utf-8-sig")

    import urllib.parse
    encoded_filename = urllib.parse.quote(filename)
    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"},
    )


# ============================================
# 重新分句
# ============================================

from pydantic import BaseModel as _BaseModel

class ResegmentRequest(_BaseModel):
    max_sentence_chars: int = 30
    force_cut_chars: int = 50

@router.post("/tasks/{task_id}/resegment")
def resegment_task(
    task_id: int,
    req: ResegmentRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
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

    engine = ASREngine(device="cpu")
    diar_segments = task.get_diar_segments() or [{"start": 0.0, "end": 999999.0, "speaker": "UNKNOWN"}]

    new_sentences = engine.build_sentences_from_chars(
        chars=chars,
        diar_segments=diar_segments,
        mode="subtitle",
        max_sentence_chars=req.max_sentence_chars,
        force_cut_chars=req.force_cut_chars,
        to_traditional=task.to_traditional if task.to_traditional else False,
    )

    task.set_sentences(new_sentences)
    db.commit()

    return {"message": "success", "sentences": new_sentences}


# ============================================
# 去除標點
# ============================================


class RemovePunctuationRequest(_BaseModel):
    mode: str = "all"  # "all" | "sentence_end"
    replace_with_space: bool = False  # True: 以空格替換, False: 直接去除

@router.post("/tasks/{task_id}/remove-punctuation")
def remove_punctuation(
    task_id: int,
    req: RemovePunctuationRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """去除標點符號（全部 / 句末），儲存結果並支援復原"""
    import re
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == current_user["owner_id"]).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="任務尚未完成")

    sentences = task.get_sentences()
    if not sentences:
        raise HTTPException(status_code=400, detail="沒有可處理的字幕")

    # 定義標點集合
    all_punct = r'[，。！？、；：＂＇（）《》【】…—·,.:;!?\'"()\[\]{}~@#$%^&*+\-=/<>]'
    # 句末標點：匹配字串最尾端的標點（含逗號、頓號、分號等）
    sentence_end_punct = r'[，。！？、；：,.:;!?]+$'

    replacement = ' ' if req.replace_with_space else ''

    updated = []
    for sent in sentences:
        original = sent["text"]
        # 保存原始文字（若尚未被儲存）
        if "original_text" not in sent:
            sent["original_text"] = original

        if req.mode == "all":
            cleaned = re.sub(all_punct, replacement, original)
        else:  # sentence_end
            cleaned = re.sub(sentence_end_punct, replacement, original)

        # 若以空格替換，清理多餘空格
        if req.replace_with_space:
            cleaned = re.sub(r' +', ' ', cleaned).strip()

        sent["text"] = cleaned
        updated.append(sent)

    task.set_sentences(updated)
    db.commit()

    return {"message": "success", "sentences": updated}


# ============================================
# 背景 ASR 處理
# ============================================

def _run_asr_task(task_id: int, audio_path: str, model: str, language: str,
                  enable_diarization: bool, to_traditional: bool,
                  start_time: Optional[float] = None, end_time: Optional[float] = None):
    """在背景執行 ASR 任務"""
    from backend.database import SessionLocal

    db = SessionLocal()
    task = None
    cancel_event = get_or_create_cancel_event(task_id)
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            _progress_store.pop(task_id, None)
            return

        task.status = "processing"
        task.progress_message = "開始處理..."
        db.commit()

        def on_progress(percent: float, message: str):
            _progress_store[task_id] = {
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
                logger.warning("任務 %s 進度寫入失敗，ASR 將繼續", task_id, exc_info=True)

        model_name = MODELS[model]
        lang_code = LANGUAGES[language]

        engine = ASREngine(
            model_name=model_name,
            device="auto",
            on_progress=on_progress,
            should_cancel=cancel_event.is_set,
            timeout_seconds=ASR_TASK_TIMEOUT_SECONDS,
        )

        result = engine.run(
            audio_path,
            language=lang_code,
            enable_diarization=enable_diarization,
            to_traditional=to_traditional,
            start_time=start_time,
            end_time=end_time,
        )
        if cancel_event.is_set():
            raise ASRCancelledError("ASR 任務已取消（結果寫入前）")

        task.status = "completed"
        task.raw_text = result["raw_text"]
        task.set_sentences(result["sentences"])
        task.set_chars(result.get("chars", []))
        task.set_diarization_result(result.get("diarization_result"))
        task.set_diar_segments(result.get("diar_segments", []))
        task.progress = 100.0
        warnings = result.get("warnings", [])
        task.progress_message = f"完成（{'；'.join(warnings)}）" if warnings else "完成"
        task.completed_at = datetime.now(timezone.utc)
        db.commit()

        # 終態已持久化，移除記憶體快取；SSE 會從資料庫讀取可靠終態。
        _progress_store.pop(task_id, None)

    except ASRCancelledError as e:
        logger.info("ASR 任務 %s 已取消: %s", task_id, e)
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
                logger.exception("ASR 任務 %s 的取消狀態無法寫入資料庫", task_id)
        if terminal_persisted:
            _progress_store.pop(task_id, None)
        else:
            _progress_store[task_id] = {
                "percent": 0,
                "message": "已取消",
                "done": True,
            }
    except ASRTimeoutError as e:
        logger.error("ASR 任務 %s 逾時: %s", task_id, e)
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
                logger.exception("ASR 任務 %s 的逾時狀態無法寫入資料庫", task_id)
        if terminal_persisted:
            _progress_store.pop(task_id, None)
        else:
            _progress_store[task_id] = {
                "percent": 0,
                "message": f"失敗: {e}",
                "done": True,
            }
    except Exception as e:
        logger.exception("ASR 任務 %s 失敗", task_id)
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
                logger.exception("ASR 任務 %s 失敗狀態無法寫入資料庫", task_id)
        if failure_persisted:
            _progress_store.pop(task_id, None)
        else:
            _progress_store[task_id] = {"percent": 0, "message": f"失敗: {e}", "done": True}
    finally:
        clear_cancel_event(task_id)
        db.close()
