"""Fast lifecycle tests for Manager configuration and process supervision."""
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from manager import config as manager_config
from manager import env_manager
from manager.process_manager import (
    ManagedProcess,
    ProcessManager,
    ProcessStatus,
    _health_url,
    _service_creation_flags,
)
from manager.env_schema import (
    EnvValidationResult,
    initial_env_values,
    validate_env_file,
    validate_env_values,
)
from launch import get_project_override, is_project_directory
from manager import git_manager


class ConfigLifecycleTests(unittest.TestCase):
    def test_normalize_rejects_invalid_runtime_values(self):
        normalized = manager_config._normalize_config(
            {
                "backend_port": 0,
                "frontend_port": 70000,
                "health_check_interval": "fast",
                "health_probe_timeout": 0,
                "health_failure_threshold": 99,
                "startup_timeout": 1,
                "auto_restart": "yes",
                "frontend_host": "  localhost  ",
            }
        )
        self.assertEqual(normalized["backend_port"], 8000)
        self.assertEqual(normalized["frontend_port"], 5173)
        self.assertEqual(normalized["health_check_interval"], 5)
        self.assertEqual(normalized["health_probe_timeout"], 2)
        self.assertEqual(normalized["health_failure_threshold"], 3)
        self.assertEqual(normalized["startup_timeout"], 60)
        self.assertIs(normalized["auto_restart"], True)
        self.assertEqual(normalized["frontend_host"], "localhost")

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "manager_config.json"
            with patch.object(manager_config, "CONFIG_FILE", config_file):
                self.assertTrue(manager_config.save_config({"backend_port": 8123}))
                loaded = manager_config.load_config()
        self.assertEqual(loaded["backend_port"], 8123)
        self.assertFalse(config_file.with_suffix(".json.tmp").exists())


class EnvConfigurationTests(unittest.TestCase):
    def test_empty_fields_receive_safe_form_defaults_and_generated_secret(self):
        values = initial_env_values({})
        self.assertEqual(values["OCR_PROVIDER"], "local")
        self.assertEqual(values["OCR_MAX_WORKERS"], "8")
        self.assertEqual(values["ASR_TASK_TIMEOUT_SECONDS"], "14400")
        self.assertGreaterEqual(len(values["SECRET_KEY"]), 32)
        self.assertEqual(values["SMTP_USER"], "")

    def test_complete_values_pass_validation(self):
        values = initial_env_values(
            {
                "SMTP_USER": "owner@example.com",
                "SMTP_PASSWORD": "application-password",
            }
        )
        result = validate_env_values(values)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(values["SMTP_FROM_EMAIL"], "owner@example.com")

    def test_missing_personal_values_block_startup(self):
        result = validate_env_values(initial_env_values({}))
        self.assertFalse(result.valid)
        self.assertIn("SMTP_USER", result.missing_keys)
        self.assertIn("SMTP_PASSWORD", result.missing_keys)

    def test_openai_ocr_requires_api_url(self):
        values = initial_env_values(
            {
                "OCR_PROVIDER": "openai",
                "SMTP_USER": "owner@example.com",
                "SMTP_PASSWORD": "application-password",
            }
        )
        result = validate_env_values(values)
        self.assertFalse(result.valid)
        self.assertIn("OCR_API_URL", result.missing_keys)

    def test_missing_env_file_is_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = validate_env_file(Path(temp_dir) / ".env")
        self.assertFalse(result.valid)
        self.assertIn("SECRET_KEY", result.missing_keys)


class BootstrapLifecycleTests(unittest.TestCase):
    def test_project_override_prefers_explicit_argument(self):
        resolved = get_project_override(
            ["--project-dir", "C:/explicit"],
            {"OMNI_AI_PROJECT_ROOT": "C:/environment"},
        )
        self.assertEqual(resolved, Path("C:/explicit").resolve())

    def test_project_validation_requires_complete_core_tree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manager").mkdir()
            (root / "manager" / "app.py").touch()
            self.assertFalse(is_project_directory(root))
            (root / "backend").mkdir()
            (root / "backend" / "app.py").touch()
            (root / "frontend").mkdir()
            (root / "frontend" / "package.json").touch()
            self.assertTrue(is_project_directory(root))

    def test_bootstrap_source_contains_no_destructive_reset(self):
        source = (Path(__file__).resolve().parents[1] / "launch.py").read_text(encoding="utf-8")
        self.assertNotIn("reset --hard", source)
        self.assertIn('"git", "clone", "--depth", "1"', source)

    def test_git_pull_refuses_dirty_worktree(self):
        output = []
        with (
            patch.object(git_manager, "is_worktree_clean", return_value=(False, " M file.py")),
            patch.object(git_manager, "check_internet") as internet,
        ):
            self.assertFalse(git_manager.git_pull(output.append))
        internet.assert_not_called()
        self.assertTrue(any("未提交變更" in line for line in output))


class PythonEnvironmentLifecycleTests(unittest.TestCase):
    def test_project_runtime_is_preferred_for_venv_rebuild(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = root / ".python-runtime" / (
                "python.exe" if sys.platform == "win32" else "bin/python"
            )
            runtime.parent.mkdir(parents=True)
            runtime.touch()

            with (
                patch.object(env_manager, "PROJECT_ROOT", root),
                patch.object(env_manager, "_probe_python", return_value=(3, 12)),
            ):
                command, version = env_manager._find_project_python()

        self.assertEqual(command, [str(runtime)])
        self.assertEqual(version, (3, 12))


class ManagedProcessLifecycleTests(unittest.TestCase):
    def test_windows_service_flags_include_no_window(self):
        from manager import process_manager

        with (
            patch.object(process_manager.os, "name", "nt"),
            patch.object(process_manager.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            patch.object(process_manager.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, create=True),
        ):
            flags = _service_creation_flags()
        self.assertTrue(flags & 0x08000000)

    def test_incomplete_env_blocks_service_before_process_checks(self):
        manager = ProcessManager.__new__(ProcessManager)
        output = []
        manager.on_output = lambda name, line: output.append((name, line))
        invalid = EnvValidationResult(False, (), ("SMTP_USER",))

        with (
            patch("manager.process_manager.validate_env_file", return_value=invalid),
            patch("manager.process_manager.is_venv_exists") as venv_check,
        ):
            self.assertFalse(manager.start_backend())

        venv_check.assert_not_called()
        self.assertTrue(any("SMTP_USER" in line for _name, line in output))

    def test_short_lived_process_reports_stopped(self):
        statuses = []
        process = ManagedProcess(
            "test",
            [sys.executable, "-c", "print('ok')"],
            Path.cwd(),
            on_status_change=lambda _name, status: statuses.append(status),
        )
        self.assertTrue(process.start())
        process.process.wait(timeout=5)
        process._reader_thread.join(timeout=5)
        self.assertFalse(process.is_running())
        self.assertTrue(process.should_be_running())
        self.assertEqual(process.status, ProcessStatus.STOPPED)
        process.stop()
        self.assertFalse(process.should_be_running())

    def test_configure_changes_next_command(self):
        process = ManagedProcess("test", ["old"], Path.cwd())
        process.configure(cmd=["new"], cwd=Path("."), env={"A": "B"})
        self.assertEqual(process.cmd, ["new"])
        self.assertEqual(process.env, {"A": "B"})

    def test_health_url_rewrites_wildcard_bind_address(self):
        self.assertEqual(
            _health_url("0.0.0.0", 8000, "/health/ready"),
            "http://127.0.0.1:8000/health/ready",
        )
        self.assertEqual(_health_url("::1", 8000), "http://[::1]:8000/")

    def test_http_readiness_controls_running_state(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        process = ManagedProcess(
            "http-test",
            [
                sys.executable,
                "-m",
                "http.server",
                str(port),
                "--bind",
                "127.0.0.1",
            ],
            Path.cwd(),
            health_url=f"http://127.0.0.1:{port}/",
            startup_timeout=10,
            health_probe_timeout=1,
        )
        try:
            self.assertTrue(process.start())
            self.assertEqual(process.status, ProcessStatus.RUNNING)
            self.assertTrue(process.is_healthy())
        finally:
            process.stop(timeout=5)
        self.assertEqual(process.status, ProcessStatus.STOPPED)

    def test_readiness_timeout_never_reports_running(self):
        process = ManagedProcess(
            "never-ready",
            [sys.executable, "-c", "import time; time.sleep(30)"],
            Path.cwd(),
            health_url="http://127.0.0.1:1/health",
            startup_timeout=1,
            health_probe_timeout=1,
        )
        try:
            self.assertFalse(process.start())
            self.assertEqual(process.status, ProcessStatus.ERROR)
            self.assertFalse(process.is_running())
        finally:
            process.stop(timeout=2)

    def test_service_output_is_persisted_to_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "service.log"
            output = []
            process = ManagedProcess(
                "logged",
                [
                    sys.executable,
                    "-c",
                    "import time; print('persisted-line', flush=True); time.sleep(.2)",
                ],
                Path.cwd(),
                on_output=lambda _name, line: output.append(line),
                log_path=log_path,
            )
            self.assertTrue(process.start())
            process.process.wait(timeout=5)
            process._reader_thread.join(timeout=5)

            self.assertIn("persisted-line", log_path.read_text(encoding="utf-8"))
            self.assertIn("persisted-line", output)

    def test_valid_process_can_be_adopted_and_stopped(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        owner = ManagedProcess(
            "owner",
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            Path.cwd(),
            health_url=f"http://127.0.0.1:{port}/",
            startup_timeout=10,
            health_probe_timeout=1,
        )
        self.assertTrue(owner.start())
        adopted = ManagedProcess(
            "adopted",
            owner.cmd,
            Path.cwd(),
            health_url=owner.health_url,
            health_probe_timeout=1,
        )
        try:
            self.assertTrue(adopted.adopt(owner.process))
            self.assertEqual(adopted.status, ProcessStatus.RUNNING)
            self.assertTrue(adopted.is_healthy())
        finally:
            adopted.stop(timeout=5)
            owner.stop(timeout=2)

    def test_process_record_command_validation(self):
        self.assertTrue(
            ProcessManager._is_compatible_record(
                "backend",
                [sys.executable, "-m", "uvicorn", "backend.app:app"],
            )
        )
        self.assertTrue(
            ProcessManager._is_compatible_record("frontend", ["npm.cmd", "run", "dev"])
        )
        self.assertFalse(
            ProcessManager._is_compatible_record("backend", [sys.executable, "unrelated.py"])
        )


class BackendHealthContractTests(unittest.TestCase):
    def test_health_handlers_expose_live_and_ready_states(self):
        try:
            from backend import app as backend_app
        except ImportError as exc:
            self.skipTest(f"backend runtime dependencies not installed: {exc}")

        live = backend_app.health_live()
        self.assertEqual(live["status"], "alive")
        backend_app.app.state.ready = True
        backend_app.app.state.semantic_status = "loading"
        ready = backend_app.health_ready()
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["semantic"], "loading")


if __name__ == "__main__":
    unittest.main()
