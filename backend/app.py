# -*- coding: utf-8 -*-
"""
FastAPI 應用入口
"""
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_db
from backend.auth_utils import get_current_user
from backend.routers.tasks import router as tasks_router
from backend.routers.youtube import router as youtube_router
from backend.routers.llm import router as llm_router
from backend.routers.auth import router as auth_router
from backend.routers.ocr import router as ocr_router
from backend.routers.clip_search import router as clip_search_router
from backend.routers.workflow import router as workflow_router
from backend.routers.semantic import router as semantic_router
from backend.semantic_engine import init_semantic_models, start_worker, stop_worker_and_cleanup

app = FastAPI(
    title="Omni AI API",
    description="多模態語音/視覺/語意操作 API — 基於 Qwen / BGE / Clip",
    version="2.1.0",
)

# ── CORS 設定 ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由掛載（需驗證）──
auth_required = [Depends(get_current_user)]
app.include_router(tasks_router, dependencies=auth_required)
app.include_router(youtube_router, dependencies=auth_required)

# ── 路由掛載（無需驗證）──
app.include_router(llm_router)
app.include_router(auth_router)
app.include_router(ocr_router)
app.include_router(clip_search_router)
app.include_router(workflow_router)
app.include_router(semantic_router)


@app.on_event("startup")
async def startup():
    """啟動時初始化資料庫與語意模型背景程序"""
    init_db()
    # 非同步啟動 worker，但在這之前需要將模型載入（如果需要事先載入的話）
    # Semantic API 若未安裝 FlagEmbedding，會跳過載入不影響主體
    import asyncio
    
    async def load_bge():
        # 模型載入是同步且耗時的，放入 thread pool 執行
        await asyncio.to_thread(init_semantic_models)
        # 載入完成後，在目前的 event loop 啟動 worker
        start_worker()
        
    asyncio.create_task(load_bge())

@app.on_event("shutdown")
def shutdown():
    stop_worker_and_cleanup()


@app.get("/")
def root():
    return {"message": "Omni AI API is running", "docs": "/docs"}
