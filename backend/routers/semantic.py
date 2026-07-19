# -*- coding: utf-8 -*-
"""
Semantic Reranking & Embedding Router
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Any
import asyncio
from asyncio import Future

from backend.semantic_engine import (
    model_container, 
    request_queue, 
    config, 
    init_semantic_models,
    start_worker
)
from backend.model_availability import require_models

router = APIRouter(
    prefix="/semantic",
    tags=["Semantic"]
)

# ================= 請求模型 =================
class RerankRequest(BaseModel):
    pairs: List[List[str]] = Field(
        ...,
        json_schema_extra={
            "example": [
                ['what is panda?', 'hi'],
                ['what is panda?', 'The giant panda (Ailuropoda melanoleuca) is a bear species endemic to China.']
            ]
        },
        description="需要計算分數的文本對列表，格式為 [query, document]"
    )
    normalize: bool = Field(
        default=True,
        description="是否對分數進行正規化"
    )

class EmbeddingRequest(BaseModel):
    sentences: List[str] = Field(
        ...,
        json_schema_extra={"example": ['你好', '世界', 'Hello', 'World']},
        description="需要進行向量化的文本列表"
    )

# ================= 共用請求處理 =================
async def process_request(model_name: str, data: Dict[str, Any], timeout: float = None):
    if timeout is None:
        timeout = config.request_timeout
        
    if not model_container.models_loaded:
        # If not loaded, try to load lazily (this might block or we just tell the user models are missing)
        raise HTTPException(
            status_code=503, 
            detail="Semantic 模型尚未載入或未安裝 (請確認 Model_Files 目錄存在並包含模型)"
        )
        
    if request_queue.full():
        raise HTTPException(
            status_code=503, 
            detail="伺服器忙碌，請求隊列已滿，請稍後再試"
        )
        
    loop = asyncio.get_running_loop()
    future: Future = loop.create_future()
    
    try:
        await request_queue.put((model_name, data, future))
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        if not future.done():
            future.cancel()
        raise HTTPException(status_code=504, detail="處理請求超時")
    except Exception as e:
        if not future.done():
            future.cancel()
        raise HTTPException(status_code=500, detail=str(e))

# ================= 路由 =================
@router.post("/rerank", summary="計算文本對的相關性分數")
async def compute_rerank_scores(request: RerankRequest):
    require_models(["bge_reranker"])
    if not request.pairs:
        raise HTTPException(status_code=400, detail="文本對列表不能為空")
        
    for i, pair in enumerate(request.pairs):
        if len(pair) != 2:
            raise HTTPException(status_code=400, detail="文本對格式錯誤，應包含兩個元素: [query, doc]")
            
    result = await process_request('reranker', request.dict())
    return {"scores": result}

@router.post("/embed", summary="生成文本的向量嵌入 (Embedding)")
async def create_embeddings(request: EmbeddingRequest):
    require_models(["bge_embedding"])
    if not request.sentences:
        raise HTTPException(status_code=400, detail="句子列表不能為空")
        
    result = await process_request('embedding', request.dict())
    return {"embeddings": result}

@router.get("/health")
def health_check():
    return {
        "healthy": model_container.models_loaded,
        "models": {
            "reranker_loaded": model_container.reranker is not None,
            "embedding_loaded": model_container.embedding_model is not None,
        },
        "queue_size": request_queue.qsize()
    }
