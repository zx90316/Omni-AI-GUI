# -*- coding: utf-8 -*-
"""
前後端程序管理器

負責啟動、停止、監控前後端程序，並提供自動重啟功能。
使用 threading 非同步讀取 stdout/stderr 輸出。
"""
import os
import json
import signal
import subprocess
import threading
import time
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
from enum import Enum

from manager.config import (
    PROJECT_ROOT,
    get_venv_python,
    get_frontend_dir,
    is_venv_exists,
    is_node_modules_exists,
    load_config,
)

logger = logging.getLogger(__name__)
STATE_DIR = PROJECT_ROOT / ".manager"
LOG_DIR = STATE_DIR / "logs"
PROCESS_STATE_FILE = STATE_DIR / "processes.json"
MAX_LOG_BYTES = 10 * 1024 * 1024


def _health_url(host: str, port: int, path: str = "/") -> str:
    """Build a locally reachable URL even when a service binds a wildcard host."""
    normalized = host.strip()
    if normalized in {"", "0.0.0.0", "::", "[::]", "*"}:
        normalized = "127.0.0.1"
    elif ":" in normalized and not normalized.startswith("["):
        normalized = f"[{normalized}]"
    return f"http://{normalized}:{port}{path}"


class AdoptedProcess:
    """Popen-compatible adapter for a validated process from a previous Manager."""

    stdout = None

    def __init__(self, pid: int, expected_create_time: float):
        import psutil

        process = psutil.Process(pid)
        if abs(process.create_time() - expected_create_time) > 1.0:
            raise psutil.NoSuchProcess(pid)
        self._process = process
        self.pid = pid
        self.returncode = None

    def poll(self):
        import psutil

        try:
            if self._process.is_running() and self._process.status() != psutil.STATUS_ZOMBIE:
                return None
        except psutil.Error:
            pass
        self.returncode = 0
        return self.returncode

    def wait(self, timeout=None):
        import psutil

        try:
            self.returncode = self._process.wait(timeout=timeout)
        except psutil.TimeoutExpired as exc:
            raise subprocess.TimeoutExpired(str(self.pid), timeout) from exc
        return self.returncode

    def send_signal(self, sig):
        self._process.send_signal(sig)

    def terminate(self):
        self._process.terminate()

    def kill(self):
        self._process.kill()

    def matches_service(self, name: str, expected_cwd: Path) -> bool:
        """Verify the live OS process still resembles the recorded service."""
        import psutil

        try:
            if Path(self._process.cwd()).resolve() != expected_cwd.resolve():
                return False
            command_line = " ".join(self._process.cmdline()).lower()
        except (OSError, psutil.Error):
            return False
        if name == "backend":
            return "uvicorn" in command_line and "backend.app:app" in command_line
        if name == "frontend":
            return "npm" in command_line and "run" in command_line and "dev" in command_line
        return False


class ProcessStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class ManagedProcess:
    """管理一個子程序的生命週期"""

    def __init__(
        self,
        name: str,
        cmd: list[str],
        cwd: Path,
        on_output: Callable[[str, str], None] | None = None,
        on_status_change: Callable[[str, ProcessStatus], None] | None = None,
        env: dict | None = None,
        health_url: str | None = None,
        startup_timeout: int = 60,
        health_probe_timeout: int = 2,
        log_path: Path | None = None,
    ):
        """
        Args:
            name: 程序名稱（例如 "backend", "frontend"）
            cmd: 啟動命令
            cwd: 工作目錄
            on_output: 輸出回呼 (name, line)
            on_status_change: 狀態變更回呼 (name, status)
            env: 追加的環境變數
        """
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.on_output = on_output
        self.on_status_change = on_status_change
        self.env = env
        self.health_url = health_url
        self.startup_timeout = startup_timeout
        self.health_probe_timeout = health_probe_timeout
        self.log_path = log_path

        self.process: subprocess.Popen | None = None
        self.status = ProcessStatus.STOPPED
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._state_lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._desired_running = False
        self._started_at: float | None = None
        self._active_cmd: list[str] | None = None
        self._active_cwd: Path | None = None
        self._active_health_url: str | None = None

    def start(self) -> bool:
        """Serialize starts/stops so rapid GUI actions cannot spawn duplicates."""
        with self._operation_lock:
            return self._start()

    def _start(self) -> bool:
        """啟動程序"""
        with self._state_lock:
            self._desired_running = True
            if self.process and self.process.poll() is None:
                self._emit_output(f"⚠️ {self.name} 已在運行中")
                return True

            self._set_status(ProcessStatus.STARTING)
            self._stop_event.clear()
            self._active_cmd = None
            self._active_cwd = None
            self._active_health_url = None

        # 強制子程序使用 UTF-8 輸出，避免中文亂碼
        run_env = os.environ.copy()
        run_env["PYTHONIOENCODING"] = "utf-8"
        if self.env:
            run_env.update(self.env)

        try:
            self._emit_output(f"▶️ 正在啟動 {self.name}...")
            self._emit_output(f"   命令: {' '.join(self.cmd)}")
            self._emit_output(f"   目錄: {self.cwd}")

            log_stream = None
            log_offset = 0
            if self.log_path:
                log_stream, log_offset = self._prepare_log_stream()

            try:
                self.process = subprocess.Popen(
                    self.cmd,
                    stdout=log_stream if log_stream is not None else subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=log_stream is None,
                    encoding="utf-8" if log_stream is None else None,
                    errors="replace" if log_stream is None else None,
                    cwd=str(self.cwd),
                    env=run_env,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                )
            finally:
                if log_stream is not None:
                    log_stream.close()

            self._active_cmd = list(self.cmd)
            self._active_cwd = self.cwd
            self._active_health_url = self.health_url

            # 啟動輸出讀取線程
            self._reader_thread = threading.Thread(
                target=self._read_log_output if self.log_path else self._read_output,
                args=(self.process, log_offset) if self.log_path else (self.process,),
                daemon=True,
                name=f"{self.name}-reader",
            )
            self._reader_thread.start()

            self._started_at = time.monotonic()
            if self.health_url:
                self._emit_output(f"   等待服務就緒: {self.health_url}")
                if not self._wait_until_ready(self.process):
                    return False
            self._set_status(ProcessStatus.RUNNING)
            self._emit_output(f"✅ {self.name} 已就緒 (PID: {self.process.pid})")
            return True

        except FileNotFoundError as e:
            self._emit_output(f"❌ 啟動 {self.name} 失敗: 找不到命令 - {e}")
            self._set_status(ProcessStatus.ERROR)
            return False
        except Exception as e:
            self._emit_output(f"❌ 啟動 {self.name} 失敗: {e}")
            self._set_status(ProcessStatus.ERROR)
            return False

    def stop(self, timeout: int = 10) -> bool:
        """Serialize stop against a concurrent start/restart."""
        # Cancel an in-flight readiness wait before waiting for the operation lock.
        with self._state_lock:
            self._desired_running = False
        with self._operation_lock:
            return self._stop(timeout)

    def _stop(self, timeout: int = 10) -> bool:
        """
        停止程序。

        先嘗試優雅終止，超時後強制終止。
        """
        with self._state_lock:
            self._desired_running = False
            process = self.process
            if not process or process.poll() is not None:
                self._active_cmd = None
                self._active_cwd = None
                self._active_health_url = None
                self._set_status(ProcessStatus.STOPPED)
                return True

        self._set_status(ProcessStatus.STOPPING)
        self._stop_event.set()
        self._emit_output(f"⏹️ 正在停止 {self.name} (PID: {self.process.pid})...")

        try:
            self._terminate_process(process, timeout=timeout)

            self._emit_output(f"⏹️ {self.name} 已停止")
            self._started_at = None
            self._active_cmd = None
            self._active_cwd = None
            self._active_health_url = None
            self._set_status(ProcessStatus.STOPPED)
            return True

        except Exception as e:
            self._emit_output(f"❌ 停止 {self.name} 時發生錯誤: {e}")
            self._set_status(ProcessStatus.ERROR)
            return False

    def restart(self) -> bool:
        """重啟程序"""
        with self._operation_lock:
            self._emit_output(f"🔄 正在重啟 {self.name}...")
            self._stop()
            time.sleep(1)
            return self._start()

    def is_running(self) -> bool:
        """檢查程序是否存活"""
        if self.process is None:
            return False
        return self.process.poll() is None

    def should_be_running(self) -> bool:
        """Whether the user/supervisor expects this service to be running."""
        with self._state_lock:
            return self._desired_running

    def uptime(self) -> float:
        if not self.is_running() or self._started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    def configure(
        self,
        *,
        cmd: list[str],
        cwd: Path,
        env: dict | None = None,
        health_url: str | None = None,
        startup_timeout: int | None = None,
        health_probe_timeout: int | None = None,
    ):
        """Update the command used by the next start/restart."""
        with self._state_lock:
            self.cmd = cmd
            self.cwd = cwd
            self.env = env
            self.health_url = health_url
            if startup_timeout is not None:
                self.startup_timeout = startup_timeout
            if health_probe_timeout is not None:
                self.health_probe_timeout = health_probe_timeout

    def give_up(self):
        """Stop supervising a process after exhausting restart attempts."""
        with self._state_lock:
            self._desired_running = False
            self._set_status(ProcessStatus.ERROR)

    def is_healthy(self) -> bool:
        """Check both process liveness and the configured HTTP endpoint."""
        if not self.is_running():
            return False

        health_url = self._active_health_url or self.health_url
        if not health_url:
            return True
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            request = urllib.request.Request(
                health_url,
                headers={"User-Agent": "Omni-AI-Manager/health"},
            )
            with opener.open(request, timeout=self.health_probe_timeout) as response:
                return 200 <= response.status < 400
        except (OSError, urllib.error.URLError, ValueError):
            return False

    def adopt(
        self,
        process: AdoptedProcess,
        *,
        active_cmd: list[str] | None = None,
        active_cwd: Path | None = None,
        active_health_url: str | None = None,
    ) -> bool:
        """Adopt a validated process left running by a previous Manager instance."""
        with self._operation_lock:
            with self._state_lock:
                if self.process and self.process.poll() is None:
                    return False
                if process.poll() is not None:
                    return False
                self.process = process
                self._desired_running = True
                self._stop_event.clear()
                self._started_at = time.monotonic()
                self._active_cmd = list(active_cmd or self.cmd)
                self._active_cwd = active_cwd or self.cwd
                self._active_health_url = active_health_url or self.health_url

            if not self.is_healthy():
                with self._state_lock:
                    self.process = None
                    self._desired_running = False
                    self._started_at = None
                    self._active_cmd = None
                    self._active_cwd = None
                    self._active_health_url = None
                self._set_status(ProcessStatus.STOPPED)
                return False

            self._set_status(ProcessStatus.RUNNING)
            self._emit_output(f"✅ 已接管既有 {self.name} 服務 (PID: {process.pid})")
            if self.log_path:
                offset = self.log_path.stat().st_size if self.log_path.exists() else 0
                self._reader_thread = threading.Thread(
                    target=self._read_log_output,
                    args=(process, offset),
                    daemon=True,
                    name=f"{self.name}-reader",
                )
                self._reader_thread.start()
            return True

    def _wait_until_ready(self, process: subprocess.Popen) -> bool:
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self._emit_output(f"❌ {self.name} 在就緒前結束 (code={process.returncode})")
                self._set_status(ProcessStatus.ERROR)
                return False
            if not self.should_be_running():
                self._emit_output(f"⏹️ 已取消 {self.name} 啟動")
                self._terminate_process(process, timeout=5)
                self._set_status(ProcessStatus.STOPPED)
                return False
            if self.is_healthy():
                return True
            time.sleep(0.25)

        self._emit_output(f"❌ {self.name} 在 {self.startup_timeout} 秒內未就緒")
        self._terminate_process(process, timeout=5)
        self._set_status(ProcessStatus.ERROR)
        return False

    def _terminate_process(self, process: subprocess.Popen, timeout: int = 10):
        """Attempt graceful process-group shutdown, then force the tree if needed."""
        if process.poll() is not None:
            return
        try:
            if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
            process.wait(timeout=timeout)
            return
        except (OSError, subprocess.TimeoutExpired):
            self._emit_output(f"⚠️ {self.name} 未回應，強制終止中...")

        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            process.kill()
        process.wait(timeout=5)

    def _read_output(self, process: subprocess.Popen):
        """讀取程序的 stdout 輸出（在獨立線程中執行）"""
        try:
            for line in iter(process.stdout.readline, ""):
                if self._stop_event.is_set():
                    break
                line = line.rstrip("\n\r")
                if line:
                    self._emit_output(line)
        except Exception:
            pass
        finally:
            if process.stdout is not None:
                process.stdout.close()
            try:
                process.wait(timeout=1)
            except (subprocess.TimeoutExpired, OSError):
                pass
            if self.process is process and self.should_be_running() and process.poll() is not None:
                self._set_status(ProcessStatus.STOPPED)

    def _prepare_log_stream(self):
        """Rotate an oversized log and return an append stream plus its tail offset."""
        assert self.log_path is not None
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.log_path.exists() and self.log_path.stat().st_size >= MAX_LOG_BYTES:
            rotated = self.log_path.with_suffix(self.log_path.suffix + ".1")
            if rotated.exists():
                rotated.unlink()
            os.replace(self.log_path, rotated)

        offset = self.log_path.stat().st_size if self.log_path.exists() else 0
        stream = self.log_path.open("ab", buffering=0)
        header = (
            f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} Manager started "
            f"{self.name} ---\n"
        ).encode("utf-8")
        stream.write(header)
        return stream, offset

    def _read_log_output(self, process, offset: int):
        """Tail a persistent service log without owning the child's output handle."""
        assert self.log_path is not None
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.touch(exist_ok=True)
            with self.log_path.open("rb") as stream:
                stream.seek(offset)
                while True:
                    line = stream.readline()
                    if line:
                        decoded = line.decode("utf-8", errors="replace").rstrip("\n\r")
                        if decoded:
                            self._emit_output(decoded)
                        continue
                    if self._stop_event.is_set() or process.poll() is not None:
                        # Drain bytes written immediately before process exit.
                        remainder = stream.read()
                        if remainder:
                            for raw_line in remainder.splitlines():
                                decoded = raw_line.decode("utf-8", errors="replace")
                                if decoded:
                                    self._emit_output(decoded)
                        break
                    time.sleep(0.1)
        except (OSError, ValueError):
            logger.exception("Failed to tail %s log", self.name)
        finally:
            if self.process is process and self.should_be_running() and process.poll() is not None:
                self._set_status(ProcessStatus.STOPPED)

    def _emit_output(self, line: str):
        """發送輸出到回呼"""
        if self.on_output:
            try:
                self.on_output(self.name, line)
            except Exception:
                pass

    def _set_status(self, status: ProcessStatus):
        """更新狀態並觸發回呼"""
        self.status = status
        if self.on_status_change:
            try:
                self.on_status_change(self.name, status)
            except Exception:
                pass


class ProcessManager:
    """
    管理前後端程序的生命週期，包含健康檢查與自動重啟。
    """

    def __init__(
        self,
        on_output: Callable[[str, str], None] | None = None,
        on_status_change: Callable[[str, ProcessStatus], None] | None = None,
    ):
        self.on_output = on_output
        self.on_status_change = on_status_change

        self._config = load_config()
        self._processes: dict[str, ManagedProcess] = {}
        self._health_thread: threading.Thread | None = None
        self._health_stop_event = threading.Event()
        self._restart_counts: dict[str, int] = {}
        self._health_failures: dict[str, int] = {}
        self._state_file_lock = threading.Lock()

        self._init_processes()
        self._adopt_existing_processes()

    def _init_processes(self):
        """初始化前後端程序定義"""
        backend_port = self._config.get("backend_port", 8000)
        backend_host = self._config.get("backend_host", "0.0.0.0")

        # Backend 命令
        venv_python = str(get_venv_python())
        backend_cmd = [
            venv_python, "-m", "uvicorn",
            "backend.app:app",
            "--host", backend_host,
            "--port", str(backend_port),
        ]

        self._processes["backend"] = ManagedProcess(
            name="backend",
            cmd=backend_cmd,
            cwd=PROJECT_ROOT,
            on_output=self.on_output,
            on_status_change=self.on_status_change,
            health_url=_health_url(backend_host, backend_port, "/health/ready"),
            startup_timeout=self._config.get("startup_timeout", 60),
            health_probe_timeout=self._config.get("health_probe_timeout", 2),
            log_path=LOG_DIR / "backend.log",
        )

        # Frontend 命令
        npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
        frontend_host = self._config.get("frontend_host", "localhost")
        frontend_port = self._config.get("frontend_port", 5173)

        # 透過環境變數傳遞給 Vite
        frontend_env = {
            "HOST": frontend_host,
            "PORT": str(frontend_port),
            "BACKEND_HOST": backend_host,
            "BACKEND_PORT": str(backend_port)
        }

        frontend_cmd = [npm_cmd, "run", "dev"]

        self._processes["frontend"] = ManagedProcess(
            name="frontend",
            cmd=frontend_cmd,
            cwd=get_frontend_dir(),
            on_output=self.on_output,
            on_status_change=self.on_status_change,
            env=frontend_env,
            health_url=_health_url(frontend_host, frontend_port),
            startup_timeout=self._config.get("startup_timeout", 60),
            health_probe_timeout=self._config.get("health_probe_timeout", 2),
            log_path=LOG_DIR / "frontend.log",
        )

    def _save_process_state(self):
        """Atomically persist enough identity data to safely re-adopt services."""
        try:
            import psutil
        except ImportError:
            return

        records = []
        for name, managed in self._processes.items():
            process = managed.process
            if not process or process.poll() is not None:
                continue
            try:
                create_time = psutil.Process(process.pid).create_time()
            except psutil.Error:
                continue
            records.append(
                {
                    "name": name,
                    "pid": process.pid,
                    "create_time": create_time,
                    "cmd": managed._active_cmd or managed.cmd,
                    "cwd": str((managed._active_cwd or managed.cwd).resolve()),
                    "health_url": managed._active_health_url or managed.health_url,
                    "log_path": str(managed.log_path.resolve()) if managed.log_path else None,
                }
            )

        payload = {"version": 1, "processes": records}
        with self._state_file_lock:
            try:
                PROCESS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                temp_file = PROCESS_STATE_FILE.with_suffix(".tmp")
                temp_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                os.replace(temp_file, PROCESS_STATE_FILE)
            except OSError:
                logger.exception("Unable to persist Manager process state")

    def _adopt_existing_processes(self):
        """Re-adopt only exact, live and healthy processes recorded by this project."""
        try:
            payload = json.loads(PROCESS_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return

        if not isinstance(payload, dict) or payload.get("version") != 1:
            return

        records = payload.get("processes", [])
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict):
                continue
            name = record.get("name")
            managed = self._processes.get(name)
            if managed is None:
                continue
            record_cmd = record.get("cmd")
            if not self._is_compatible_record(name, record_cmd):
                continue
            try:
                record_cwd = Path(record.get("cwd", "")).resolve()
                if record_cwd != managed.cwd.resolve():
                    continue
            except (OSError, TypeError):
                continue
            recorded_log = record.get("log_path")
            if managed.log_path:
                try:
                    if Path(recorded_log).resolve() != managed.log_path.resolve():
                        continue
                except (OSError, TypeError):
                    continue

            try:
                adopted = AdoptedProcess(
                    int(record["pid"]),
                    float(record["create_time"]),
                )
                if not adopted.matches_service(name, record_cwd):
                    continue
                adopted_ok = managed.adopt(
                    adopted,
                    active_cmd=record_cmd,
                    active_cwd=record_cwd,
                    active_health_url=record.get("health_url"),
                )
                if (
                    adopted_ok
                    and record.get("health_url") != managed.health_url
                    and self.on_output
                ):
                    self.on_output(
                        name,
                        "ℹ️ 已接管使用舊連線設定的服務；重啟服務後會套用目前設定",
                    )
            except ImportError:
                logger.warning("psutil is unavailable; process adoption is disabled")
                break
            except (KeyError, TypeError, ValueError, OSError):
                continue
            except Exception:
                logger.debug("Ignoring stale %s process record", name, exc_info=True)

        self._save_process_state()

    @staticmethod
    def _is_compatible_record(name: str, command) -> bool:
        """Reject state records that do not look like a Manager-owned service."""
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            return False
        if name == "backend":
            return "-m" in command and "uvicorn" in command and "backend.app:app" in command
        if name == "frontend":
            return len(command) >= 3 and command[-2:] == ["run", "dev"]
        return False

    def reload_config(self):
        """Reload settings and apply commands to the next service start/restart."""
        self._config = load_config()
        backend_port = self._config.get("backend_port", 8000)
        backend_host = self._config.get("backend_host", "0.0.0.0")
        self._processes["backend"].configure(
            cmd=[
                str(get_venv_python()),
                "-m",
                "uvicorn",
                "backend.app:app",
                "--host",
                backend_host,
                "--port",
                str(backend_port),
            ],
            cwd=PROJECT_ROOT,
            health_url=_health_url(backend_host, backend_port, "/health/ready"),
            startup_timeout=self._config.get("startup_timeout", 60),
            health_probe_timeout=self._config.get("health_probe_timeout", 2),
        )

        frontend_host = self._config.get("frontend_host", "localhost")
        frontend_port = self._config.get("frontend_port", 5173)
        npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
        self._processes["frontend"].configure(
            cmd=[npm_cmd, "run", "dev"],
            cwd=get_frontend_dir(),
            env={
                "HOST": frontend_host,
                "PORT": str(frontend_port),
                "BACKEND_HOST": backend_host,
                "BACKEND_PORT": str(backend_port),
            },
            health_url=_health_url(frontend_host, frontend_port),
            startup_timeout=self._config.get("startup_timeout", 60),
            health_probe_timeout=self._config.get("health_probe_timeout", 2),
        )

    def start_backend(self) -> bool:
        """啟動 Backend"""
        if not is_venv_exists():
            if self.on_output:
                self.on_output("backend", "❌ .venv 不存在，請先安裝依賴")
            return False
        self._restart_counts["backend"] = 0
        self._health_failures["backend"] = 0
        result = self._processes["backend"].start()
        self._save_process_state()
        return result

    def stop_backend(self) -> bool:
        """停止 Backend"""
        self._restart_counts["backend"] = 0
        self._health_failures["backend"] = 0
        result = self._processes["backend"].stop()
        self._save_process_state()
        return result

    def restart_backend(self) -> bool:
        """重啟 Backend"""
        if not is_venv_exists():
            if self.on_output:
                self.on_output("backend", "❌ .venv 不存在或已損壞，請先安裝依賴")
            return False
        self._restart_counts["backend"] = 0
        self._health_failures["backend"] = 0
        result = self._processes["backend"].restart()
        self._save_process_state()
        return result

    def start_frontend(self) -> bool:
        """啟動 Frontend"""
        if not is_node_modules_exists():
            if self.on_output:
                self.on_output("frontend", "❌ node_modules 不存在，請先安裝前端依賴")
            return False
        self._restart_counts["frontend"] = 0
        self._health_failures["frontend"] = 0
        result = self._processes["frontend"].start()
        self._save_process_state()
        return result

    def stop_frontend(self) -> bool:
        """停止 Frontend"""
        self._restart_counts["frontend"] = 0
        self._health_failures["frontend"] = 0
        result = self._processes["frontend"].stop()
        self._save_process_state()
        return result

    def restart_frontend(self) -> bool:
        """重啟 Frontend"""
        if not is_node_modules_exists():
            if self.on_output:
                self.on_output("frontend", "❌ node_modules 不存在，請先安裝前端依賴")
            return False
        self._restart_counts["frontend"] = 0
        self._health_failures["frontend"] = 0
        result = self._processes["frontend"].restart()
        self._save_process_state()
        return result

    def start_all(self) -> bool:
        """啟動前後端"""
        b = self.start_backend()
        f = self.start_frontend()
        return b and f

    def stop_all(self) -> bool:
        """停止前後端"""
        f = self.stop_frontend()
        b = self.stop_backend()
        return b and f

    def restart_all(self) -> bool:
        """重啟前後端"""
        self.stop_all()
        time.sleep(1)
        return self.start_all()

    def get_status(self, name: str) -> ProcessStatus:
        """取得指定程序的狀態"""
        proc = self._processes.get(name)
        if proc is None:
            return ProcessStatus.STOPPED

        # 即時更新狀態
        if proc.status == ProcessStatus.RUNNING and not proc.is_running():
            proc.status = ProcessStatus.STOPPED
        return proc.status

    def start_health_check(self):
        """啟動健康檢查線程"""
        if self._health_thread and self._health_thread.is_alive():
            return

        self._health_stop_event.clear()
        self._health_thread = threading.Thread(
            target=self._health_check_loop,
            daemon=True,
            name="health-check",
        )
        self._health_thread.start()

    def stop_health_check(self):
        """停止健康檢查"""
        self._health_stop_event.set()

    def _health_check_loop(self):
        """健康檢查主迴圈"""
        while not self._health_stop_event.is_set():
            interval = self._config.get("health_check_interval", 5)
            self._health_stop_event.wait(interval)
            if self._health_stop_event.is_set():
                break

            self.reload_config()
            if not self._config.get("auto_restart", True):
                continue

            max_attempts = self._config.get("max_restart_attempts", 5)
            restart_delay = self._config.get("restart_delay", 3)
            failure_threshold = self._config.get("health_failure_threshold", 3)

            for name, proc in self._processes.items():
                if proc.should_be_running() and not proc.is_running():
                    count = self._restart_counts.get(name, 0)
                    if count >= max_attempts:
                        if self.on_output:
                            self.on_output(
                                name,
                                f"⛔ {name} 已連續重啟 {count} 次，停止自動重啟。"
                                f"請手動檢查問題後再啟動。",
                            )
                        proc.give_up()
                        continue

                    if self.on_output:
                        self.on_output(
                            name,
                            f"🔄 偵測到 {name} 意外停止，{restart_delay}秒後自動重啟... "
                            f"(第 {count + 1}/{max_attempts} 次)",
                        )

                    if self._health_stop_event.wait(restart_delay):
                        break
                    proc.start()
                    self._restart_counts[name] = count + 1
                    self._health_failures[name] = 0
                elif proc.is_running() and proc.status == ProcessStatus.RUNNING:
                    if proc.is_healthy():
                        self._health_failures[name] = 0
                        if proc.uptime() >= max(30, restart_delay * 3):
                            self._restart_counts[name] = 0
                        continue

                    failures = self._health_failures.get(name, 0) + 1
                    self._health_failures[name] = failures
                    if failures == 1 and self.on_output:
                        self.on_output(name, f"⚠️ {name} HTTP 健康檢查失敗")
                    if failures < failure_threshold:
                        continue

                    count = self._restart_counts.get(name, 0)
                    if count >= max_attempts:
                        if self.on_output:
                            self.on_output(name, f"⛔ {name} HTTP 健康檢查持續失敗，停止自動重啟")
                        proc.stop()
                        proc.give_up()
                        continue
                    if self.on_output:
                        self.on_output(
                            name,
                            f"🔄 {name} 連續 {failures} 次 HTTP 健康檢查失敗，正在重啟...",
                        )
                    if self._health_stop_event.wait(restart_delay):
                        break
                    proc.restart()
                    self._restart_counts[name] = count + 1
                    self._health_failures[name] = 0
            self._save_process_state()

    def cleanup(self):
        """清理所有程序"""
        self.stop_health_check()
        if self._health_thread and self._health_thread.is_alive():
            self._health_thread.join(timeout=2)
        for proc in self._processes.values():
            if proc.is_running():
                proc.stop()
        self._save_process_state()
