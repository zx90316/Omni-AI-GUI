# -*- coding: utf-8 -*-
"""
Qwen-ASR-GUI 管理面板

使用 ttkbootstrap 建立現代化的管理介面。
"""
import sys
import os
import threading
import webbrowser
import logging
from datetime import datetime

try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
    from ttkbootstrap.scrolled import ScrolledText
except ImportError:
    print("=" * 60)
    print("  缺少 ttkbootstrap 套件，正在安裝...")
    print("=" * 60)
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "ttkbootstrap>=1.10.0"])
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
    from ttkbootstrap.scrolled import ScrolledText

from manager.config import (
    PROJECT_ROOT,
    load_config,
    save_config,
    is_venv_exists,
    is_node_modules_exists,
)
from manager.process_manager import ProcessManager, ProcessStatus
from manager.env_manager import (
    create_venv,
    install_python_deps,
    install_frontend_deps,
    reinstall_all,
    get_python_version,
    get_node_version,
    get_npm_version,
)
from manager.gpu_detector import detect_gpu_info
from manager.git_manager import git_pull, check_for_updates, get_current_version, is_git_repo
from manager.network_utils import check_internet
from manager.ffmpeg_utils import is_ffmpeg_installed, download_ffmpeg
from manager.env_editor import open_env_editor

logger = logging.getLogger(__name__)

# --- 常數 ---
STATUS_COLORS = {
    ProcessStatus.STOPPED: "danger",
    ProcessStatus.STARTING: "warning",
    ProcessStatus.RUNNING: "success",
    ProcessStatus.STOPPING: "warning",
    ProcessStatus.ERROR: "danger",
}

STATUS_LABELS = {
    ProcessStatus.STOPPED: "⏹ 已停止",
    ProcessStatus.STARTING: "⏳ 啟動中...",
    ProcessStatus.RUNNING: "▶ 運行中",
    ProcessStatus.STOPPING: "⏳ 停止中...",
    ProcessStatus.ERROR: "❌ 錯誤",
}


class ManagerApp:
    """管理面板主視窗"""

    def __init__(self):
        self.config = load_config()
        self.process_manager: ProcessManager | None = None

        # 建立主視窗
        self.root = ttk.Window(
            title="Qwen-ASR-GUI Manager",
            themename=self.config.get("theme", "darkly"),
            size=(1100, 980),
            minsize=(900, 600),
        )
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        # 置中視窗
        self.root.place_window_center()

        # Console 互斥鎖
        self._console_lock = threading.Lock()

        # 建立 UI
        self._build_ui()

        # 初始化程序管理器
        self.process_manager = ProcessManager(
            on_output=self._on_process_output,
            on_status_change=self._on_status_change,
        )

        # 載入系統資訊
        self.root.after(100, self._load_system_info)

        # 啟動健康檢查
        self.process_manager.start_health_check()

        # 自動啟動
        if self.config.get("auto_start_on_launch", False):
            self.root.after(500, self._auto_start)

    # ─── UI 建構 ─────────────────────────────────────────

    def _build_ui(self):
        """建構所有 UI 元件"""
        # 主容器
        main = ttk.Frame(self.root)
        main.pack(fill=BOTH, expand=True, padx=10, pady=10)

        # 上方區域：狀態 + 控制
        top_frame = ttk.Frame(main)
        top_frame.pack(fill=X, pady=(0, 10))

        self._build_status_panel(top_frame)
        self._build_control_panel(top_frame)

        # 中間區域：Notebook (Console / 設定)
        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=BOTH, expand=True)

        self._build_console_tab()
        self._build_install_tab()
        self._build_settings_tab()
        self._build_info_tab()

    def _build_status_panel(self, parent):
        """狀態面板"""
        frame = ttk.LabelFrame(parent, text="  📊 狀態  ")
        frame.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 5), ipadx=10, ipady=10)

        # Backend 狀態
        backend_frame = ttk.Frame(frame)
        backend_frame.pack(fill=X, pady=(0, 8))

        ttk.Label(backend_frame, text="Backend:", font=("", 11, "bold")).pack(side=LEFT)
        self.backend_status_label = ttk.Label(
            backend_frame, text="⏹ 已停止", font=("", 11), bootstyle="danger"
        )
        self.backend_status_label.pack(side=LEFT, padx=(10, 0))

        # Frontend 狀態
        frontend_frame = ttk.Frame(frame)
        frontend_frame.pack(fill=X, pady=(0, 8))

        ttk.Label(frontend_frame, text="Frontend:", font=("", 11, "bold")).pack(side=LEFT)
        self.frontend_status_label = ttk.Label(
            frontend_frame, text="⏹ 已停止", font=("", 11), bootstyle="danger"
        )
        self.frontend_status_label.pack(side=LEFT, padx=(10, 0))

        # 網路狀態
        net_frame = ttk.Frame(frame)
        net_frame.pack(fill=X, pady=(0, 5))

        ttk.Label(net_frame, text="網路:", font=("", 11, "bold")).pack(side=LEFT)
        self.net_status_label = ttk.Label(
            net_frame, text="🔍 檢測中...", font=("", 11)
        )
        self.net_status_label.pack(side=LEFT, padx=(10, 0))

        # GPU 資訊
        gpu_frame = ttk.Frame(frame)
        gpu_frame.pack(fill=X)

        ttk.Label(gpu_frame, text="GPU:", font=("", 11, "bold")).pack(side=LEFT)
        self.gpu_label = ttk.Label(
            gpu_frame, text="偵測中...", font=("", 10)
        )
        self.gpu_label.pack(side=LEFT, padx=(10, 0))

    def _build_control_panel(self, parent):
        """控制面板"""
        frame = ttk.LabelFrame(parent, text="  🎛️ 控制  ")
        frame.pack(side=RIGHT, fill=Y, padx=(5, 0), ipadx=10, ipady=10)

        # Backend 控制
        ttk.Label(frame, text="Backend", font=("", 10, "bold")).pack(anchor=W)
        backend_btns = ttk.Frame(frame)
        backend_btns.pack(fill=X, pady=(2, 8))

        self.btn_start_backend = ttk.Button(
            backend_btns, text="▶ 啟動", bootstyle="success-outline",
            command=lambda: self._run_async(self._start_backend), width=8,
        )
        self.btn_start_backend.pack(side=LEFT, padx=(0, 3))

        self.btn_stop_backend = ttk.Button(
            backend_btns, text="⏹ 停止", bootstyle="danger-outline",
            command=lambda: self._run_async(self._stop_backend), width=8,
        )
        self.btn_stop_backend.pack(side=LEFT, padx=(0, 3))

        self.btn_restart_backend = ttk.Button(
            backend_btns, text="🔄 重啟", bootstyle="warning-outline",
            command=lambda: self._run_async(self._restart_backend), width=8,
        )
        self.btn_restart_backend.pack(side=LEFT)

        # Frontend 控制
        ttk.Label(frame, text="Frontend", font=("", 10, "bold")).pack(anchor=W)
        frontend_btns = ttk.Frame(frame)
        frontend_btns.pack(fill=X, pady=(2, 8))

        self.btn_start_frontend = ttk.Button(
            frontend_btns, text="▶ 啟動", bootstyle="success-outline",
            command=lambda: self._run_async(self._start_frontend), width=8,
        )
        self.btn_start_frontend.pack(side=LEFT, padx=(0, 3))

        self.btn_stop_frontend = ttk.Button(
            frontend_btns, text="⏹ 停止", bootstyle="danger-outline",
            command=lambda: self._run_async(self._stop_frontend), width=8,
        )
        self.btn_stop_frontend.pack(side=LEFT, padx=(0, 3))

        self.btn_restart_frontend = ttk.Button(
            frontend_btns, text="🔄 重啟", bootstyle="warning-outline",
            command=lambda: self._run_async(self._restart_frontend), width=8,
        )
        self.btn_restart_frontend.pack(side=LEFT)

        # 一鍵操作
        ttk.Separator(frame, orient=HORIZONTAL).pack(fill=X, pady=5)

        all_btns = ttk.Frame(frame)
        all_btns.pack(fill=X, pady=(2, 5))

        self.btn_start_all = ttk.Button(
            all_btns, text="▶ 全部啟動", bootstyle="success",
            command=lambda: self._run_async(self._start_all), width=12,
        )
        self.btn_start_all.pack(side=LEFT, padx=(0, 3))

        self.btn_stop_all = ttk.Button(
            all_btns, text="⏹ 全部停止", bootstyle="danger",
            command=lambda: self._run_async(self._stop_all), width=12,
        )
        self.btn_stop_all.pack(side=LEFT)

        # 開啟瀏覽器
        ttk.Separator(frame, orient=HORIZONTAL).pack(fill=X, pady=5)

        self.btn_open_browser = ttk.Button(
            frame, text="🌐 開啟前端頁面", bootstyle="info-outline",
            command=self._open_browser,
        )
        self.btn_open_browser.pack(fill=X)

    def _build_console_tab(self):
        """Console 分頁"""
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  📋 Console  ")

        # Console 過濾按鈕
        filter_frame = ttk.Frame(frame)
        filter_frame.pack(fill=X, pady=5, padx=5)

        self.console_filter = ttk.StringVar(value="all")

        ttk.Radiobutton(
            filter_frame, text="全部", variable=self.console_filter,
            value="all", bootstyle="outline-toolbutton",
        ).pack(side=LEFT, padx=(0, 3))

        ttk.Radiobutton(
            filter_frame, text="Backend", variable=self.console_filter,
            value="backend", bootstyle="outline-toolbutton",
        ).pack(side=LEFT, padx=(0, 3))

        ttk.Radiobutton(
            filter_frame, text="Frontend", variable=self.console_filter,
            value="frontend", bootstyle="outline-toolbutton",
        ).pack(side=LEFT, padx=(0, 3))

        ttk.Radiobutton(
            filter_frame, text="系統", variable=self.console_filter,
            value="system", bootstyle="outline-toolbutton",
        ).pack(side=LEFT, padx=(0, 10))

        # 清除按鈕
        ttk.Button(
            filter_frame, text="🗑️ 清除", bootstyle="secondary-outline",
            command=self._clear_console,
        ).pack(side=RIGHT)

        # 自動捲動
        self.auto_scroll_var = ttk.BooleanVar(value=True)
        ttk.Checkbutton(
            filter_frame, text="自動捲動", variable=self.auto_scroll_var,
            bootstyle="round-toggle",
        ).pack(side=RIGHT, padx=(0, 10))

        # Console 文字框
        self.console_text = ScrolledText(frame, height=20, autohide=True)
        self.console_text.pack(fill=BOTH, expand=True, padx=5, pady=(0, 5))
        self.console_text.text.configure(
            state="disabled", background="#1e1e1e", foreground="#d4d4d4",
            font=("Consolas", 10), wrap="word",
        )

        # 儲存所有 console 內容用於過濾
        self._console_entries: list[tuple[str, str]] = []  # (source, text)

    def _build_install_tab(self):
        """安裝/更新分頁"""
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  📦 安裝/更新  ")

        inner = ttk.Frame(frame)
        inner.pack(fill=BOTH, expand=True, padx=10, pady=10)

        # Git 操作
        git_frame = ttk.LabelFrame(inner, text="  📥 Git 版本管理  ")
        git_frame.pack(fill=X, pady=(0, 10), ipadx=10, ipady=8)

        self.version_label = ttk.Label(git_frame, text="版本: 載入中...", font=("", 10))
        self.version_label.pack(anchor=W, pady=(0, 8))

        git_btns = ttk.Frame(git_frame)
        git_btns.pack(fill=X)

        self.btn_git_pull = ttk.Button(
            git_btns, text="📥 Git Pull 更新", bootstyle="info",
            command=lambda: self._run_async(self._git_pull),
        )
        self.btn_git_pull.pack(side=LEFT, padx=(0, 5))

        self.btn_check_update = ttk.Button(
            git_btns, text="🔍 檢查更新", bootstyle="info-outline",
            command=lambda: self._run_async(self._check_updates),
        )
        self.btn_check_update.pack(side=LEFT)

        # Python 依賴
        py_frame = ttk.LabelFrame(inner, text="  🐍 Python 依賴  ")
        py_frame.pack(fill=X, pady=(0, 10), ipadx=10, ipady=8)

        py_info = ttk.Frame(py_frame)
        py_info.pack(fill=X, pady=(0, 8))

        self.py_venv_label = ttk.Label(py_info, text=".venv: 檢測中...", font=("", 10))
        self.py_venv_label.pack(anchor=W)

        self.py_platform_label = ttk.Label(py_info, text="PyTorch: 檢測中...", font=("", 10))
        self.py_platform_label.pack(anchor=W)

        py_btns = ttk.Frame(py_frame)
        py_btns.pack(fill=X)

        self.btn_create_venv = ttk.Button(
            py_btns, text="🔧 建立 .venv", bootstyle="warning-outline",
            command=lambda: self._run_async(self._create_venv),
        )
        self.btn_create_venv.pack(side=LEFT, padx=(0, 5))

        self.btn_pip_install = ttk.Button(
            py_btns, text="📦 Pip Install", bootstyle="warning",
            command=lambda: self._run_async(self._pip_install),
        )
        self.btn_pip_install.pack(side=LEFT, padx=(0, 5))

        # Frontend 依賴
        fe_frame = ttk.LabelFrame(inner, text="  ⚛️ Frontend 依賴  ")
        fe_frame.pack(fill=X, pady=(0, 10), ipadx=10, ipady=8)

        self.fe_status_label = ttk.Label(fe_frame, text="node_modules: 檢測中...", font=("", 10))
        self.fe_status_label.pack(anchor=W, pady=(0, 8))

        fe_btns = ttk.Frame(fe_frame)
        fe_btns.pack(fill=X)

        self.btn_npm_install = ttk.Button(
            fe_btns, text="📦 npm install", bootstyle="success",
            command=lambda: self._run_async(self._npm_install),
        )
        self.btn_npm_install.pack(side=LEFT)

        # FFmpeg
        ffmpeg_frame = ttk.LabelFrame(inner, text="  🎥 系統工具 (FFmpeg)  ")
        ffmpeg_frame.pack(fill=X, pady=(0, 10), ipadx=10, ipady=8)

        self.ffmpeg_status_label = ttk.Label(ffmpeg_frame, text="FFmpeg: 檢測中...", font=("", 10))
        self.ffmpeg_status_label.pack(anchor=W, pady=(0, 8))

        self.btn_download_ffmpeg = ttk.Button(
            ffmpeg_frame, text="📥 自動下載 FFmpeg", bootstyle="info",
            command=lambda: self._run_async(self._download_ffmpeg),
        )
        self.btn_download_ffmpeg.pack(side=LEFT)

        # 重新安裝
        ttk.Separator(inner, orient=HORIZONTAL).pack(fill=X, pady=10)

        self.btn_reinstall = ttk.Button(
            inner,
            text="🔄 完整重新安裝（刪除 .venv + node_modules 並重新安裝）",
            bootstyle="danger",
            command=self._confirm_reinstall,
        )
        self.btn_reinstall.pack(fill=X)

    def _build_settings_tab(self):
        """設定分頁"""
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  ⚙️ 設定  ")

        inner = ttk.Frame(frame)
        inner.pack(fill=BOTH, expand=True, padx=10, pady=10)

        # .env 設定檔
        env_frame = ttk.LabelFrame(inner, text="  📝 環境變數  ")
        env_frame.pack(fill=X, pady=(0, 10), ipadx=10, ipady=8)

        ttk.Button(
            env_frame, text="⚙️ 設定 .env 參數", bootstyle="info",
            command=self._open_env_editor, width=20
        ).pack(anchor=W)

        # 自動行為
        auto_frame = ttk.LabelFrame(inner, text="  🤖 自動化  ")
        auto_frame.pack(fill=X, pady=(0, 10), ipadx=10, ipady=8)

        self.auto_restart_var = ttk.BooleanVar(value=self.config.get("auto_restart", True))
        ttk.Checkbutton(
            auto_frame, text="程序掛掉時自動重啟",
            variable=self.auto_restart_var, bootstyle="round-toggle",
            command=self._save_settings,
        ).pack(anchor=W, pady=(0, 5))

        self.auto_start_var = ttk.BooleanVar(value=self.config.get("auto_start_on_launch", False))
        ttk.Checkbutton(
            auto_frame, text="啟動管理面板時自動啟動前後端",
            variable=self.auto_start_var, bootstyle="round-toggle",
            command=self._save_settings,
        ).pack(anchor=W, pady=(0, 5))

        self.start_backend_var = ttk.BooleanVar(value=self.config.get("start_backend", True))
        ttk.Checkbutton(
            auto_frame, text="  └ 啟動 Backend",
            variable=self.start_backend_var, bootstyle="round-toggle",
            command=self._save_settings,
        ).pack(anchor=W, padx=(20, 0), pady=(0, 5))

        self.start_frontend_var = ttk.BooleanVar(value=self.config.get("start_frontend", True))
        ttk.Checkbutton(
            auto_frame, text="  └ 啟動 Frontend",
            variable=self.start_frontend_var, bootstyle="round-toggle",
            command=self._save_settings,
        ).pack(anchor=W, padx=(20, 0))

        # 網路設定
        net_frame = ttk.LabelFrame(inner, text="  🌐 網路設定  ")
        net_frame.pack(fill=X, pady=(0, 10), ipadx=10, ipady=8)

        port_frame = ttk.Frame(net_frame)
        port_frame.pack(fill=X, pady=(0, 5))

        ttk.Label(port_frame, text="Backend Port:", width=15).pack(side=LEFT)
        self.backend_port_var = ttk.StringVar(value=str(self.config.get("backend_port", 8000)))
        ttk.Entry(port_frame, textvariable=self.backend_port_var, width=8).pack(side=LEFT)

        fe_port_frame = ttk.Frame(net_frame)
        fe_port_frame.pack(fill=X)

        ttk.Label(fe_port_frame, text="Frontend Port:", width=15).pack(side=LEFT)
        self.frontend_port_var = ttk.StringVar(value=str(self.config.get("frontend_port", 5173)))
        ttk.Entry(fe_port_frame, textvariable=self.frontend_port_var, width=8).pack(side=LEFT)

        # 進階設定
        adv_frame = ttk.LabelFrame(inner, text="  🔧 進階  ")
        adv_frame.pack(fill=X, pady=(0, 10), ipadx=10, ipady=8)

        hc_frame = ttk.Frame(adv_frame)
        hc_frame.pack(fill=X, pady=(0, 5))

        ttk.Label(hc_frame, text="健康檢查間隔 (秒):", width=20).pack(side=LEFT)
        self.hc_interval_var = ttk.StringVar(value=str(self.config.get("health_check_interval", 5)))
        ttk.Spinbox(hc_frame, from_=1, to=60, textvariable=self.hc_interval_var, width=5).pack(side=LEFT)

        max_frame = ttk.Frame(adv_frame)
        max_frame.pack(fill=X, pady=(0, 5))

        ttk.Label(max_frame, text="最大重啟次數:", width=20).pack(side=LEFT)
        self.max_restart_var = ttk.StringVar(value=str(self.config.get("max_restart_attempts", 5)))
        ttk.Spinbox(max_frame, from_=1, to=20, textvariable=self.max_restart_var, width=5).pack(side=LEFT)

        # 儲存按鈕
        ttk.Button(
            inner, text="💾 儲存設定", bootstyle="primary",
            command=self._save_settings,
        ).pack(anchor=E, pady=(5, 0))

    def _build_info_tab(self):
        """系統資訊分頁"""
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  ℹ️ 系統資訊  ")

        self.info_text = ttk.Text(frame, height=20, wrap="word")
        self.info_text.pack(fill=BOTH, expand=True, padx=10, pady=10)
        self.info_text.configure(state="disabled", font=("Consolas", 11))

    # ─── Console 方法 ─────────────────────────────────────

    def _append_console(self, source: str, text: str):
        """
        向 Console 添加一行文字（thread-safe）。

        Args:
            source: "backend", "frontend", "system"
            text: 文字內容
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {"backend": "[BE]", "frontend": "[FE]", "system": "[SYS]"}.get(source, "[??]")
        line = f"[{timestamp}] {prefix} {text}\n"

        with self._console_lock:
            self._console_entries.append((source, line))

            # 限制最大行數
            max_lines = self.config.get("console_max_lines", 5000)
            if len(self._console_entries) > max_lines:
                self._console_entries = self._console_entries[-max_lines:]

        # UI 更新需要在主線程
        current_filter = self.console_filter.get()
        if current_filter == "all" or current_filter == source:
            self.root.after_idle(self._append_console_ui, line)

    def _append_console_ui(self, line: str):
        """在主線程中更新 Console UI"""
        try:
            self.console_text.text.configure(state="normal")
            self.console_text.text.insert("end", line)

            # 限制顯示行數
            max_display = 3000
            current_lines = int(self.console_text.text.index("end-1c").split(".")[0])
            if current_lines > max_display:
                self.console_text.text.delete("1.0", f"{current_lines - max_display}.0")

            self.console_text.text.configure(state="disabled")

            if self.auto_scroll_var.get():
                self.console_text.text.see("end")
        except Exception:
            pass

    def _clear_console(self):
        """清除 Console"""
        with self._console_lock:
            self._console_entries.clear()
        self.console_text.text.configure(state="normal")
        self.console_text.text.delete("1.0", "end")
        self.console_text.text.configure(state="disabled")

    # ─── 回呼方法 ─────────────────────────────────────────

    def _on_process_output(self, name: str, line: str):
        """程序輸出回呼"""
        self._append_console(name, line)

    def _on_status_change(self, name: str, status: ProcessStatus):
        """程序狀態變更回呼"""
        label_text = STATUS_LABELS.get(status, str(status))
        bootstyle = STATUS_COLORS.get(status, "secondary")

        self.root.after_idle(self._update_status_label, name, label_text, bootstyle)

    def _update_status_label(self, name: str, text: str, bootstyle: str):
        """更新狀態標籤（主線程）"""
        if name == "backend":
            self.backend_status_label.configure(text=text, bootstyle=bootstyle)
        elif name == "frontend":
            self.frontend_status_label.configure(text=text, bootstyle=bootstyle)

    # ─── 操作方法 ─────────────────────────────────────────

    def _run_async(self, func, *args):
        """在背景線程中執行函式"""
        thread = threading.Thread(target=func, args=args, daemon=True)
        thread.start()

    def _start_backend(self):
        self.process_manager.start_backend()

    def _stop_backend(self):
        self.process_manager.stop_backend()

    def _restart_backend(self):
        self.process_manager.restart_backend()

    def _start_frontend(self):
        self.process_manager.start_frontend()

    def _stop_frontend(self):
        self.process_manager.stop_frontend()

    def _restart_frontend(self):
        self.process_manager.restart_frontend()

    def _start_all(self):
        self.process_manager.start_all()

    def _stop_all(self):
        self.process_manager.stop_all()

    def _auto_start(self):
        """自動啟動"""
        self._append_console("system", "🚀 自動啟動前後端...")
        if self.config.get("start_backend", True):
            self._run_async(self._start_backend)
        if self.config.get("start_frontend", True):
            self._run_async(self._start_frontend)

    def _git_pull(self):
        self._append_console("system", "━" * 50)
        git_pull(on_output=lambda line: self._append_console("system", line))
        self._update_version_info()

    def _check_updates(self):
        check_for_updates(on_output=lambda line: self._append_console("system", line))

    def _create_venv(self):
        self._append_console("system", "━" * 50)
        create_venv(on_output=lambda line: self._append_console("system", line))
        self.root.after_idle(self._update_install_status)

    def _pip_install(self):
        self._append_console("system", "━" * 50)
        install_python_deps(on_output=lambda line: self._append_console("system", line))
        self.root.after_idle(self._update_install_status)

    def _npm_install(self):
        self._append_console("system", "━" * 50)
        install_frontend_deps(on_output=lambda line: self._append_console("system", line))
        self.root.after_idle(self._update_install_status)

    def _open_env_editor(self):
        self._append_console("system", "📝 開啟 .env 編輯器")
        open_env_editor(self.root, on_saved=lambda: self._append_console("system", "✅ .env 檔案已儲存"))

    def _download_ffmpeg(self):
        self._append_console("system", "━" * 50)
        self.root.after_idle(lambda: self.btn_download_ffmpeg.configure(state="disabled"))
        try:
            download_ffmpeg(on_output=lambda line: self._append_console("system", line))
        finally:
            self.root.after_idle(self._update_install_status)

    def _confirm_reinstall(self):
        """確認重新安裝"""
        from tkinter import messagebox
        result = messagebox.askyesno(
            "確認重新安裝",
            "這將刪除 .venv 和 node_modules 並重新安裝所有依賴。\n\n"
            "⚠️ 此操作需要網路連線且可能耗費較長時間。\n\n"
            "確定要繼續嗎？",
            icon="warning",
        )
        if result:
            self._run_async(self._do_reinstall)

    def _do_reinstall(self):
        self._append_console("system", "━" * 50)
        self._append_console("system", "⚠️ 先停止所有程序...")
        self.process_manager.stop_all()
        import time
        time.sleep(2)
        reinstall_all(on_output=lambda line: self._append_console("system", line))
        self.root.after_idle(self._update_install_status)

    def _open_browser(self):
        """開啟前端頁面"""
        port = self.config.get("frontend_port", 5173)
        webbrowser.open(f"http://localhost:{port}")

    # ─── 系統資訊 ─────────────────────────────────────────

    def _load_system_info(self):
        """載入系統資訊（在背景線程中執行）"""
        self._run_async(self._do_load_system_info)

    def _do_load_system_info(self):
        """實際載入系統資訊"""
        self._append_console("system", "🔍 正在偵測系統資訊...")

        # GPU 偵測
        gpu_info = detect_gpu_info()
        if gpu_info["has_nvidia"]:
            gpu_text = f"{gpu_info['gpu_name']} (CUDA {gpu_info['cuda_version']} → {gpu_info['compute_platform']})"
        else:
            gpu_text = "CPU 模式（未偵測到 NVIDIA GPU）"
        self.root.after_idle(lambda: self.gpu_label.configure(text=gpu_text))

        # 網路
        has_internet = check_internet()
        net_text = "🟢 已連線" if has_internet else "🔴 無連線"
        self.root.after_idle(lambda: self.net_status_label.configure(
            text=net_text,
            bootstyle="success" if has_internet else "danger",
        ))

        # Python & Node
        py_ver = get_python_version()
        node_ver = get_node_version()
        npm_ver = get_npm_version()

        # Git 版本
        version_info = get_current_version() if is_git_repo() else None

        # 更新安裝狀態
        self.root.after_idle(self._update_install_status)
        self.root.after_idle(self._update_version_info)

        # 更新系統資訊分頁
        info_lines = [
            "═" * 50,
            "  Qwen-ASR-GUI Manager 系統資訊",
            "═" * 50,
            "",
            f"  📁 專案路徑:    {PROJECT_ROOT}",
            f"  🐍 Python:      {py_ver}",
            f"  📦 Node.js:     {node_ver}",
            f"  📦 npm:         {npm_ver}",
            "",
            f"  🎮 GPU:         {gpu_text}",
            f"  🌐 網路:        {'已連線' if has_internet else '無連線'}",
            "",
        ]

        if version_info:
            info_lines.extend([
                f"  📌 版本:        {version_info['commit_hash']}",
                f"  📝 最新提交:    {version_info['commit_message']}",
                f"  📅 日期:        {version_info['commit_date']}",
                f"  🌿 分支:        {version_info['branch']}",
                "",
            ])

        if gpu_info["has_nvidia"]:
            info_lines.extend([
                "─" * 50,
                "  GPU 詳細資訊",
                "─" * 50,
                f"  名稱:           {gpu_info['gpu_name']}",
                f"  驅動版本:       {gpu_info['driver_version']}",
                f"  CUDA 版本:      {gpu_info['cuda_version']}",
                f"  PyTorch 索引:   {gpu_info['compute_platform']}",
                f"  Index URL:      {gpu_info['pytorch_index_url']}",
                "",
            ])

        info_lines.extend([
            "─" * 50,
            "  環境狀態",
            "─" * 50,
            f"  .venv:          {'✅ 已建立' if is_venv_exists() else '❌ 未建立'}",
            f"  node_modules:   {'✅ 已安裝' if is_node_modules_exists() else '❌ 未安裝'}",
            f"  FFmpeg:         {'✅ 已安裝' if is_ffmpeg_installed() else '❌ 未安裝'}",
            "",
            "═" * 50,
        ])

        self.root.after_idle(self._update_info_text, "\n".join(info_lines))
        self._append_console("system", "✅ 系統資訊載入完成")

    def _update_info_text(self, text: str):
        """更新系統資訊文字"""
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", text)
        self.info_text.configure(state="disabled")

    def _update_install_status(self):
        """更新安裝狀態顯示"""
        venv = is_venv_exists()
        self.py_venv_label.configure(
            text=f".venv: {'✅ 已建立' if venv else '❌ 未建立'}",
            bootstyle="success" if venv else "danger",
        )

        platform = self.config.get("detected_compute_platform", "")
        if platform:
            self.py_platform_label.configure(text=f"PyTorch 平台: {platform}")
        else:
            self.py_platform_label.configure(text="PyTorch 平台: 尚未偵測")

        nm = is_node_modules_exists()
        self.fe_status_label.configure(
            text=f"node_modules: {'✅ 已安裝' if nm else '❌ 未安裝'}",
            bootstyle="success" if nm else "danger",
        )

        ffmpeg_ok = is_ffmpeg_installed()
        if hasattr(self, 'ffmpeg_status_label'):
            self.ffmpeg_status_label.configure(
                text=f"FFmpeg: {'✅ 已安裝' if ffmpeg_ok else '❌ 未安裝'}",
                bootstyle="success" if ffmpeg_ok else "danger",
            )
            if ffmpeg_ok:
                self.btn_download_ffmpeg.configure(state="disabled")
            else:
                self.btn_download_ffmpeg.configure(state="normal")

    def _update_version_info(self):
        """更新版本顯示"""
        if is_git_repo():
            info = get_current_version()
            self.root.after_idle(
                lambda: self.version_label.configure(
                    text=f"版本: {info['commit_hash']} - {info['commit_message']} ({info['branch']})"
                )
            )

    # ─── 設定 ─────────────────────────────────────────

    def _save_settings(self):
        """儲存設定"""
        self.config["auto_restart"] = self.auto_restart_var.get()
        self.config["auto_start_on_launch"] = self.auto_start_var.get()
        self.config["start_backend"] = self.start_backend_var.get()
        self.config["start_frontend"] = self.start_frontend_var.get()

        try:
            self.config["backend_port"] = int(self.backend_port_var.get())
        except ValueError:
            pass
        try:
            self.config["frontend_port"] = int(self.frontend_port_var.get())
        except ValueError:
            pass
        try:
            self.config["health_check_interval"] = int(self.hc_interval_var.get())
        except ValueError:
            pass
        try:
            self.config["max_restart_attempts"] = int(self.max_restart_var.get())
        except ValueError:
            pass

        save_config(self.config)
        self._append_console("system", "💾 設定已儲存")

    # ─── 關閉 ─────────────────────────────────────────

    def _on_closing(self):
        """關閉視窗前清理"""
        from tkinter import messagebox

        # 檢查是否有程序在運行
        running = []
        if self.process_manager:
            for name in ["backend", "frontend"]:
                if self.process_manager.get_status(name) == ProcessStatus.RUNNING:
                    running.append(name)

        if running:
            result = messagebox.askyesnocancel(
                "關閉管理面板",
                f"以下程序仍在運行: {', '.join(running)}\n\n"
                "• 是 - 停止所有程序並關閉\n"
                "• 否 - 保持程序運行並關閉\n"
                "• 取消 - 返回管理面板",
            )
            if result is None:  # 取消
                return
            elif result:  # 是 — 停止所有
                self._append_console("system", "⏹️ 正在停止所有程序...")
                self.process_manager.cleanup()

        if self.process_manager:
            self.process_manager.stop_health_check()

        self.root.destroy()


def main():
    """管理面板入口"""
    # 設定 logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    app = ManagerApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
