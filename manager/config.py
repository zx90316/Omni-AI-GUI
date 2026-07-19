# -*- coding: utf-8 -*-
"""
管理 GUI 設定檔管理

使用 JSON 檔案儲存管理 GUI 的設定。
設定檔位於專案根目錄下的 manager_config.json。
"""
import json
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

import sys

# 專案根目錄（manager/ 的上層）
project_root_override = os.environ.get("OMNI_AI_PROJECT_ROOT", "").strip()
if project_root_override:
    PROJECT_ROOT = Path(project_root_override).expanduser().resolve()
elif getattr(sys, 'frozen', False):
    # 執行為 PyInstaller 打包的 .exe
    PROJECT_ROOT = Path(sys.executable).parent.resolve()
else:
    # 正常 Python 腳本執行
    PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# 設定檔路徑
CONFIG_FILE = PROJECT_ROOT / "manager_config.json"

# 預設設定
DEFAULT_CONFIG = {
    "auto_restart": True,
    "auto_start_on_launch": True,
    "start_backend": True,
    "start_frontend": True,
    "detected_compute_platform": "",
    "backend_host": "0.0.0.0",
    "backend_port": 8000,
    "frontend_host": "localhost",
    "frontend_port": 5173,
    "health_check_interval": 5,       # 健康檢查間隔（秒）
    "health_probe_timeout": 2,        # 單次 HTTP health probe timeout（秒）
    "health_failure_threshold": 3,    # 連續失敗幾次後重啟
    "startup_timeout": 60,            # 等待服務 readiness 的上限（秒）
    "restart_delay": 3,               # 重啟延遲（秒）
    "max_restart_attempts": 5,        # 最大連續重啟次數
    "console_max_lines": 5000,        # Console 最大行數
    "theme": "darkly",                # ttkbootstrap 主題
}


def _normalize_config(saved: dict) -> dict:
    """Merge defaults and reject values that would break the runtime lifecycle."""
    config = DEFAULT_CONFIG.copy()
    if isinstance(saved, dict):
        config.update(saved)

    for key in ("auto_restart", "auto_start_on_launch", "start_backend", "start_frontend"):
        if not isinstance(config.get(key), bool):
            config[key] = DEFAULT_CONFIG[key]

    for key, minimum, maximum in (
        ("backend_port", 1, 65535),
        ("frontend_port", 1, 65535),
        ("health_check_interval", 1, 3600),
        ("health_probe_timeout", 1, 30),
        ("health_failure_threshold", 1, 20),
        ("startup_timeout", 5, 600),
        ("restart_delay", 0, 3600),
        ("max_restart_attempts", 1, 100),
        ("console_max_lines", 100, 100000),
    ):
        value = config.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            config[key] = DEFAULT_CONFIG[key]

    for key in ("backend_host", "frontend_host", "theme"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            config[key] = DEFAULT_CONFIG[key]
        else:
            config[key] = value.strip()
    return config


def load_config() -> dict:
    """
    載入設定檔。若檔案不存在或格式錯誤，回傳預設設定。
    """
    if not CONFIG_FILE.exists():
        logger.info("設定檔不存在，使用預設設定")
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)

        # 合併預設設定（補上新增的設定項目）
        return _normalize_config(saved)

    except (json.JSONDecodeError, OSError) as e:
        logger.warning("讀取設定檔失敗: %s，使用預設設定", e)
        return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> bool:
    """
    儲存設定到檔案。

    Returns:
        bool: 是否成功儲存
    """
    try:
        normalized = _normalize_config(config)
        temp_file = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
        with open(temp_file, "w", encoding="utf-8", newline="\n") as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, CONFIG_FILE)
        logger.info("設定已儲存到 %s", CONFIG_FILE)
        return True
    except (OSError, TypeError) as e:
        logger.error("儲存設定檔失敗: %s", e)
        try:
            temp_file.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
        return False


def get_venv_python() -> Path:
    """取得 .venv 中的 Python 執行檔路徑"""
    return PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"


def get_venv_pip() -> Path:
    """取得 .venv 中的 pip 執行檔路徑"""
    return PROJECT_ROOT / ".venv" / "Scripts" / "pip.exe"


def get_requirements_path() -> Path:
    """取得 requirements.txt 路徑"""
    return PROJECT_ROOT / "requirements.txt"


def get_frontend_dir() -> Path:
    """取得前端目錄路徑"""
    return PROJECT_ROOT / "frontend"


def is_venv_exists() -> bool:
    """檢查 .venv Python 是否存在且仍可執行。"""
    python = get_venv_python()
    if not python.exists():
        return False
    try:
        result = subprocess.run(
            [str(python), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def is_node_modules_exists() -> bool:
    """檢查 frontend/node_modules 是否已存在"""
    return (get_frontend_dir() / "node_modules").exists()
