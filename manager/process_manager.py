# -*- coding: utf-8 -*-
"""
前後端程序管理器

負責啟動、停止、監控前後端程序，並提供自動重啟功能。
使用 threading 非同步讀取 stdout/stderr 輸出。
"""
import os
import signal
import subprocess
import threading
import time
import logging
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

        self.process: subprocess.Popen | None = None
        self.status = ProcessStatus.STOPPED
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> bool:
        """啟動程序"""
        if self.process and self.process.poll() is None:
            self._emit_output(f"⚠️ {self.name} 已在運行中")
            return True

        self._set_status(ProcessStatus.STARTING)
        self._stop_event.clear()

        # 強制子程序使用 UTF-8 輸出，避免中文亂碼
        run_env = os.environ.copy()
        run_env["PYTHONIOENCODING"] = "utf-8"
        if self.env:
            run_env.update(self.env)

        try:
            self._emit_output(f"▶️ 正在啟動 {self.name}...")
            self._emit_output(f"   命令: {' '.join(self.cmd)}")
            self._emit_output(f"   目錄: {self.cwd}")

            self.process = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(self.cwd),
                env=run_env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )

            # 啟動輸出讀取線程
            self._reader_thread = threading.Thread(
                target=self._read_output,
                daemon=True,
                name=f"{self.name}-reader",
            )
            self._reader_thread.start()

            self._set_status(ProcessStatus.RUNNING)
            self._emit_output(f"✅ {self.name} 已啟動 (PID: {self.process.pid})")
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
        """
        停止程序。

        先嘗試優雅終止，超時後強制終止。
        """
        if not self.process or self.process.poll() is not None:
            self._set_status(ProcessStatus.STOPPED)
            return True

        self._set_status(ProcessStatus.STOPPING)
        self._stop_event.set()
        self._emit_output(f"⏹️ 正在停止 {self.name} (PID: {self.process.pid})...")

        try:
            # Windows 下使用 taskkill 終止整個程序樹
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                self.process.terminate()

            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._emit_output(f"⚠️ {self.name} 未回應，強制終止中...")
                self.process.kill()
                self.process.wait(timeout=5)

            self._emit_output(f"⏹️ {self.name} 已停止")
            self._set_status(ProcessStatus.STOPPED)
            return True

        except Exception as e:
            self._emit_output(f"❌ 停止 {self.name} 時發生錯誤: {e}")
            self._set_status(ProcessStatus.ERROR)
            return False

    def restart(self) -> bool:
        """重啟程序"""
        self._emit_output(f"🔄 正在重啟 {self.name}...")
        self.stop()
        time.sleep(1)
        return self.start()

    def is_running(self) -> bool:
        """檢查程序是否存活"""
        if self.process is None:
            return False
        return self.process.poll() is None

    def _read_output(self):
        """讀取程序的 stdout 輸出（在獨立線程中執行）"""
        try:
            for line in iter(self.process.stdout.readline, ""):
                if self._stop_event.is_set():
                    break
                line = line.rstrip("\n\r")
                if line:
                    self._emit_output(line)
        except Exception:
            pass

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

        self._init_processes()

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
        )

    def start_backend(self) -> bool:
        """啟動 Backend"""
        if not is_venv_exists():
            if self.on_output:
                self.on_output("backend", "❌ .venv 不存在，請先安裝依賴")
            return False
        self._restart_counts["backend"] = 0
        return self._processes["backend"].start()

    def stop_backend(self) -> bool:
        """停止 Backend"""
        self._restart_counts["backend"] = 0
        return self._processes["backend"].stop()

    def restart_backend(self) -> bool:
        """重啟 Backend"""
        self._restart_counts["backend"] = 0
        return self._processes["backend"].restart()

    def start_frontend(self) -> bool:
        """啟動 Frontend"""
        if not is_node_modules_exists():
            if self.on_output:
                self.on_output("frontend", "❌ node_modules 不存在，請先安裝前端依賴")
            return False
        self._restart_counts["frontend"] = 0
        return self._processes["frontend"].start()

    def stop_frontend(self) -> bool:
        """停止 Frontend"""
        self._restart_counts["frontend"] = 0
        return self._processes["frontend"].stop()

    def restart_frontend(self) -> bool:
        """重啟 Frontend"""
        self._restart_counts["frontend"] = 0
        return self._processes["frontend"].restart()

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
        interval = self._config.get("health_check_interval", 5)
        max_attempts = self._config.get("max_restart_attempts", 5)
        restart_delay = self._config.get("restart_delay", 3)

        while not self._health_stop_event.is_set():
            self._health_stop_event.wait(interval)
            if self._health_stop_event.is_set():
                break

            self._config = load_config()
            if not self._config.get("auto_restart", True):
                continue

            for name, proc in self._processes.items():
                if proc.status == ProcessStatus.RUNNING and not proc.is_running():
                    count = self._restart_counts.get(name, 0)
                    if count >= max_attempts:
                        if self.on_output:
                            self.on_output(
                                name,
                                f"⛔ {name} 已連續重啟 {count} 次，停止自動重啟。"
                                f"請手動檢查問題後再啟動。",
                            )
                        proc._set_status(ProcessStatus.ERROR)
                        continue

                    if self.on_output:
                        self.on_output(
                            name,
                            f"🔄 偵測到 {name} 意外停止，{restart_delay}秒後自動重啟... "
                            f"(第 {count + 1}/{max_attempts} 次)",
                        )

                    time.sleep(restart_delay)
                    if proc.start():
                        self._restart_counts[name] = count + 1
                    else:
                        self._restart_counts[name] = count + 1

    def cleanup(self):
        """清理所有程序"""
        self.stop_health_check()
        for proc in self._processes.values():
            if proc.is_running():
                proc.stop()
