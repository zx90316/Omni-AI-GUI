# -*- coding: utf-8 -*-
"""HTTP contract tests for ASR upload, ownership, cancellation, and cleanup."""
from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi import FastAPI
from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message=r"Using `httpx` with `starlette\.testclient` is deprecated.*",
    category=StarletteDeprecationWarning,
)

from starlette.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.asr_control import clear_cancel_event
from backend.asr_engine import ASREngine
from backend.auth_utils import get_current_user
from backend.database import Base, Task, get_db
from backend.routers import tasks as tasks_router
from backend.routers import youtube as youtube_router


class ASRApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.upload_dir = Path(self.temp_dir.name)
        self.owner = {"owner_id": "owner-a", "role": "user"}
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

        app = FastAPI()
        app.include_router(tasks_router.router)
        app.include_router(youtube_router.router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: self.owner
        self.client = TestClient(app)
        self.patches = [
            patch.object(tasks_router, "UPLOAD_DIR", self.upload_dir),
            patch.object(tasks_router, "_start_asr_thread", return_value=None),
            patch.object(youtube_router, "TEMP_DIR", self.upload_dir),
            patch.object(youtube_router, "_start_youtube_thread", return_value=None),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        with self.Session() as db:
            for task_id, in db.query(Task.id).all():
                clear_cancel_event(task_id)
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp_dir.cleanup()

    def create_valid_task(self):
        response = self.client.post(
            "/api/tasks",
            files={"file": ("sample.wav", b"RIFF-fake-audio", "audio/wav")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_rejects_unsupported_and_empty_uploads_without_artifacts(self):
        unsupported = self.client.post(
            "/api/tasks",
            files={"file": ("notes.txt", b"not audio", "text/plain")},
        )
        self.assertEqual(unsupported.status_code, 400)

        empty = self.client.post(
            "/api/tasks",
            files={"file": ("empty.wav", b"", "audio/wav")},
        )
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_rejects_oversize_upload_and_removes_partial_file(self):
        with patch.object(tasks_router, "ASR_MAX_UPLOAD_MB", 0):
            response = self.client.post(
                "/api/tasks",
                files={"file": ("large.wav", b"x", "audio/wav")},
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_selected_time_range_is_forwarded_to_background_task(self):
        with patch.object(tasks_router, "_start_asr_thread") as start_thread:
            response = self.client.post(
                "/api/tasks",
                files={"file": ("sample.wav", b"RIFF-fake-audio", "audio/wav")},
                data={"start_time": "12.5", "end_time": "47.25"},
            )

        self.assertEqual(response.status_code, 201, response.text)
        args = start_thread.call_args.args[0]
        self.assertEqual(args[-2:], (12.5, 47.25))

    def test_audio_conversion_trims_selected_range_to_sample_precision(self):
        from backend.audio_utils import convert_to_wav

        sample_rate = 16000
        source = self.upload_dir / "source.wav"
        selected = self.upload_dir / "selected.wav"
        samples = np.linspace(-0.5, 0.5, sample_rate * 5, dtype=np.float32)
        sf.write(source, samples, sample_rate)

        convert_to_wav(
            str(source),
            str(selected),
            start_time=1.25,
            end_time=3.75,
        )

        selected_samples, _ = sf.read(selected, dtype="float32")
        source_samples, _ = sf.read(source, dtype="float32")
        expected = source_samples[round(1.25 * sample_rate):round(3.75 * sample_rate)]
        self.assertAlmostEqual(sf.info(selected).duration, 2.5, places=3)
        np.testing.assert_allclose(selected_samples, expected, atol=1 / 32768)

    def test_rejects_invalid_time_range_before_saving_upload(self):
        response = self.client.post(
            "/api/tasks",
            files={"file": ("sample.wav", b"RIFF-fake-audio", "audio/wav")},
            data={"start_time": "30", "end_time": "10"},
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_cancel_is_idempotent_and_active_task_cannot_be_deleted(self):
        task = self.create_valid_task()
        first = self.client.post(f"/api/tasks/{task['id']}/cancel")
        second = self.client.post(f"/api/tasks/{task['id']}/cancel")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["status"], "cancelling")
        self.assertEqual(second.status_code, 200, second.text)

        delete = self.client.delete(f"/api/tasks/{task['id']}")
        self.assertEqual(delete.status_code, 409)

        with self.Session() as db:
            stored = db.get(Task, task["id"])
            self.assertEqual(stored.status, "cancelling")

    def test_media_endpoint_enforces_task_ownership(self):
        task = self.create_valid_task()
        owned = self.client.get(f"/api/tasks/media/{task['video_id']}")
        self.assertEqual(owned.status_code, 200)

        self.owner = {"owner_id": "owner-b", "role": "user"}
        denied = self.client.get(f"/api/tasks/media/{task['video_id']}")
        self.assertEqual(denied.status_code, 404)

    def test_youtube_task_can_be_cancelled_before_download_starts(self):
        created = self.client.post(
            "/api/youtube/analyze",
            json={
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "model": "1.7B (高品質)",
                "language": "中文",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        task_id = created.json()["id"]

        cancelled = self.client.post(f"/api/youtube/{task_id}/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelling")

        with self.Session() as db:
            self.assertEqual(db.get(Task, task_id).status, "cancelling")

    def test_long_audio_silence_detection_streams_and_finds_boundaries(self):
        sample_rate = 1000
        duration = 130
        audio = np.full(sample_rate * duration, 0.1, dtype=np.float32)
        audio[58 * sample_rate:62 * sample_rate] = 0.0
        audio[118 * sample_rate:122 * sample_rate] = 0.0
        audio_path = self.upload_dir / "long.wav"
        sf.write(audio_path, audio, sample_rate)

        engine = ASREngine.__new__(ASREngine)
        engine.on_progress = lambda percent, message: None
        engine.should_cancel = lambda: False
        engine._deadline = None
        chunks = engine.split_audio_by_silence(
            str(audio_path),
            target_duration=60,
            max_duration=90,
        )

        self.assertEqual(len(chunks), 3)
        self.assertAlmostEqual(chunks[0][1], 60.0, delta=1.0)
        self.assertAlmostEqual(chunks[1][1], 120.0, delta=1.0)
        self.assertEqual(chunks[-1][1], 130.0)


if __name__ == "__main__":
    unittest.main()
