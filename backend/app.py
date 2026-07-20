# -*- coding: utf-8 -*-
"""
FastAPI 應用入口
"""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.database import fail_interrupted_tasks, init_db
from backend.auth_utils import get_current_user
from backend.routers.tasks import router as tasks_router
from backend.routers.youtube import router as youtube_router
from backend.routers.auth import router as auth_router
from backend.routers.ocr import router as ocr_router
from backend.routers.clip_search import router as clip_search_router
from backend.routers.workflow import router as workflow_router
from backend.routers.semantic import router as semantic_router
from backend.routers.system import router as system_router
from backend.semantic_engine import init_semantic_models, start_worker, stop_worker_and_cleanup
from backend.network_utils import set_hf_offline_env

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_application: FastAPI):
    """Run the existing startup/shutdown lifecycle without deprecated events."""
    await startup()
    try:
        yield
    finally:
        shutdown()


app = FastAPI(
    title="Omni AI API",
    description="多模態語音/視覺/語意操作 API — 基於 Qwen / BGE / Clip",
    version="2.1.0",
    lifespan=lifespan,
)
app.state.ready = False
app.state.started_at = time.time()
app.state.semantic_status = "not_started"

# ── CORS 設定 ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由掛載（需驗證）──
# All inference endpoints can consume substantial CPU/GPU resources.  Keep
# only authentication, health checks, and the read-only system status public.
auth_required = [Depends(get_current_user)]
app.include_router(tasks_router, dependencies=auth_required)
app.include_router(youtube_router, dependencies=auth_required)
app.include_router(ocr_router, dependencies=auth_required)
app.include_router(clip_search_router, dependencies=auth_required)
app.include_router(workflow_router, dependencies=auth_required)
app.include_router(semantic_router, dependencies=auth_required)

# ── 路由掛載（無需驗證）──
app.include_router(auth_router)
app.include_router(system_router)


async def startup():
    """Initialize storage and load only models already installed by Manager."""
    app.state.ready = False
    init_db()
    interrupted_count = fail_interrupted_tasks()
    if interrupted_count:
        logger.warning("已將 %d 個因服務重啟中斷的任務標記為失敗", interrupted_count)

    # Manager exclusively owns model downloads. Inference must remain local-only
    # so a feature invocation cannot silently start a network download.
    set_hf_offline_env()
    logger.info("模型載入已設為 local-only；缺少模型時請使用 Manager 下載")

    import asyncio
    
    async def load_bge():
        app.state.semantic_status = "loading"
        try:
            initialized = await asyncio.to_thread(init_semantic_models)
            if initialized:
                start_worker()
                app.state.semantic_status = "ready"
            else:
                app.state.semantic_status = "unavailable"
        except Exception:
            app.state.semantic_status = "error"
            logger.exception("語意模型背景初始化失敗")
        
    asyncio.create_task(load_bge())
    app.state.ready = True

def shutdown():
    app.state.ready = False
    stop_worker_and_cleanup()


@app.get("/health/live", tags=["health"])
def health_live():
    """Process-level liveness. A response proves the event loop is serving requests."""
    return {
        "status": "alive",
        "uptime_seconds": round(max(0.0, time.time() - app.state.started_at), 3),
    }


@app.get("/health/ready", tags=["health"])
def health_ready():
    """Core readiness; optional semantic model loading is reported separately."""
    payload = {
        "status": "ready" if app.state.ready else "starting",
        "semantic": app.state.semantic_status,
    }
    if not app.state.ready:
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/")
def root():
    return {"message": "Omni AI API is running", "docs": "/docs"}
