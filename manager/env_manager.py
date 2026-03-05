# -*- coding: utf-8 -*-
"""
虛擬環境與依賴管理模組

負責建立 .venv、安裝 Python 依賴（含 PyTorch CUDA 偵測）、
安裝前端依賴 (npm install)、以及完整重新安裝功能。
"""
import os
import sys
import shutil
import subprocess
import logging
import re
from pathlib import Path
from typing import Callable

from manager.network_utils import check_internet
from manager.gpu_detector import detect_gpu_info, get_pytorch_install_args
from manager.config import (
    PROJECT_ROOT,
    get_venv_python,
    get_venv_pip,
    get_requirements_path,
    get_frontend_dir,
    is_venv_exists,
    is_node_modules_exists,
    load_config,
    save_config,
)

logger = logging.getLogger(__name__)


def _run_command(
    cmd: list[str],
    on_output: Callable[[str], None] | None = None,
    cwd: Path | str | None = None,
    env: dict | None = None,
) -> tuple[bool, str]:
    """
    執行外部命令，即時回傳輸出。

    Args:
        cmd: 命令列表
        on_output: 輸出回呼函式
        cwd: 工作目錄
        env: 環境變數（會合併到現有環境）

    Returns:
        (success: bool, full_output: str)
    """
    if cwd is None:
        cwd = PROJECT_ROOT

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    full_output = []

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(cwd),
            env=run_env,
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
            if on_output:
                on_output(f"⚠️ 命令結束，退出碼: {process.returncode}")
            return False, output

    except FileNotFoundError:
        msg = f"❌ 未找到命令: {cmd[0]}"
        if on_output:
            on_output(msg)
        return False, msg
    except Exception as e:
        msg = f"❌ 執行命令時發生錯誤: {e}"
        if on_output:
            on_output(msg)
        return False, msg


def create_venv(on_output: Callable[[str], None] | None = None) -> bool:
    """
    建立 Python 虛擬環境 .venv。

    Returns:
        bool: 是否成功
    """
    if is_venv_exists():
        if on_output:
            on_output("ℹ️ .venv 已存在，跳過建立")
        return True

    if on_output:
        on_output("🔧 正在建立虛擬環境 .venv ...")

    # 使用當前系統 Python 建立 venv (如果是 EXE，則尋找系統 python)
    if getattr(sys, 'frozen', False):
        import shutil
        python_exe = shutil.which("python") or shutil.which("python3") or shutil.which("py")
        if not python_exe:
            if on_output:
                on_output("❌ 找不到系統 Python，請確認已安裝 Python 並加入 PATH")
            return False
    else:
        python_exe = sys.executable
    success, _ = _run_command(
        [python_exe, "-m", "venv", str(PROJECT_ROOT / ".venv")],
        on_output=on_output,
    )

    if success:
        if on_output:
            on_output("✅ 虛擬環境建立成功")

        # 升級 pip
        if on_output:
            on_output("📦 正在升級 pip ...")
        _run_command(
            [str(get_venv_python()), "-m", "pip", "install", "--upgrade", "pip"],
            on_output=on_output,
        )
    else:
        if on_output:
            on_output("❌ 虛擬環境建立失敗")

    return success


def _generate_temp_requirements(compute_platform: str) -> Path:
    """
    根據偵測到的 compute platform 產生暫時的 requirements 檔案，
    將 --extra-index-url 替換為正確的版本。

    Returns:
        Path: 暫時 requirements 檔案路徑
    """
    req_path = get_requirements_path()
    temp_path = PROJECT_ROOT / "requirements_temp.txt"

    with open(req_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 替換 --extra-index-url 行
    index_url = f"https://download.pytorch.org/whl/{compute_platform}"
    content = re.sub(
        r"--extra-index-url\s+https://download\.pytorch\.org/whl/\S+",
        f"--extra-index-url {index_url}",
        content,
    )

    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(content)

    return temp_path


def install_python_deps(
    on_output: Callable[[str], None] | None = None,
    force_platform: str | None = None,
) -> bool:
    """
    安裝 Python 依賴。

    會自動偵測 GPU 並選擇適合的 PyTorch 版本。

    Args:
        on_output: 輸出回呼函式
        force_platform: 強制指定 compute platform（例如 "cu128", "cpu"），
                        None 時自動偵測

    Returns:
        bool: 是否成功
    """
    if not check_internet():
        msg = "⚠️ 無網路連線，跳過 pip install"
        if on_output:
            on_output(msg)
        logger.warning(msg)
        return False

    if not is_venv_exists():
        if on_output:
            on_output("⚠️ .venv 不存在，先建立虛擬環境")
        if not create_venv(on_output):
            return False

    # GPU 偵測
    if force_platform:
        compute_platform = force_platform
    else:
        if on_output:
            on_output("🔍 正在偵測 GPU...")
        gpu_info = detect_gpu_info()
        compute_platform = gpu_info["compute_platform"]

        if gpu_info["has_nvidia"]:
            if on_output:
                on_output(f"🎮 偵測到 GPU: {gpu_info['gpu_name']}")
                on_output(f"   CUDA: {gpu_info['cuda_version']} → PyTorch {compute_platform}")
        else:
            if on_output:
                on_output("💻 未偵測到 NVIDIA GPU，使用 CPU 模式")

    # 儲存偵測結果到設定
    config = load_config()
    config["detected_compute_platform"] = compute_platform
    save_config(config)

    # 產生正確的 requirements 檔案
    temp_req = _generate_temp_requirements(compute_platform)

    if on_output:
        on_output(f"📦 正在安裝 Python 依賴（{compute_platform}）...")

    pip_exe = str(get_venv_pip())
    success, _ = _run_command(
        [pip_exe, "install", "-r", str(temp_req)],
        on_output=on_output,
    )

    # 清理暫時檔案
    try:
        temp_req.unlink()
    except OSError:
        pass

    if success:
        if on_output:
            on_output("✅ Python 依賴安裝完成")
    else:
        if on_output:
            on_output("❌ Python 依賴安裝失敗")

    return success


def install_frontend_deps(on_output: Callable[[str], None] | None = None) -> bool:
    """
    安裝前端依賴 (npm install)。

    Returns:
        bool: 是否成功
    """
    if not check_internet():
        msg = "⚠️ 無網路連線，跳過 npm install"
        if on_output:
            on_output(msg)
        logger.warning(msg)
        return False

    frontend_dir = get_frontend_dir()
    if not (frontend_dir / "package.json").exists():
        msg = "❌ 未找到 frontend/package.json"
        if on_output:
            on_output(msg)
        return False

    if on_output:
        on_output("📦 正在安裝前端依賴 (npm install)...")

    # 尋找 npm
    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"

    success, _ = _run_command(
        [npm_cmd, "install"],
        on_output=on_output,
        cwd=frontend_dir,
    )

    if success:
        if on_output:
            on_output("✅ 前端依賴安裝完成")
    else:
        if on_output:
            on_output("❌ 前端依賴安裝失敗")

    return success


def _is_running_from_venv() -> bool:
    """檢查當前 Python 是否從 .venv 中執行"""
    venv_dir = str(PROJECT_ROOT / ".venv")
    return sys.executable.startswith(venv_dir)


def _robust_rmtree(
    path: Path,
    on_output: Callable[[str], None] | None = None,
) -> bool:
    """
    穩健地刪除目錄，處理 Windows 上的權限與檔案鎖定問題。

    策略：
    1. 先嘗試 shutil.rmtree + onerror 修改權限後重試
    2. 若失敗，在 Windows 上嘗試 rd /s /q 命令
    """
    import stat
    import time

    def _onerror(func, filepath, exc_info):
        """shutil.rmtree 的錯誤回呼：移除唯讀屬性後重試"""
        try:
            os.chmod(filepath, stat.S_IWRITE)
            func(filepath)
        except Exception:
            pass  # 靜默跳過仍然失敗的檔案

    try:
        shutil.rmtree(str(path), onerror=_onerror)
        if not path.exists():
            return True
    except Exception:
        pass

    # 備用方案：Windows 上使用 rd /s /q
    if os.name == "nt" and path.exists():
        if on_output:
            on_output("   使用系統命令重試刪除...")
        try:
            result = subprocess.run(
                ["cmd", "/c", "rd", "/s", "/q", str(path)],
                capture_output=True, text=True, timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            time.sleep(1)  # 等待檔案系統同步
            if not path.exists():
                return True
        except Exception:
            pass

    return not path.exists()


def reinstall_all(on_output: Callable[[str], None] | None = None) -> bool:
    """
    完整重新安裝：刪除 .venv 和 node_modules，重新建立並安裝所有依賴。

    Returns:
        bool: 是否全部成功
    """
    if not check_internet():
        msg = "⚠️ 無網路連線，無法執行重新安裝"
        if on_output:
            on_output(msg)
        return False

    # 檢查是否從 .venv 中執行
    if _is_running_from_venv():
        if on_output:
            on_output("⚠️ 管理面板正從 .venv 中執行，部分檔案可能被鎖定")
            on_output("   建議改用系統 Python 執行: python launch.py")
            on_output("   仍將嘗試刪除，被鎖定的檔案會在下次啟動時清理")

    success = True

    # 1. 刪除 .venv
    venv_dir = PROJECT_ROOT / ".venv"
    if venv_dir.exists():
        if on_output:
            on_output("🗑️ 正在刪除 .venv ...")
        if _robust_rmtree(venv_dir, on_output):
            if on_output:
                on_output("✅ .venv 已刪除")
        else:
            if on_output:
                on_output("❌ 部分 .venv 檔案被鎖定無法刪除（正在使用中）")
                on_output("   請關閉管理面板後手動刪除 .venv 資料夾，再重新執行")
            success = False

    # 2. 刪除 node_modules
    node_modules = get_frontend_dir() / "node_modules"
    if node_modules.exists():
        if on_output:
            on_output("🗑️ 正在刪除 node_modules ...")
        if _robust_rmtree(node_modules, on_output):
            if on_output:
                on_output("✅ node_modules 已刪除")
        else:
            if on_output:
                on_output("❌ 部分 node_modules 檔案被鎖定無法刪除")
            success = False

    # 3. 重新建立 .venv
    if not create_venv(on_output):
        success = False

    # 4. 安裝 Python 依賴
    if not install_python_deps(on_output):
        success = False

    # 5. 安裝前端依賴
    if not install_frontend_deps(on_output):
        success = False

    if success:
        if on_output:
            on_output("🎉 完整重新安裝成功！")
    else:
        if on_output:
            on_output("⚠️ 重新安裝過程中部分步驟失敗，請查看上方訊息")

    return success


def get_python_version() -> str:
    """取得 .venv 中 Python 的版本"""
    if not is_venv_exists():
        return "未安裝"

    try:
        result = subprocess.run(
            [str(get_venv_python()), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "Unknown"


def get_node_version() -> str:
    """取得 Node.js 版本"""
    try:
        node_cmd = "node.exe" if os.name == "nt" else "node"
        result = subprocess.run(
            [node_cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "未安裝"


def get_npm_version() -> str:
    """取得 npm 版本"""
    try:
        npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
        result = subprocess.run(
            [npm_cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "未安裝"
