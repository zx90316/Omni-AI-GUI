# -*- coding: utf-8 -*-
"""
.env 圖形化編輯器
"""
import os
from pathlib import Path
try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
except ImportError:
    pass

from manager.config import PROJECT_ROOT


def open_env_editor(parent_window, on_saved=None):
    """開啟 .env 編輯的獨立視窗"""
    try:
        import dotenv
    except ImportError:
        from tkinter import messagebox
        messagebox.showerror("錯誤", "缺少 python-dotenv 套件，無法解析 .env 檔案。\n請先安裝 Python 依賴。")
        return

    env_path = PROJECT_ROOT / ".env"
    example_path = PROJECT_ROOT / ".env.example"

    # 讀取現有與範本的變數
    existing_vars = dotenv.dotenv_values(env_path) if env_path.exists() else {}
    example_vars = dotenv.dotenv_values(example_path) if example_path.exists() else {}

    # 合併所有金鑰，保留順序
    all_keys = list(example_vars.keys())
    for k in existing_vars.keys():
        if k not in all_keys:
            all_keys.append(k)

    # 建立視窗
    top = ttk.Toplevel(parent_window)
    top.title("📝 設定 .env 參數")
    top.geometry("600x650")
    top.minsize(500, 500)
    top.transient(parent_window)
    top.grab_set()

    # 主容器
    main_frame = ttk.Frame(top, padding=20)
    main_frame.pack(fill=BOTH, expand=True)

    ttk.Label(
        main_frame,
        text="請在此設定環境變數 (.env)",
        font=("", 14, "bold"),
    ).pack(anchor=W, pady=(0, 15))

    # Canvas 捲動區域
    canvas = ttk.Canvas(main_frame, highlightthickness=0)
    scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
    scrollable_frame = ttk.Frame(canvas)

    scrollable_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )
    
    # 支援滑鼠滾輪
    def _on_mousewheel(event):
        try:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass
        
    canvas.bind_all("<MouseWheel>", _on_mousewheel)

    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw", width=530)
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side=LEFT, fill=BOTH, expand=True)
    scrollbar.pack(side=RIGHT, fill=Y)

    # 動態產生表單
    entries = {}
    
    for key in all_keys:
        row = ttk.Frame(scrollable_frame)
        row.pack(fill=X, pady=5)
        
        ttk.Label(row, text=key, width=20, font=("", 10, "bold")).pack(side=LEFT, anchor=N, pady=(5, 0))
        
        # 使用 Text 或 Entry 取決於可能的大小，但一般 Entry 夠用
        val_var = ttk.StringVar(value=existing_vars.get(key, ""))
        entry = ttk.Entry(row, textvariable=val_var)
        entry.pack(side=LEFT, fill=X, expand=True, padx=(5, 0))
        
        # 提示文字 (從 example 拿)
        example_val = example_vars.get(key, "")
        if example_val and not val_var.get():
            # placeholder behavior for empty fields
            pass
            
        entries[key] = val_var

    # 儲存與取消按鈕
    btn_frame = ttk.Frame(top, padding=10)
    btn_frame.pack(fill=X, side=BOTTOM)
    ttk.Separator(top, orient=HORIZONTAL).pack(fill=X, side=BOTTOM)

    def save_env():
        try:
            # 如果 .env 不存在，先建一個空的
            if not env_path.exists():
                env_path.touch()
                
            for k, string_var in entries.items():
                val = string_var.get()
                dotenv.set_key(env_path, k, val, quote_mode="always")
                
            from tkinter import messagebox
            messagebox.showinfo("成功", ".env 設定已儲存成功！\n部分變更可能需要重新啟動 Backend 才會生效。")
            top.destroy()
            
            if on_saved:
                on_saved()
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("錯誤", f"儲存 .env 時發生錯誤：\n{e}")

    ttk.Button(btn_frame, text="💾 儲存 (Save)", bootstyle="success", command=save_env, width=15).pack(side=RIGHT, padx=(10, 0))
    ttk.Button(btn_frame, text="❌ 取消 (Cancel)", bootstyle="secondary", command=top.destroy, width=15).pack(side=RIGHT)
    
    # 清理滾輪事件綁定
    def on_close():
        canvas.unbind_all("<MouseWheel>")
        top.destroy()
        
    top.protocol("WM_DELETE_WINDOW", on_close)

