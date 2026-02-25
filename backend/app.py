# -*- coding: utf-8 -*-
"""
FastAPI 應用入口
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_db
from backend.routers.tasks import router as tasks_router
from backend.routers.youtube import router as youtube_router

app = FastAPI(
    title="Qwen ASR API",
    description="語音辨識 API — 基於 Qwen ASR 模型",
    version="1.0.0",
)

# ── CORS 設定 ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由掛載 ──
app.include_router(tasks_router)
app.include_router(youtube_router)


@app.on_event("startup")
def startup():
    """啟動時初始化資料庫"""
    init_db()


@app.get("/")
def root():
    return {"message": "Qwen ASR API is running", "docs": "/docs"}
