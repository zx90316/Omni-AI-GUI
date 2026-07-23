# -*- coding: utf-8 -*-
"""
SQLAlchemy 資料庫模型與連線設定
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, Text, DateTime,
    inspect, text,
)
from sqlalchemy.orm import sessionmaker, declarative_base

# ── 資料庫路徑 ──
DB_DIR = Path(__file__).resolve().parent.parent
DB_PATH = DB_DIR / "omni_ai.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    """使用者模型"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    verification_code = Column(String(6), nullable=True)
    code_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Task(Base):
    """Persistent inference task shared by ASR and OCR workflows."""
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    owner_id = Column(String(255), index=True, nullable=False, default="guest") # email or guest UUID
    task_type = Column(String(20), default="local") # "local", "youtube", or "subsync_upload"
    video_id = Column(String(50), nullable=True) # for youtube or subsync uploads
    filename = Column(String(255), nullable=False)
    status = Column(String(20), default="pending")  # pending / processing / cancelling / completed / failed / cancelled
    model = Column(String(100), nullable=False)
    language = Column(String(50), nullable=False)
    enable_diarization = Column(Boolean, default=True)
    to_traditional = Column(Boolean, default=True)
    progress = Column(Float, default=0.0)
    progress_message = Column(String(255), default="等待中")
    raw_text = Column(Text, nullable=True)
    chars = Column(Text, nullable=True)             # JSON string of raw character timestamps
    sentences = Column(Text, nullable=True)         # JSON string — 字幕短句
    diarization_result = Column(Text, nullable=True) # JSON string — 語者歸組段落
    diar_segments = Column(Text, nullable=True)     # JSON string — 語者分離原始區段
    task_options = Column(Text, nullable=True)      # JSON — task-type-specific input options
    result_data = Column(Text, nullable=True)       # JSON — task-type-specific persisted result
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)


    def set_chars(self, data):
        self.chars = json.dumps(data, ensure_ascii=False) if data else None

    def get_chars(self):
        return json.loads(self.chars) if self.chars else []

    def set_sentences(self, data):
        self.sentences = json.dumps(data, ensure_ascii=False) if data else None

    def get_sentences(self):
        return json.loads(self.sentences) if self.sentences else []

    def set_diarization_result(self, data):
        self.diarization_result = json.dumps(data, ensure_ascii=False) if data else None

    def get_diarization_result(self):
        return json.loads(self.diarization_result) if self.diarization_result else None

    def set_diar_segments(self, data):
        self.diar_segments = json.dumps(data, ensure_ascii=False) if data else None

    def get_diar_segments(self):
        return json.loads(self.diar_segments) if self.diar_segments else []

    def set_task_options(self, data):
        self.task_options = json.dumps(data, ensure_ascii=False) if data else None

    def get_task_options(self):
        return json.loads(self.task_options) if self.task_options else None

    def set_result_data(self, data):
        self.result_data = json.dumps(data, ensure_ascii=False) if data is not None else None

    def get_result_data(self):
        return json.loads(self.result_data) if self.result_data else None



def get_db():
    """FastAPI dependency — 取得 DB session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化資料庫（建立資料表）"""
    Base.metadata.create_all(bind=engine)
    # create_all does not add columns to an existing SQLite table. Keep this
    # narrow migration in-process so upgrades preserve all prior ASR tasks.
    columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
    additions = {
        "task_options": "TEXT",
        "result_data": "TEXT",
    }
    with engine.begin() as connection:
        for column_name, sql_type in additions.items():
            if column_name not in columns:
                connection.execute(
                    text(f"ALTER TABLE tasks ADD COLUMN {column_name} {sql_type}")
                )


def fail_interrupted_tasks() -> int:
    """將上次程序遺留的待處理任務標記為失敗，避免前端永久等待。"""
    db = SessionLocal()
    try:
        interrupted = db.query(Task).filter(
            Task.status.in_(("pending", "processing", "cancelling"))
        ).all()
        if not interrupted:
            return 0

        now = datetime.now(timezone.utc)
        for task in interrupted:
            if task.status == "cancelling":
                task.status = "cancelled"
                task.progress_message = "已取消（服務重啟）"
                task.error_message = None
            else:
                task.status = "failed"
                task.progress_message = "服務重啟，任務已中斷"
                task.error_message = "背景處理程序在任務完成前重新啟動，請重新提交任務。"
            task.completed_at = now
        db.commit()
        return len(interrupted)
    finally:
        db.close()
