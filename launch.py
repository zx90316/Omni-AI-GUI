# -*- coding: utf-8 -*-
"""
Omni AI Manager — 啟動入口

使用系統 Python 執行，不需要 .venv。
會自動安裝缺少的管理 GUI 依賴（ttkbootstrap, psutil）。

使用方式:
    python launch.py
    或直接雙擊執行
"""
import sys
import os
import subprocess
import importlib
import shutil
from pathlib import Path

PROJECT_ROOT_ENV = "OMNI_AI_PROJECT_ROOT"
CLONE_URL = "https://github.com/zx90316/Omni-AI-GUI.git"


def is_project_directory(path: Path) -> bool:
    """Require the core source tree, not just one coincidentally named file."""
    return all(
        candidate.exists()
        for candidate in (
            path / "manager" / "app.py",
            path / "backend" / "app.py",
            path / "frontend" / "package.json",
        )
    )


def get_project_override(
    argv: list[str] | None = None,
    environ: dict[str, str] | None = None,
) -> Path | None:
    """Read an explicit project root without letting argparse consume GUI args."""
    args = sys.argv[1:] if argv is None else argv
    env = os.environ if environ is None else environ
    for index, arg in enumerate(args):
        if arg == "--project-dir" and index + 1 < len(args):
            return Path(args[index + 1]).expanduser().resolve()
        if arg.startswith("--project-dir="):
            return Path(arg.split("=", 1)[1]).expanduser().resolve()
    value = env.get(PROJECT_ROOT_ENV, "").strip()
    return Path(value).expanduser().resolve() if value else None


def configure_project_override() -> Path | None:
    """Validate and publish the external source tree used by a packaged Manager."""
    project_dir = get_project_override()
    if project_dir is None:
        return None
    if not is_project_directory(project_dir):
        raise RuntimeError(f"指定的 Omni AI 專案目錄無效: {project_dir}")
    os.environ[PROJECT_ROOT_ENV] = str(project_dir)
    os.chdir(project_dir)
    return project_dir


def auto_clone_setup():
    """
    如果在專案目錄之外執行打包後的 .exe，則自動詢問並 git clone 專案。
    此功能僅在 sys.frozen == True 時生效。
    """
    if not getattr(sys, 'frozen', False):
        return

    override = get_project_override()
    if override is not None and is_project_directory(override):
        return

    exe_path = Path(sys.executable).resolve()
    current_dir = exe_path.parent

    # 確認當前目錄是否為專案目錄（藉由辨識是否有 launch.py 或 manager 目錄）
    # 因為打包後我們希望 exe 被放在專案根目錄下
    if is_project_directory(current_dir):
        return

    # 若不在專案目錄中，表示使用者可能只下載了 exe
    import tkinter as tk
    from tkinter import filedialog, messagebox
    import threading
    import queue

    root = tk.Tk()
    root.withdraw()

    # 檢查是否安裝 git
    if not shutil.which("git"):
        messagebox.showerror(
            "缺少 Git",
            "系統未安裝 Git，無法自動下載專案。\n請先安裝 Git 並加入系統 PATH 後再試一次！"
        )
        sys.exit(1)

    result = messagebox.askyesno(
        "Omni AI 自動設定",
        "偵測到目前不在 Omni AI 專案資料夾中。\n\n"
        "是否要將完整專案下載到新的資料夾後啟動？\n"
        "現有 EXE 所在目錄不會被修改。",
        icon="info"
    )

    if not result:
        sys.exit(0)

    install_parent = filedialog.askdirectory(
        title="選擇 Omni AI 安裝位置",
        initialdir=str(current_dir),
        mustexist=True,
    )
    if not install_parent:
        sys.exit(0)
    target_dir = Path(install_parent).resolve() / "Omni-AI-GUI"
    if target_dir.exists() and is_project_directory(target_dir):
        use_existing = messagebox.askyesno(
            "使用既有專案",
            f"已找到 Omni AI 專案：\n{target_dir}\n\n是否直接使用？",
        )
        if not use_existing:
            sys.exit(0)
        child_env = os.environ.copy()
        child_env[PROJECT_ROOT_ENV] = str(target_dir)
        subprocess.Popen(
            [str(exe_path), "--project-dir", str(target_dir)],
            cwd=str(target_dir),
            env=child_env,
        )
        os._exit(0)
    if target_dir.exists() and any(target_dir.iterdir()):
        messagebox.showerror(
            "安裝位置已有檔案",
            f"為避免覆蓋資料，請移開或重新命名此目錄後再試：\n{target_dir}",
        )
        sys.exit(1)

    # 開始 Clone
    progress_win = tk.Toplevel(root)
    progress_win.title("自動下載中")
    progress_win.geometry("400x150")
    progress_win.resizable(False, False)
    
    # 嘗試將視窗置中
    progress_win.update_idletasks()
    width = progress_win.winfo_width()
    frm_width = progress_win.winfo_rootx() - progress_win.winfo_x()
    win_width = width + 2 * frm_width
    height = progress_win.winfo_height()
    titlebar_height = progress_win.winfo_rooty() - progress_win.winfo_y()
    win_height = height + titlebar_height + frm_width
    x = progress_win.winfo_screenwidth() // 2 - win_width // 2
    y = progress_win.winfo_screenheight() // 2 - win_height // 2
    progress_win.geometry(f'{width}x{height}+{x}+{y}')

    tk.Label(progress_win, text="正在下載 Omni AI 專案檔案...", font=("", 12)).pack(pady=20)
    status_label = tk.Label(progress_win, text="請稍候，這可能需要一點時間...")
    status_label.pack()

    results: queue.Queue[tuple[bool, str]] = queue.Queue(maxsize=1)

    def do_clone():
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        completed = subprocess.run(
            ["git", "clone", "--depth", "1", CLONE_URL, str(target_dir)],
            cwd=str(Path(install_parent).resolve()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        message = (completed.stdout + "\n" + completed.stderr).strip()
        results.put((completed.returncode == 0, message))

    def poll_clone_result():
        try:
            success, detail = results.get_nowait()
        except queue.Empty:
            root.after(100, poll_clone_result)
            return

        progress_win.destroy()
        if not success or not is_project_directory(target_dir):
            messagebox.showerror(
                "下載失敗",
                f"無法建立完整的 Omni AI 專案：\n{detail or '專案結構驗證失敗'}",
            )
            root.destroy()
            return

        messagebox.showinfo(
            "下載完成",
            f"專案已安全下載至：\n{target_dir}\n\n按下確定後啟動管理面板。",
        )
        child_env = os.environ.copy()
        child_env[PROJECT_ROOT_ENV] = str(target_dir)
        subprocess.Popen(
            [str(exe_path), "--project-dir", str(target_dir)],
            cwd=str(target_dir),
            env=child_env,
        )
        root.destroy()

    threading.Thread(target=do_clone, daemon=True, name="project-clone").start()
    root.after(100, poll_clone_result)
    root.mainloop()
    raise SystemExit(0)



def ensure_dependencies():
    """確保管理 GUI 的依賴已安裝"""
    deps = {
        "ttkbootstrap": "ttkbootstrap>=1.10.0",
        "dotenv": "python-dotenv",
        "psutil": "psutil>=5.9",
    }

    missing = []
    for module_name, pip_name in deps.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(pip_name)

    if missing:
        print("=" * 60)
        print("  Omni AI Manager")
        print("  正在安裝必要的依賴套件...")
        print("=" * 60)
        for dep in missing:
            print(f"  📦 安裝 {dep}...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", dep],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        print("  ✅ 依賴安裝完成！")
        print("=" * 60)


def main():
    try:
        configure_project_override()
    except RuntimeError as exc:
        print(f"❌ {exc}")
        raise SystemExit(2) from exc

    # 自動 Clone 檢查與處理
    auto_clone_setup()

    # 確保依賴

    if not getattr(sys, 'frozen', False):
        ensure_dependencies()

    # 啟動管理面板
    from manager.app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
