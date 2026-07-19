# -*- coding: utf-8 -*-
"""Schema-driven graphical editor for the project ``.env`` file."""

from __future__ import annotations

try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import BOTH, BOTTOM, DISABLED, HORIZONTAL, LEFT, NORMAL, RIGHT, W, X, Y
except ImportError:  # pragma: no cover - Manager bootstrap installs it first
    ttk = None

from manager.config import PROJECT_ROOT
from manager.env_schema import (
    DEPRECATED_ENV_KEYS,
    ENV_FIELDS,
    initial_env_values,
    read_env_values,
    validate_env_values,
)


def open_env_editor(parent_window, on_saved=None):
    """Open a modal editor with defaults, examples and live validation."""
    try:
        import dotenv
    except ImportError:
        from tkinter import messagebox

        messagebox.showerror(
            "缺少套件",
            "缺少 python-dotenv，無法讀寫 .env。請先安裝 Python 依賴。",
        )
        return

    env_path = PROJECT_ROOT / ".env"
    existing = read_env_values(env_path)
    initial = initial_env_values(existing)

    top = ttk.Toplevel(parent_window)
    top.title("設定 .env 環境變數")
    top.geometry("820x760")
    top.minsize(680, 560)
    top.transient(parent_window)
    top.grab_set()

    header = ttk.Frame(top, padding=(20, 16, 20, 8))
    header.pack(fill=X)
    ttk.Label(header, text="環境變數設定", font=("", 16, "bold")).pack(anchor=W)
    ttk.Label(
        header,
        text="必要欄位會自動帶入安全預設；帳號、密碼等個人值請依下方範例填寫。",
        bootstyle="secondary",
    ).pack(anchor=W, pady=(4, 0))

    status_frame = ttk.Frame(top, padding=(20, 0, 20, 8))
    status_frame.pack(fill=X)
    status_label = ttk.Label(status_frame, text="正在檢查設定…")
    status_label.pack(anchor=W)

    body = ttk.Frame(top, padding=(20, 0, 20, 8))
    body.pack(fill=BOTH, expand=True)
    canvas = ttk.Canvas(body, highlightthickness=0)
    scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
    scrollable = ttk.Frame(canvas)
    window_id = canvas.create_window((0, 0), window=scrollable, anchor="nw")
    scrollable.bind(
        "<Configure>",
        lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas.bind(
        "<Configure>",
        lambda event: canvas.itemconfigure(window_id, width=event.width),
    )
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side=LEFT, fill=BOTH, expand=True)
    scrollbar.pack(side=RIGHT, fill=Y)

    def on_mousewheel(event):
        try:
            canvas.yview_scroll(int(-event.delta / 120), "units")
        except (AttributeError, ValueError):
            pass

    canvas.bind_all("<MouseWheel>", on_mousewheel)

    variables: dict[str, object] = {}
    widgets: dict[str, object] = {}
    secret_widgets: list[object] = []
    show_secrets = ttk.BooleanVar(value=False)

    grouped: dict[str, list] = {}
    for field in ENV_FIELDS:
        grouped.setdefault(field.group, []).append(field)

    def current_values():
        return {key: variable.get().strip() for key, variable in variables.items()}

    def refresh_validation(*_args):
        result = validate_env_values(current_values())
        if result.valid:
            status_label.configure(
                text="✓ .env 設定完整，可以啟動 Backend 與 Frontend",
                bootstyle="success",
            )
            save_button.configure(state=NORMAL)
        else:
            details = list(result.missing_keys) + list(result.errors)
            preview = "；".join(details[:3])
            if len(details) > 3:
                preview += f"；另有 {len(details) - 3} 項"
            status_label.configure(
                text=f"⚠ 設定尚未完整：{preview}",
                bootstyle="danger",
            )
            save_button.configure(state=DISABLED)

    for group_name, fields in grouped.items():
        group_frame = ttk.LabelFrame(scrollable, text=f"  {group_name}  ", padding=12)
        group_frame.pack(fill=X, pady=(0, 12))

        for field in fields:
            row = ttk.Frame(group_frame)
            row.pack(fill=X, pady=(0, 12))

            label_text = f"{field.label}{' *' if field.required else ''}"
            ttk.Label(row, text=label_text, width=19, font=("", 10, "bold")).pack(
                side=LEFT, anchor=W, padx=(0, 10)
            )

            input_area = ttk.Frame(row)
            input_area.pack(side=LEFT, fill=X, expand=True)
            variable = ttk.StringVar(value=initial[field.key])
            variables[field.key] = variable

            if field.choices:
                widget = ttk.Combobox(
                    input_area,
                    textvariable=variable,
                    values=field.choices,
                    state="normal" if field.editable_choices else "readonly",
                )
            else:
                widget = ttk.Entry(
                    input_area,
                    textvariable=variable,
                    show="•" if field.secret else "",
                )
            widget.pack(fill=X)
            widgets[field.key] = widget
            if field.secret:
                secret_widgets.append(widget)

            hint_parts = [field.description]
            if field.default:
                hint_parts.append(f"預設：{field.default}")
            if field.example and field.example != field.default:
                hint_parts.append(f"範例：{field.example}")
            ttk.Label(
                input_area,
                text="  ".join(hint_parts),
                bootstyle="secondary",
                wraplength=510,
                justify="left",
            ).pack(anchor=W, pady=(3, 0))
            ttk.Label(
                input_area,
                text=field.key,
                font=("Consolas", 8),
                bootstyle="secondary",
            ).pack(anchor=W)
            variable.trace_add("write", refresh_validation)

    def toggle_secrets():
        mask = "" if show_secrets.get() else "•"
        for widget in secret_widgets:
            widget.configure(show=mask)

    footer = ttk.Frame(top, padding=(20, 8, 20, 16))
    footer.pack(fill=X, side=BOTTOM)
    ttk.Separator(top, orient=HORIZONTAL).pack(fill=X, side=BOTTOM)
    ttk.Checkbutton(
        footer,
        text="顯示敏感值",
        variable=show_secrets,
        command=toggle_secrets,
        bootstyle="round-toggle",
    ).pack(side=LEFT)

    def close_editor():
        canvas.unbind_all("<MouseWheel>")
        top.destroy()

    def save_env():
        values = current_values()
        result = validate_env_values(values)
        if not result.valid:
            from tkinter import messagebox

            messagebox.showerror(
                "設定尚未完整",
                "請先修正以下項目：\n\n" + "\n".join(
                    [f"缺少 {key}" for key in result.missing_keys]
                    + list(result.errors)
                ),
                parent=top,
            )
            return
        try:
            env_path.parent.mkdir(parents=True, exist_ok=True)
            env_path.touch(exist_ok=True)
            for key, value in values.items():
                dotenv.set_key(env_path, key, value, quote_mode="always")
            for key in DEPRECATED_ENV_KEYS:
                if key in existing:
                    dotenv.unset_key(env_path, key)

            from tkinter import messagebox

            messagebox.showinfo(
                "儲存成功",
                ".env 已完成驗證並儲存。變更會在下次啟動或重新啟動服務時套用。",
                parent=top,
            )
            close_editor()
            if on_saved:
                on_saved()
        except Exception as exc:
            from tkinter import messagebox

            messagebox.showerror("儲存失敗", f"無法儲存 .env：\n{exc}", parent=top)

    save_button = ttk.Button(
        footer,
        text="儲存設定",
        bootstyle="success",
        command=save_env,
        width=15,
    )
    save_button.pack(side=RIGHT, padx=(10, 0))
    ttk.Button(
        footer,
        text="取消",
        bootstyle="secondary",
        command=close_editor,
        width=12,
    ).pack(side=RIGHT)

    top.protocol("WM_DELETE_WINDOW", close_editor)
    refresh_validation()
