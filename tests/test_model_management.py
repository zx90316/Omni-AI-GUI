"""Unit tests for model registry and cache checks; never download weights."""
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from backend.model_cache import (
    get_hf_cache_dir,
    get_paddlex_cache_dir,
    inspect_model_cache,
    inspect_paddlex_model,
)
from backend.model_registry import MODEL_SPECS, MODEL_SPECS_BY_KEY
from manager.model_manager import (
    EVENT_PREFIX,
    ModelDownloadController,
    download_models,
    parse_worker_event,
)
from manager.model_downloader import (
    _DownloadProgress,
    _snapshot_root_from_plan,
    download_model,
)


class ModelCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _repo(self, model_id="org/model"):
        return self.cache_dir / f"models--{model_id.replace('/', '--')}"

    def test_missing_model(self):
        status = inspect_model_cache("org/model", self.cache_dir)
        self.assertEqual(status.state, "missing")
        self.assertFalse(status.cached)

    def test_valid_main_snapshot_is_ready(self):
        repo = self._repo()
        snapshot = repo / "snapshots" / "abc123"
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}", encoding="utf-8")
        (repo / "refs").mkdir()
        (repo / "refs" / "main").write_text("abc123\n", encoding="utf-8")

        status = inspect_model_cache("org/model", self.cache_dir)

        self.assertTrue(status.cached)
        self.assertEqual(status.state, "ready")
        self.assertEqual(status.revision, "abc123")

    def test_incomplete_blob_is_not_reported_as_ready(self):
        repo = self._repo()
        snapshot = repo / "snapshots" / "abc123"
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}", encoding="utf-8")
        (repo / "blobs").mkdir()
        (repo / "blobs" / "weights.incomplete").write_bytes(b"partial")

        status = inspect_model_cache("org/model", self.cache_dir)

        self.assertEqual(status.state, "partial")
        self.assertFalse(status.cached)

    def test_yaml_component_references_require_component_weights(self):
        repo = self._repo()
        snapshot = repo / "snapshots" / "abc123"
        snapshot.mkdir(parents=True)
        (snapshot / "config.yaml").write_text(
            "embedding: $model/embedding\nsegmentation: $model/segmentation\n",
            encoding="utf-8",
        )
        (snapshot / "embedding").mkdir()
        (snapshot / "embedding" / "pytorch_model.bin").write_bytes(b"weights")
        (repo / "refs").mkdir()
        (repo / "refs" / "main").write_text("abc123", encoding="utf-8")

        self.assertEqual(
            inspect_model_cache("org/model", self.cache_dir).state,
            "partial",
        )
        (snapshot / "segmentation").mkdir()
        (snapshot / "segmentation" / "model.safetensors").write_bytes(b"weights")
        self.assertEqual(
            inspect_model_cache("org/model", self.cache_dir).state,
            "ready",
        )

    def test_unreferenced_incomplete_blob_does_not_hide_complete_snapshot(self):
        repo = self._repo()
        snapshot = repo / "snapshots" / "abc123"
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}", encoding="utf-8")
        (snapshot / "model.safetensors").write_bytes(b"complete weights")
        (repo / "refs").mkdir()
        (repo / "refs" / "main").write_text("abc123", encoding="utf-8")
        (repo / "blobs").mkdir()
        (repo / "blobs" / "stale.incomplete").write_bytes(b"unused")

        status = inspect_model_cache("org/model", self.cache_dir)

        self.assertTrue(status.cached)
        self.assertEqual(status.state, "ready")
        self.assertIn("忽略", status.detail)

    def test_broken_main_ref_is_partial_even_with_old_snapshot(self):
        repo = self._repo()
        snapshot = repo / "snapshots" / "old123"
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}", encoding="utf-8")
        (repo / "refs").mkdir()
        (repo / "refs" / "main").write_text("missing456", encoding="utf-8")

        status = inspect_model_cache("org/model", self.cache_dir)

        self.assertEqual(status.state, "partial")
        self.assertFalse(status.cached)
        self.assertIn("main revision", status.detail)

    def test_cache_environment_precedence(self):
        env = {
            "HF_HOME": "C:/hf-home",
            "HUGGINGFACE_HUB_CACHE": "C:/legacy-cache",
            "HF_HUB_CACHE": "C:/current-cache",
        }
        self.assertEqual(get_hf_cache_dir(env), Path("C:/current-cache"))
        del env["HF_HUB_CACHE"]
        self.assertEqual(get_hf_cache_dir(env), Path("C:/legacy-cache"))
        del env["HUGGINGFACE_HUB_CACHE"]
        self.assertEqual(get_hf_cache_dir(env), Path("C:/hf-home") / "hub")

    def test_paddlex_model_requires_config_and_weights(self):
        model_dir = self.cache_dir / "PP-DocLayoutV3"
        model_dir.mkdir()
        (model_dir / "inference.yml").write_text("model: layout", encoding="utf-8")
        self.assertEqual(
            inspect_paddlex_model("PP-DocLayoutV3", self.cache_dir).state,
            "partial",
        )
        (model_dir / "inference.pdiparams").write_bytes(b"weights")
        status = inspect_paddlex_model("PP-DocLayoutV3", self.cache_dir)
        self.assertTrue(status.cached)
        self.assertEqual(status.state, "ready")

    def test_paddlex_cache_environment(self):
        resolved = get_paddlex_cache_dir({"PADDLE_PDX_CACHE_HOME": "C:/paddlex"})
        self.assertEqual(resolved, Path("C:/paddlex") / "official_models")


class ModelRegistryTests(unittest.TestCase):
    def test_windows_symlink_failure_falls_back_to_regular_files(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            snapshot = Path(cache_dir) / "snapshots" / "abc123"
            item = types.SimpleNamespace(
                commit_hash="abc123",
                local_path=str(snapshot / "config.json"),
                filename="config.json",
                file_size=2,
                is_cached=False,
            )
            symlink_error = OSError("symlink privilege is unavailable")
            symlink_error.winerror = 1314

            def fake_snapshot_download(**kwargs):
                if kwargs.get("dry_run"):
                    return [item]
                if kwargs.get("local_files_only"):
                    self.assertTrue((snapshot / "config.json").is_file())
                    return str(snapshot)
                if kwargs.get("local_dir"):
                    local_dir = Path(kwargs["local_dir"])
                    local_dir.mkdir(parents=True, exist_ok=True)
                    (local_dir / "config.json").write_text("{}", encoding="utf-8")
                    return str(local_dir)
                raise symlink_error

            fake_hub = types.ModuleType("huggingface_hub")
            fake_hub.snapshot_download = fake_snapshot_download
            fake_tqdm_auto = types.ModuleType("tqdm.auto")
            fake_tqdm_auto.tqdm = type("FakeTqdm", (), {"update": lambda self, n=1: None})

            with (
                patch.dict(
                    sys.modules,
                    {
                        "huggingface_hub": fake_hub,
                        "tqdm.auto": fake_tqdm_auto,
                    },
                ),
                patch("manager.model_downloader.emit_event") as emit,
            ):
                result = download_model("org/model")

            self.assertEqual(result, snapshot)
            self.assertEqual((snapshot / "config.json").read_text(encoding="utf-8"), "{}")
            phases = [
                call.kwargs.get("phase")
                for call in emit.call_args_list
                if call.args == ("phase",)
            ]
            self.assertIn("windows_copy_fallback", phases)
            self.assertIn("verify", phases)

    def test_snapshot_root_is_resolved_from_nested_plan_path(self):
        item = types.SimpleNamespace(
            commit_hash="abc123",
            local_path="C:/cache/repo/snapshots/abc123/subdir/model.bin",
        )
        self.assertEqual(
            _snapshot_root_from_plan([item]),
            Path("C:/cache/repo/snapshots/abc123"),
        )

    def test_download_progress_reserves_100_for_verified_snapshot(self):
        progress = _DownloadProgress("org/model", total=100, completed=250)
        with patch("manager.model_downloader.emit_event") as emit:
            progress.emit()
            progress.emit(force_complete=True)

        first = emit.call_args_list[0].kwargs
        final = emit.call_args_list[1].kwargs
        self.assertEqual(first["completed_bytes"], 100)
        self.assertEqual(first["percent"], 99.0)
        self.assertEqual(final["completed_bytes"], 100)
        self.assertEqual(final["percent"], 100.0)

    def test_registry_keys_and_ids_are_unique(self):
        keys = [spec.key for spec in MODEL_SPECS]
        ids = [spec.model_id for spec in MODEL_SPECS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(keys), set(MODEL_SPECS_BY_KEY))
        self.assertEqual(MODEL_SPECS_BY_KEY["pp_doclayout"].source, "paddlex")
        self.assertEqual(MODEL_SPECS_BY_KEY["pp_doclayout"].model_id, "PP-DocLayoutV3")

    @patch("manager.model_manager.is_venv_exists", return_value=False)
    def test_download_requires_usable_venv(self, _is_venv):
        output = []
        result = download_models(["clip"], on_output=output.append)
        self.assertFalse(result)
        self.assertTrue(any(".venv" in line for line in output))

    @patch("manager.model_manager.is_venv_exists", return_value=True)
    def test_paddlex_download_dispatches_to_provider_worker(self, _is_venv):
        class FakeProcess:
            stdout = iter([
                EVENT_PREFIX + '{"type":"start","model_id":"PP-DocLayoutV3"}\n',
                "downloaded\n",
            ])

            @staticmethod
            def wait():
                return 0

        missing = inspect_model_cache("missing/model", Path("Z:/does-not-exist"))
        ready = missing.__class__(
            "PP-DocLayoutV3", "ready", True, "ok", snapshot_path="cache/model"
        )
        commands = []
        events = []

        def fake_popen(cmd, **_kwargs):
            commands.append(cmd)
            return FakeProcess()

        with (
            patch("manager.model_manager._inspect", side_effect=[missing, ready]),
            patch("manager.model_manager.subprocess.Popen", side_effect=fake_popen),
        ):
            self.assertTrue(download_models(["pp_doclayout"], on_event=events.append))

        self.assertEqual(commands[0][-2:], ["--source", "paddlex"])
        self.assertEqual(events[0]["type"], "start")
        self.assertEqual(events[0]["key"], "pp_doclayout")

    def test_worker_event_parser_rejects_non_events(self):
        self.assertIsNone(parse_worker_event("ordinary output"))
        self.assertIsNone(parse_worker_event(EVENT_PREFIX + "not-json"))
        event = parse_worker_event(EVENT_PREFIX + '{"type":"progress","percent":50}')
        self.assertEqual(event, {"type": "progress", "percent": 50})

    def test_download_controller_terminates_active_worker(self):
        class FakeProcess:
            terminated = False

            @staticmethod
            def poll():
                return None

            def terminate(self):
                self.terminated = True

        controller = ModelDownloadController()
        process = FakeProcess()
        self.assertTrue(controller.begin())
        controller.attach(process)
        self.assertTrue(controller.cancel())
        self.assertTrue(process.terminated)
        self.assertTrue(controller.is_cancelled())
        controller.finish()
        self.assertFalse(controller.is_active())


if __name__ == "__main__":
    unittest.main()
