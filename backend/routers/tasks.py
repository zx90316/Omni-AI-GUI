# -*- coding: utf-8 -*-
"""
任務 API 路由
"""
import asyncio
import io
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.database import Task, get_db, init_db
from backend.schemas import TaskResponse, TaskDetailResponse, ConfigResponse
from backend.auth_utils import get_current_user

from backend.config import MODELS, LANGUAGES, DEFAULT_MODEL, DEFAULT_LANGUAGE
from backend.asr_engine import ASREngine, detect_device

router = APIRouter(prefix="/api", tags=["tasks"])

# ── 上傳目錄 ──
UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# ── 進度追蹤（記憶體內） ──
_progress_store: dict[int, dict] = {}


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
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """上傳音訊並建立 ASR 任務"""
    # 驗證模型和語言
    if model not in MODELS:
        raise HTTPException(status_code=400, detail=f"不支援的模型: {model}")
    if language not in LANGUAGES:
        raise HTTPException(status_code=400, detail=f"不支援的語言: {language}")

    import uuid
    # Generate a local video id
    video_id = f"local_{uuid.uuid4().hex[:11]}"
    safe_name = file.filename.replace("/", "_").replace("\\", "_")
    import os
    ext = os.path.splitext(safe_name)[1]
    save_name = f"{video_id}{ext}"
    save_path = UPLOAD_DIR / save_name
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # 建立任務紀錄
    task = Task(
        owner_id=current_user["owner_id"],
        task_type="local",
        video_id=video_id,
        filename=file.filename,
        status="pending",
        model=model,
        language=language,
        enable_diarization=enable_diarization,
        to_traditional=to_traditional,
        progress=0.0,
        progress_message="等待中",
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # 啟動背景 ASR 處理
    task_id = task.id
    audio_path = str(save_path)
    threading.Thread(
        target=_run_asr_task,
        args=(task_id, audio_path, model, language, enable_diarization, to_traditional),
        daemon=True,
    ).start()

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
    return tasks


@router.get("/tasks/media/{video_id}")
def get_task_media(video_id: str):
    """取得上傳的本地影音檔案"""
    if not video_id.startswith("local_"):
        raise HTTPException(status_code=400, detail="僅支援本地上傳檔案")
    
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
        created_at=task.created_at,
        completed_at=task.completed_at,
        merged_result=task.get_sentences(),
        raw_text=task.raw_text,
        sentences=task.get_sentences(),
        diarization_result=task.get_diarization_result(),
    )


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """刪除任務"""
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == current_user["owner_id"]).first()
    if not task:
        raise HTTPException(status_code=404, detail="任務不存在")
    # If it's a local file, delete it from storage
    if task.video_id and task.video_id.startswith("local_"):
        for file_path in UPLOAD_DIR.glob(f"{task.video_id}.*"):
            try:
                file_path.unlink()
            except Exception as e:
                print(f"Error deleting local media {file_path}: {e}")

    db.delete(task)
    db.commit()
    return {"message": "已刪除"}


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
        last_progress = -1
        while True:
            progress_data = _progress_store.get(task_id)
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
                  enable_diarization: bool, to_traditional: bool):
    """在背景執行 ASR 任務"""
    from backend.database import SessionLocal

    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
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
            db.commit()

        model_name = MODELS[model]
        lang_code = LANGUAGES[language]

        engine = ASREngine(
            model_name=model_name,
            device="auto",
            on_progress=on_progress,
        )

        result = engine.run(
            audio_path,
            language=lang_code,
            enable_diarization=enable_diarization,
            to_traditional=to_traditional,
        )

        task.status = "completed"
        task.raw_text = result["raw_text"]
        task.set_sentences(result["sentences"])
        task.set_chars(result.get("chars", []))
        task.set_diarization_result(result.get("diarization_result"))
        task.set_diar_segments(result.get("diar_segments", []))
        task.progress = 100.0
        task.progress_message = "完成"
        task.completed_at = datetime.now(timezone.utc)
        db.commit()

        _progress_store[task_id] = {"percent": 100, "message": "完成", "done": True}

    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        task.progress_message = "處理失敗"
        db.commit()
        _progress_store[task_id] = {"percent": 0, "message": f"失敗: {e}", "done": True}
    finally:
        db.close()
