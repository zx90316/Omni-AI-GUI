# -*- coding: utf-8 -*-
"""ASR 資源隔離、重試與進度容錯的回歸測試。"""
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")

from backend.asr_control import (
    active_control_count,
    clear_cancel_event,
    get_or_create_cancel_event,
    request_cancel,
)
from backend.asr_engine import (
    ASRCancelledError,
    ASREngine,
    ASRTimeoutError,
    _ASR_RUN_LOCK,
)


class ASRStabilityTests(unittest.TestCase):
    def make_engine(self, transcribe_hook=None):
        engine = ASREngine.__new__(ASREngine)
        engine._model = None
        engine.on_progress = lambda percent, message: None
        engine.should_cancel = lambda: False
        engine.timeout_seconds = None
        engine._deadline = None
        engine.load_model = lambda: None
        engine.unload_model = lambda: None
        engine.transcribe = transcribe_hook or (
            lambda path, language="Chinese": [types.SimpleNamespace(text="測試")]
        )
        engine.merge = lambda *args, **kwargs: ([{"start": 0, "end": 1, "text": "測試"}], [])
        return engine

    def test_progress_callback_failure_does_not_abort_asr(self):
        engine = self.make_engine()
        engine.on_progress = lambda percent, message: (_ for _ in ()).throw(
            RuntimeError("database locked")
        )

        engine._progress(25, "轉錄中")

    def test_transcribe_retries_once_then_succeeds(self):
        engine = self.make_engine()
        calls = []

        def transcribe_single(path, language):
            calls.append(path)
            if len(calls) == 1:
                raise RuntimeError("temporary inference failure")
            return ["ok"]

        engine._transcribe_single = transcribe_single
        with patch("backend.asr_engine.time.sleep", return_value=None):
            result = engine._transcribe_with_retry("chunk.wav", "Chinese")

        self.assertEqual(result, ["ok"])
        self.assertEqual(len(calls), 2)

    def test_hf_native_transcription_and_forced_alignment_contract(self):
        observed = {}

        class FakeTensor:
            shape = (1, 3)

            def __getitem__(self, key):
                observed["generated_slice"] = key
                return self

        class FakeBatch(dict):
            def to(self, device, dtype):
                observed.setdefault("moves", []).append((device, dtype))
                return self

        class FakeASRProcessor:
            def apply_transcription_request(self, **kwargs):
                observed["transcription_request"] = kwargs
                return FakeBatch(input_ids=FakeTensor())

            def decode(self, generated_ids, return_format):
                observed["decode_format"] = return_format
                return [{"language": "Chinese", "transcription": "測試文字"}]

        class FakeAlignerProcessor:
            def prepare_forced_aligner_inputs(self, **kwargs):
                observed["aligner_request"] = kwargs
                return FakeBatch(input_ids=FakeTensor()), [["測", "試"]]

            def decode_forced_alignment(self, **kwargs):
                observed["alignment_decode"] = kwargs
                return [[
                    {"text": "測", "start_time": 0.1, "end_time": 0.2},
                    {"text": "試", "start_time": 0.2, "end_time": 0.3},
                ]]

        class FakeASRModel:
            device = "cuda:0"
            dtype = "bfloat16"

            def generate(self, **kwargs):
                observed["generate"] = kwargs
                return FakeTensor()

        class FakeAlignerModel:
            device = "cuda:0"
            dtype = "bfloat16"
            config = types.SimpleNamespace(
                timestamp_token_id=99,
                timestamp_segment_time=80,
            )

            def __call__(self, **kwargs):
                observed["aligner_model"] = kwargs
                return types.SimpleNamespace(logits="logits")

        fake_torch = types.ModuleType("torch")
        fake_torch.inference_mode = nullcontext

        engine = ASREngine.__new__(ASREngine)
        engine._processor = FakeASRProcessor()
        engine._model = FakeASRModel()
        engine._aligner_processor = FakeAlignerProcessor()
        engine._aligner_model = FakeAlignerModel()
        engine.max_new_tokens = 512

        with patch.dict(sys.modules, {"torch": fake_torch}):
            results = engine._transcribe_single("sample.wav", "Chinese")

        self.assertEqual(results[0].language, "Chinese")
        self.assertEqual(results[0].text, "測試文字")
        self.assertEqual([item.text for item in results[0].time_stamps], ["測", "試"])
        self.assertEqual(
            observed["transcription_request"],
            {"audio": "sample.wav", "language": "Chinese"},
        )
        self.assertEqual(observed["decode_format"], "parsed")
        self.assertEqual(observed["generate"]["max_new_tokens"], 512)
        self.assertFalse(observed["generate"]["do_sample"])
        self.assertEqual(observed["aligner_request"]["transcript"], "測試文字")
        self.assertEqual(observed["alignment_decode"]["timestamp_token_id"], 99)

    def test_concurrent_runs_are_serialized_and_use_isolated_workspaces(self):
        active = 0
        max_active = 0
        state_lock = threading.Lock()
        converted_paths = []
        errors = []

        def transcribe_hook(path, language="Chinese"):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return [types.SimpleNamespace(text="測試")]

        with tempfile.TemporaryDirectory() as result_root:
            engines = [self.make_engine(transcribe_hook), self.make_engine(transcribe_hook)]

            audio_module = types.ModuleType("backend.audio_utils")

            def convert_to_wav(input_path, output_path, should_cancel=None):
                output = Path(output_path)
                output.write_bytes(b"fake wav")
                converted_paths.append(output)
                return str(output)

            audio_module.convert_to_wav = convert_to_wav
            config_module = types.ModuleType("backend.config")
            config_module.RESULT_DIR = Path(result_root)

            def worker(engine):
                try:
                    engine.run(
                        "input.mp3",
                        enable_diarization=False,
                        to_traditional=False,
                    )
                except Exception as exc:  # pragma: no cover - included in assertion output
                    errors.append(exc)

            with patch.dict(
                sys.modules,
                {"backend.audio_utils": audio_module, "backend.config": config_module},
            ):
                threads = [threading.Thread(target=worker, args=(engine,)) for engine in engines]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=2)

            self.assertFalse(errors)
            self.assertEqual(max_active, 1)
            self.assertEqual(len(converted_paths), 2)
            self.assertNotEqual(converted_paths[0].parent, converted_paths[1].parent)
            self.assertTrue(all(not path.parent.exists() for path in converted_paths))

    def test_cancel_event_registry_is_idempotent_and_cleans_up(self):
        task_id = 987654
        clear_cancel_event(task_id)
        event = get_or_create_cancel_event(task_id)
        self.assertFalse(event.is_set())
        self.assertTrue(request_cancel(task_id))
        self.assertFalse(request_cancel(task_id))
        self.assertIs(event, get_or_create_cancel_event(task_id))
        self.assertTrue(event.is_set())
        clear_cancel_event(task_id)
        self.assertEqual(active_control_count(), 0)

    def test_cancelled_task_stops_while_waiting_for_global_lock(self):
        engine = self.make_engine()
        cancel_event = threading.Event()
        engine.should_cancel = cancel_event.is_set
        errors = []

        _ASR_RUN_LOCK.acquire()
        try:
            worker = threading.Thread(
                target=lambda: self._capture_run_error(engine, errors),
            )
            worker.start()
            time.sleep(0.05)
            cancel_event.set()
            worker.join(timeout=1)
        finally:
            _ASR_RUN_LOCK.release()

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ASRCancelledError)

    def test_task_times_out_while_waiting_for_global_lock(self):
        engine = self.make_engine()
        engine.timeout_seconds = 0.05
        errors = []

        _ASR_RUN_LOCK.acquire()
        try:
            worker = threading.Thread(
                target=lambda: self._capture_run_error(engine, errors),
            )
            worker.start()
            worker.join(timeout=1)
        finally:
            _ASR_RUN_LOCK.release()

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ASRTimeoutError)

    @staticmethod
    def _capture_run_error(engine, errors):
        try:
            engine.run("input.mp3", enable_diarization=False, to_traditional=False)
        except Exception as exc:
            errors.append(exc)


if __name__ == "__main__":
    unittest.main()
