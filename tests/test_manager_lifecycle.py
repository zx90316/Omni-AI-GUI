"""Fast lifecycle tests for Manager configuration and process supervision."""
from pathlib import Path
import socket
import subprocess
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
from manager import env_editor
from launch import (
    discover_project_root,
    get_project_override,
    is_project_directory,
    load_saved_project_root,
    save_project_root,
)
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
                "model_idle_timeout_minutes": 0,
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
        self.assertEqual(normalized["model_idle_timeout_minutes"], 5)
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
    def test_env_group_uses_child_frame_for_padding(self):
        source = Path(env_editor.__file__).read_text(encoding="utf-8")
        self.assertNotIn(
            'ttk.LabelFrame(scrollable, text=f"  {group_name}  ", padding=',
            source,
        )
        self.assertIn("ttk.Frame(group_frame, padding=12)", source)

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
        self.assertIn("SMTP_FROM_EMAIL", result.missing_keys)

    def test_self_hosted_smtp_allows_custom_port_and_optional_auth(self):
        values = initial_env_values(
            {
                "SMTP_HOST": "mail.internal.example",
                "SMTP_PORT": "2525",
                "SMTP_SECURITY": "starttls",
                "SMTP_USER": "relay",
                "SMTP_PASSWORD": "",
                "SMTP_FROM_EMAIL": "noreply@example.com",
            }
        )
        result = validate_env_values(values)
        self.assertTrue(result.valid, result.errors)

    def test_smtp_security_rejects_unknown_mode(self):
        values = initial_env_values(
            {
                "SMTP_USER": "owner@example.com",
                "SMTP_PASSWORD": "application-password",
                "SMTP_SECURITY": "tls",
            }
        )
        result = validate_env_values(values)
        self.assertFalse(result.valid)
        self.assertTrue(any("SMTP_SECURITY" in err for err in result.errors))

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
    @staticmethod
    def _make_project(root: Path) -> None:
        (root / "manager").mkdir(parents=True)
        (root / "manager" / "app.py").touch()
        (root / "backend").mkdir()
        (root / "backend" / "app.py").touch()
        (root / "frontend").mkdir()
        (root / "frontend" / "package.json").touch()

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

    def test_project_discovery_finds_clone_beside_executable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            executable_dir = Path(temp_dir)
            project_root = executable_dir / "Omni-AI-GUI"
            self._make_project(project_root)
            discovered = discover_project_root(executable_dir)
        self.assertEqual(discovered, project_root.resolve())

    def test_project_state_round_trip_and_stale_path_rejection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_root = root / "project"
            state_file = root / "state" / "bootstrap.json"
            self._make_project(project_root)
            self.assertTrue(save_project_root(project_root, state_file))
            self.assertEqual(load_saved_project_root(state_file), project_root.resolve())
            (project_root / "backend" / "app.py").unlink()
            self.assertIsNone(load_saved_project_root(state_file))

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
    def tearDown(self):
        env_manager.reset_environment_cancellation()
        with env_manager._active_commands_lock:
            env_manager._active_commands.clear()

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

    def test_frontend_install_uses_lockfile_when_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text("{}", encoding="utf-8")
            (root / "package-lock.json").write_text("{}", encoding="utf-8")
            with (
                patch.object(env_manager, "check_internet", return_value=True),
                patch.object(env_manager, "get_frontend_dir", return_value=root),
                patch.object(env_manager, "_run_command", return_value=(True, "")) as run,
            ):
                self.assertTrue(env_manager.install_frontend_deps())

        self.assertEqual(run.call_args.args[0][1], "ci")

    def test_shutdown_cancels_active_environment_process_tree(self):
        class FakeProcess:
            pid = 8765

        process = FakeProcess()
        with env_manager._active_commands_lock:
            env_manager._active_commands.add(process)
        with patch.object(env_manager, "terminate_process_tree") as terminate_tree:
            count = env_manager.cancel_environment_operations(timeout=7)

        self.assertEqual(count, 1)
        terminate_tree.assert_called_once_with(process, timeout=7)
        self.assertTrue(env_manager._commands_cancelled.is_set())


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

    def test_windows_stop_terminates_complete_process_tree(self):
        from manager import process_manager

        class FakeProcess:
            pid = 4321
            returncode = None
            killed = False

            @staticmethod
            def poll():
                return None

            @staticmethod
            def wait(timeout=None):
                del timeout
                return 0

            def kill(self):
                self.killed = True

        managed = ManagedProcess("frontend", ["npm.cmd", "run", "dev"], Path.cwd())
        process = FakeProcess()
        completed = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(process_manager.os, "name", "nt"),
            patch.object(process_manager.subprocess, "run", return_value=completed) as run,
        ):
            managed._terminate_process(process)

        self.assertEqual(
            run.call_args.args[0],
            ["taskkill", "/F", "/T", "/PID", "4321"],
        )
        self.assertFalse(process.killed)

    def test_stop_waits_for_reader_and_releases_process_handle(self):
        class FakeProcess:
            pid = 4321
            returncode = None

            def poll(self):
                return self.returncode

        class FakeReader:
            joined = False

            @staticmethod
            def is_alive():
                return not FakeReader.joined

            @staticmethod
            def join(timeout=None):
                del timeout
                FakeReader.joined = True

        managed = ManagedProcess("backend", ["python"], Path.cwd())
        process = FakeProcess()
        managed.process = process
        managed._reader_thread = FakeReader()

        def terminate(_process, timeout=10):
            del timeout
            _process.returncode = 0

        with patch.object(managed, "_terminate_process", side_effect=terminate):
            self.assertTrue(managed.stop())

        self.assertTrue(FakeReader.joined)
        self.assertIsNone(managed._reader_thread)
        self.assertIsNone(managed.process)

    def test_vite_uses_configured_port_without_automatic_fallback(self):
        config = (
            Path(__file__).resolve().parents[1] / "frontend" / "vite.config.js"
        ).read_text(encoding="utf-8")
        self.assertIn("strictPort: true", config)

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

    def test_backend_receives_configured_model_idle_timeout(self):
        configured = manager_config.DEFAULT_CONFIG | {
            "model_idle_timeout_minutes": 17,
        }
        with (
            patch("manager.process_manager.load_config", return_value=configured),
            patch.object(ProcessManager, "_adopt_existing_processes"),
        ):
            manager = ProcessManager()

        self.assertEqual(
            manager._processes["backend"].env["MODEL_IDLE_TIMEOUT_MINUTES"],
            "17",
        )

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

    def test_inference_routes_require_authentication(self):
        try:
            import importlib

            backend_app = importlib.import_module("backend.app")
            from backend.auth_utils import get_current_user
        except ImportError as exc:
            self.skipTest(f"backend runtime dependencies not installed: {exc}")

        protected_routers = (
            backend_app.tasks_router,
            backend_app.youtube_router,
            backend_app.ocr_router,
            backend_app.clip_search_router,
            backend_app.workflow_router,
            backend_app.semantic_router,
        )
        for protected_router in protected_routers:
            protected_paths = {
                route.path
                for route in protected_router.routes
                if hasattr(route, "dependant")
            }
            included_routes = [
                route
                for route in backend_app.app.routes
                if getattr(route, "path", None) in protected_paths
            ]
            self.assertTrue(included_routes, protected_router.prefix)
            for route in included_routes:
                dependency_calls = {
                    dependency.call
                    for dependency in route.dependant.dependencies
                }
                self.assertIn(
                    get_current_user,
                    dependency_calls,
                    f"{route.methods} {route.path}",
                )


if __name__ == "__main__":
    unittest.main()
