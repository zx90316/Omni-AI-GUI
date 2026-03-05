# -*- coding: utf-8 -*-
"""
Qwen-ASR-GUI Manager — 啟動入口

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

def auto_clone_setup():
    """
    如果在專案目錄之外執行打包後的 .exe，則自動詢問並 git clone 專案。
    此功能僅在 sys.frozen == True 時生效。
    """
    if not getattr(sys, 'frozen', False):
        return

    exe_path = Path(sys.executable).resolve()
    current_dir = exe_path.parent

    # 確認當前目錄是否為專案目錄（藉由辨識是否有 launch.py 或 manager 目錄）
    # 因為打包後我們希望 exe 被放在專案根目錄下
    if (current_dir / "launch.py").exists() or (current_dir / "manager").is_dir():
        return

    # 若不在專案目錄中，表示使用者可能只下載了 exe
    import tkinter as tk
    from tkinter import messagebox
    import threading

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
        "Qwen-ASR-GUI 自動設定",
        "偵測到目前不在 Qwen-ASR-GUI 專案資料夾中。\n\n"
        "是否要自動下載 (git clone) 整個專案，\n並將此管理面板移入專案資料夾中執行？",
        icon="info"
    )

    if not result:
        sys.exit(0)

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

    tk.Label(progress_win, text="正在下載 Qwen-ASR-GUI 專案檔案...", font=("", 12)).pack(pady=20)
    status_label = tk.Label(progress_win, text="請稍候，這可能需要一點時間...")
    status_label.pack()

    def do_clone():
        clone_url = "https://github.com/zx90316/Qwen-ASR-GUI.git"
        
        try:
            # 使用 init + fetch + reset 將專案拉取到當前資料夾（保留現有的 exe 與 _internal）
            creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            
            subprocess.check_call(["git", "init"], cwd=str(current_dir), creationflags=creationflags)
            
            # 確保還沒有 origin，有的話也沒關係（略過建立）
            try:
                subprocess.check_call(["git", "remote", "add", "origin", clone_url], cwd=str(current_dir), creationflags=creationflags)
            except subprocess.CalledProcessError:
                pass
                
            subprocess.check_call(["git", "fetch", "--all"], cwd=str(current_dir), creationflags=creationflags)
            subprocess.check_call(["git", "reset", "--hard", "origin/master"], cwd=str(current_dir), creationflags=creationflags)
            subprocess.check_call(["git", "branch", "--set-upstream-to=origin/master", "master"], cwd=str(current_dir), creationflags=creationflags)

            # 提示成功並在當前目錄重啟
            messagebox.showinfo(
                "下載完成",
                "專案已成功下載至當前資料夾！\n\n按下確定後將會自動啟動管理面板。"
            )

            # 重啟
            subprocess.Popen([str(exe_path)], cwd=str(current_dir))
            os._exit(0)

        except Exception as e:
            messagebox.showerror("錯誤", f"下載與設定過程中發生錯誤:\n{e}")
            os._exit(1)

    threading.Thread(target=do_clone, daemon=True).start()
    root.mainloop()



def ensure_dependencies():
    """確保管理 GUI 的依賴已安裝"""
    deps = {
        "ttkbootstrap": "ttkbootstrap>=1.10.0",
        "dotenv": "python-dotenv",
    }

    missing = []
    for module_name, pip_name in deps.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(pip_name)

    if missing:
        print("=" * 60)
        print("  Qwen-ASR-GUI Manager")
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
