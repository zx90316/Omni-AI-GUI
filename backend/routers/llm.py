# -*- coding: utf-8 -*-
import json
import asyncio
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import get_db, YouTubeTask
from backend.llm_manager import llm_manager

router = APIRouter(prefix="/api/llm", tags=["llm"])

class ProcessRequest(BaseModel):
    task_id: int
    provider: str
    model: str
    action: str  # "polish" or "translate"
    custom_prompt: Optional[str] = ""
    temperature: float = 0.3
    max_tokens: int = 1024
    thinking_level: str = "medium"  # "low", "medium", "high" (for O1/O3 reasoning models, or mapped to some internal params for Ollama if needed)


class CancelRequest(BaseModel):
    task_id: int

class SaveRequest(BaseModel):
    task_id: int
    sentences: list

@router.get("/providers")
async def get_providers() -> Dict[str, list]:
    """取得可用的 LLM Provider 以及對應的 Models"""
    return llm_manager.get_available_providers_and_models()

# 全域變數紀錄被標記為取消的 task_id
cancelled_tasks = set()

@router.post("/process")
async def process_subtitles(req: ProcessRequest, db: Session = Depends(get_db)):
    """逐句處理字幕 (潤飾或翻譯)，並透過 SSE 回傳進度"""
    db_task = db.query(YouTubeTask).filter(YouTubeTask.id == req.task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    sentences = db_task.get_sentences()
    if not sentences:
        raise HTTPException(status_code=400, detail="No sentences to process")

    # 移除之前的取消標記 (如果有的話)
    if req.task_id in cancelled_tasks:
        cancelled_tasks.remove(req.task_id)

    # SSE Generator
    async def event_generator():
        total = len(sentences)
        processed = 0
        
        for i, sentence in enumerate(sentences):
            if req.task_id in cancelled_tasks:
                print(f"Task {req.task_id} cancelled.")
                cancelled_tasks.remove(req.task_id)
                # 儲存目前進度
                db_task.set_sentences(sentences)
                db.commit()
                yield f"data: {json.dumps({'percent': (processed / total) * 100, 'done': True, 'cancelled': True, 'sentences': sentences}, ensure_ascii=False)}\n\n"
                break

            # 確保有 original_text 欄位紀錄最初的 ASR 結果
            if "original_text" not in sentence:
                sentence["original_text"] = sentence.get("text", "")

            # 取得前後句作為上下文
            prev_text = sentences[i - 1].get("text", "") if i > 0 else ""
            next_text = sentences[i + 1].get("text", "") if i < len(sentences) - 1 else ""

            # 我們可以選擇拿 "翻譯/潤飾" 過的最新文字繼續做，或是拿原始文字做。這裡通常拿最新的 text 做處理
            current_text = sentence.get("text", "")
            
            # 使用 LLM 處理 (這是 blocking call)
            try:
                processed_text = await asyncio.to_thread(
                    llm_manager.process_sentence, 
                    current_text, 
                    req.provider, 
                    req.model, 
                    req.action, 
                    req.custom_prompt,
                    prev_text,
                    next_text,
                    req.temperature,
                    req.max_tokens,
                    req.thinking_level
                )
            except Exception as e:
                processed_text = current_text # 發生錯誤則保留原句
                print(f"Error processing sentence {i}: {e}")
                
            sentence["text"] = processed_text
            processed += 1
            
            # 定期自動儲存 (每 5 句或全部完成前)
            if processed % 5 == 0:
                db_task.set_sentences(sentences)
                db.commit()

            percent = (processed / total) * 100
            
            # 回傳目前進度與剛剛處理完的句子索引
            yield f"data: {json.dumps({'percent': percent, 'index': i, 'processed_text': processed_text, 'done': False}, ensure_ascii=False)}\n\n"
            
            # 稍微延遲避免卡死 Event Loop
            await asyncio.sleep(0.01)
            
        # 全部處理完畢後更新資料庫 (暫存狀態，前端可再手動確認)
        db_task.set_sentences(sentences)
        db.commit()
        
        yield f"data: {json.dumps({'percent': 100, 'done': True, 'sentences': sentences}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.post("/cancel")
async def cancel_processing(req: CancelRequest):
    """標記特定任務為取消狀態"""
    cancelled_tasks.add(req.task_id)
    return {"status": "success", "message": "Cancellation requested"}

@router.post("/save")
async def save_processed_subtitles(req: SaveRequest, db: Session = Depends(get_db)):
    """前端微調後，儲存最終版的字幕"""
    db_task = db.query(YouTubeTask).filter(YouTubeTask.id == req.task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    db_task.set_sentences(req.sentences)
    db.commit()
    
    return {"status": "success"}
