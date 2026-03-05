# -*- coding: utf-8 -*-
"""
Git 操作管理模組

提供 git pull、版本檢查等功能。
所有操作的輸出透過 callback 即時回傳給 GUI。
"""
import subprocess
import logging
from pathlib import Path
from typing import Callable

from manager.network_utils import check_internet

logger = logging.getLogger(__name__)

from manager.config import PROJECT_ROOT


def _run_git_command(
    args: list[str],
    on_output: Callable[[str], None] | None = None,
    cwd: Path | None = None,
) -> tuple[bool, str]:
    """
    執行 git 命令。

    Args:
        args: git 子命令與參數，例如 ["pull"]
        on_output: 輸出回呼函式
        cwd: 工作目錄，預設為專案根目錄

    Returns:
        (success: bool, output: str)
    """
    if cwd is None:
        cwd = PROJECT_ROOT

    cmd = ["git"] + args
    full_output = []

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd),
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )

        for line in iter(process.stdout.readline, ""):
            line = line.rstrip("\n\r")
            full_output.append(line)
            if on_output:
                on_output(line)

        process.wait()
        output = "\n".join(full_output)

        if process.returncode == 0:
            return True, output
        else:
            return False, output

    except FileNotFoundError:
        msg = "❌ 未找到 git 命令，請確認已安裝 Git"
        if on_output:
            on_output(msg)
        return False, msg
    except Exception as e:
        msg = f"❌ 執行 git 命令時發生錯誤: {e}"
        if on_output:
            on_output(msg)
        return False, msg


def git_pull(on_output: Callable[[str], None] | None = None) -> bool:
    """
    執行 git pull 拉取最新程式碼。

    Args:
        on_output: 輸出回呼函式

    Returns:
        bool: 是否成功
    """
    if not check_internet():
        msg = "⚠️ 無網路連線，跳過 git pull"
        if on_output:
            on_output(msg)
        logger.warning(msg)
        return False

    if on_output:
        on_output("📥 正在拉取最新程式碼...")

    success, output = _run_git_command(["pull"], on_output)

    if success:
        if on_output:
            on_output("✅ Git pull 完成")
    else:
        if on_output:
            on_output("❌ Git pull 失敗")

    return success


def check_for_updates(on_output: Callable[[str], None] | None = None) -> bool | None:
    """
    檢查是否有新版本可用。

    Returns:
        True: 有新版本
        False: 已是最新
        None: 檢查失敗
    """
    if not check_internet():
        msg = "⚠️ 無網路連線，無法檢查更新"
        if on_output:
            on_output(msg)
        return None

    if on_output:
        on_output("🔍 正在檢查更新...")

    # 先 fetch
    success, _ = _run_git_command(["fetch"], on_output)
    if not success:
        return None

    # 比較本地與遠端
    success, output = _run_git_command(
        ["status", "-uno"], on_output
    )
    if not success:
        return None

    if "behind" in output.lower():
        if on_output:
            on_output("🆕 有新版本可用！")
        return True
    else:
        if on_output:
            on_output("✅ 已是最新版本")
        return False


def get_current_version() -> dict:
    """
    取得當前版本資訊。

    Returns:
        dict: {
            "commit_hash": str,
            "commit_message": str,
            "commit_date": str,
            "branch": str,
        }
    """
    result = {
        "commit_hash": "unknown",
        "commit_message": "",
        "commit_date": "",
        "branch": "unknown",
    }

    # commit 資訊
    success, output = _run_git_command(
        ["log", "-1", "--format=%h|%s|%ci"]
    )
    if success and output.strip():
        parts = output.strip().split("|", 2)
        if len(parts) >= 3:
            result["commit_hash"] = parts[0]
            result["commit_message"] = parts[1]
            result["commit_date"] = parts[2]

    # branch 名稱
    success, output = _run_git_command(["branch", "--show-current"])
    if success and output.strip():
        result["branch"] = output.strip()

    return result


def is_git_repo() -> bool:
    """檢查專案是否為 git repo"""
    return (PROJECT_ROOT / ".git").is_dir()
