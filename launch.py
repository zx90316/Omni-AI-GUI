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
import json
from pathlib import Path

from omni_version import __version__

PROJECT_ROOT_ENV = "OMNI_AI_PROJECT_ROOT"
CLONE_URL = "https://github.com/zx90316/Omni-AI-GUI.git"
BOOTSTRAP_STATE_DIR = "Omni-AI-Manager"
BOOTSTRAP_STATE_FILE = "bootstrap.json"


def is_packaged() -> bool:
    """Return whether this module is running as a Nuitka compiled build."""
    return "__compiled__" in globals()


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


def project_integrity_errors(path: Path) -> list[str]:
    """Return missing files that make a release snapshot unusable."""
    required = (
        "backend/app.py",
        "backend/ocr_correction_map.json",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/src/main.jsx",
        "manager/app.py",
        "manager/requirements.txt",
        ".env.example",
        "manager_config.json",
        "requirements.txt",
    )
    return [item for item in required if not (path / item).is_file()]


def packaged_project_root() -> Path:
    """Resolve the project snapshot beside the compiled Manager."""
    override = get_project_override()
    if override is not None:
        return override
    if is_packaged():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def handle_metadata_commands(argv: list[str] | None = None) -> bool:
    """Handle non-GUI diagnostics used by release verification."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args:
        print(f"Omni AI Manager {__version__}")
        return True
    verify_bootstrap = "--verify-bootstrap" in args
    verify_install = "--verify-install" in args
    if not verify_install and not verify_bootstrap:
        return False

    command = "--verify-bootstrap" if verify_bootstrap else "--verify-install"
    index = args.index(command)
    output_path = None
    if index + 1 < len(args) and not args[index + 1].startswith("--"):
        output_path = Path(args[index + 1]).expanduser().resolve()

    missing: list[str] = []
    override = get_project_override(args, os.environ)
    if verify_bootstrap:
        executable_dir = (
            Path(sys.executable).resolve().parent
            if is_packaged()
            else Path(__file__).resolve().parent
        )
        root = discover_project_root(
            executable_dir,
            explicit=override,
            saved=load_saved_project_root(),
        )
        if root is None:
            root = executable_dir
            missing.append("No complete Omni-AI-GUI project could be discovered.")
        else:
            try:
                activate_project_root(root)
                __import__("manager.app")
            except Exception as exc:
                missing.append(f"Manager project import failed: {exc}")
    else:
        root = packaged_project_root()
        if override is not None:
            missing = project_integrity_errors(root)
            if not missing:
                try:
                    activate_project_root(root)
                    __import__("manager.app")
                except Exception as exc:
                    missing.append(f"Manager project import failed: {exc}")
        elif is_packaged():
            for item in ("Omni-AI-Manager.exe", "LICENSE", "SECURITY.md", "README_RELEASE.txt"):
                if not (root / item).is_file():
                    missing.append(item)
        else:
            missing = project_integrity_errors(root)
    result = {
        "application": "Omni AI Manager",
        "version": __version__,
        "project_root": str(root),
        "status": "ok" if not missing else "error",
        "missing": missing,
    }
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    if missing:
        raise SystemExit(3)
    return True


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


def get_bootstrap_state_file(environ: dict[str, str] | None = None) -> Path:
    """Use per-user state so a read-only EXE directory is still supported."""
    env = os.environ if environ is None else environ
    local_app_data = env.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        base = Path(local_app_data).expanduser()
    else:
        base = Path.home() / ".omni-ai-manager"
        return base / BOOTSTRAP_STATE_FILE
    return base / BOOTSTRAP_STATE_DIR / BOOTSTRAP_STATE_FILE


def load_saved_project_root(state_file: Path | None = None) -> Path | None:
    """Return the last valid project root, ignoring stale or malformed state."""
    path = get_bootstrap_state_file() if state_file is None else Path(state_file)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        project_text = data.get("project_root", "") if isinstance(data, dict) else ""
        if not isinstance(project_text, str) or not project_text.strip():
            return None
        project_root = Path(project_text).expanduser().resolve()
    except (OSError, ValueError, TypeError):
        return None
    return project_root if is_project_directory(project_root) else None


def save_project_root(project_dir: Path, state_file: Path | None = None) -> bool:
    """Persist a validated project root atomically for later direct launches."""
    project_root = Path(project_dir).expanduser().resolve()
    if not is_project_directory(project_root):
        return False
    path = get_bootstrap_state_file() if state_file is None else Path(state_file)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(
            json.dumps({"project_root": str(project_root)}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, path)
        return True
    except OSError:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def discover_project_root(
    executable_dir: Path,
    *,
    explicit: Path | None = None,
    saved: Path | None = None,
) -> Path | None:
    """Find a project beside the EXE or from a previous valid selection."""
    executable_dir = Path(executable_dir).expanduser().resolve()
    candidates = (
        explicit,
        executable_dir,
        executable_dir / "Omni-AI-GUI",
        saved,
    )
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        resolved = Path(candidate).expanduser().resolve()
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        if is_project_directory(resolved):
            return resolved
    return None


def activate_project_root(project_dir: Path) -> Path:
    """Expose an external project tree to compiled Manager imports."""
    resolved = project_dir.expanduser().resolve()
    os.environ[PROJECT_ROOT_ENV] = str(resolved)
    os.chdir(resolved)
    project_text = str(resolved)
    if project_text not in sys.path:
        sys.path.insert(0, project_text)
    return resolved


def configure_project_override() -> Path | None:
    """Validate and publish the external source tree used by a packaged Manager."""
    project_dir = get_project_override()
    if project_dir is None:
        return None
    if not is_project_directory(project_dir):
        raise RuntimeError(f"指定的 Omni AI 專案目錄無效: {project_dir}")
    activated = activate_project_root(project_dir)
    if is_packaged():
        save_project_root(activated)
    return activated


def auto_clone_setup():
    """
    如果在專案目錄之外執行打包後的 .exe，則自動詢問並 git clone 專案。
    此功能僅在 Nuitka 打包後的 executable 生效。
    """
    if not is_packaged():
        return

    exe_path = Path(sys.executable).resolve()
    current_dir = exe_path.parent
    detected_project = discover_project_root(
        current_dir,
        explicit=get_project_override(),
        saved=load_saved_project_root(),
    )
    if detected_project is not None:
        activate_project_root(detected_project)
        save_project_root(detected_project)
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
        save_project_root(target_dir)
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
        save_project_root(target_dir)
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
        "ttkbootstrap": "ttkbootstrap>=1.10.0,<2",
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
    if handle_metadata_commands():
        return

    try:
        configure_project_override()
    except RuntimeError as exc:
        print(f"❌ {exc}")
        raise SystemExit(2) from exc

    # 自動 Clone 檢查與處理
    auto_clone_setup()

    # 確保依賴

    if not is_packaged():
        ensure_dependencies()

    # 啟動管理面板
    from manager.app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
