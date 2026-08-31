from __future__ import annotations

import tkinter as tk
from tkinter import ttk


BG = "#10151d"
SURFACE = "#18202b"
SURFACE_ALT = "#202b39"
BORDER = "#334154"
TEXT = "#e8eef7"
MUTED = "#a6b2c2"
ACCENT = "#4b96ff"
SUCCESS = "#58c58a"
WARNING = "#efb85a"
ERROR = "#ef6b73"


def apply_dark_style(root: tk.Tk) -> None:
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.option_add("*Font", ("Segoe UI", 9))
    style.configure(".", background=SURFACE, foreground=TEXT, fieldbackground=SURFACE_ALT)
    style.configure("TFrame", background=SURFACE)
    style.configure("Root.TFrame", background=BG)
    style.configure("TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Muted.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 16))
    style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
    style.configure("Status.TLabel", background=SURFACE_ALT, foreground=TEXT, padding=(9, 6))
    style.configure("Good.Status.TLabel", background=SURFACE_ALT, foreground=SUCCESS, padding=(9, 6))
    style.configure("Warn.Status.TLabel", background=SURFACE_ALT, foreground=WARNING, padding=(9, 6))
    style.configure("Bad.Status.TLabel", background=SURFACE_ALT, foreground=ERROR, padding=(9, 6))
    style.configure("TEntry", fieldbackground=SURFACE_ALT, foreground=TEXT, insertcolor=TEXT, bordercolor=BORDER)
    style.configure("TCombobox", fieldbackground=SURFACE_ALT, foreground=TEXT, arrowsize=14)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE_ALT)],
        foreground=[("readonly", TEXT)],
        selectbackground=[("readonly", SURFACE_ALT)],
        selectforeground=[("readonly", TEXT)],
    )
    style.configure("TCheckbutton", background=SURFACE, foreground=TEXT)
    style.map("TCheckbutton", background=[("active", SURFACE)], foreground=[("active", TEXT)])
    style.configure("TButton", background=SURFACE_ALT, foreground=TEXT, padding=(10, 6), bordercolor=BORDER)
    style.map("TButton", background=[("active", "#29384a")])
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", bordercolor=ACCENT)
    style.map("Accent.TButton", background=[("active", "#63a6ff")])
    style.configure("Treeview", background=SURFACE_ALT, fieldbackground=SURFACE_ALT, foreground=TEXT, rowheight=25)
    style.configure("Treeview.Heading", background=SURFACE, foreground=TEXT, font=("Segoe UI Semibold", 9))
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])
    style.configure("TLabelframe", background=SURFACE, foreground=TEXT, bordercolor=BORDER)
    style.configure("TLabelframe.Label", background=SURFACE, foreground=TEXT, font=("Segoe UI Semibold", 9))
    style.configure("Vertical.TScrollbar", background=SURFACE_ALT, troughcolor=SURFACE)


def make_log_widget(parent: tk.Widget, *, height: int = 9) -> tuple[tk.Text, ttk.Scrollbar]:
    text = tk.Text(
        parent,
        height=height,
        wrap="word",
        bg=SURFACE_ALT,
        fg=TEXT,
        insertbackground=TEXT,
        relief="flat",
        borderwidth=0,
        padx=8,
        pady=7,
        state="disabled",
    )
    scrollbar = ttk.Scrollbar(parent, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scrollbar.set)
    text.tag_configure("info", foreground=TEXT)
    text.tag_configure("success", foreground=SUCCESS)
    text.tag_configure("warning", foreground=WARNING)
    text.tag_configure("error", foreground=ERROR)
    return text, scrollbar


def append_log(widget: tk.Text, line: str, level: str = "info", *, max_lines: int = 500) -> None:
    widget.configure(state="normal")
    widget.insert("end", line.rstrip() + "\n", level if level in {"info", "success", "warning", "error"} else "info")
    line_count = int(widget.index("end-1c").split(".")[0])
    if line_count > max_lines:
        widget.delete("1.0", f"{line_count - max_lines}.0")
    widget.see("end")
    widget.configure(state="disabled")


def add_labeled_entry(
    parent: ttk.Frame | ttk.LabelFrame,
    row: int,
    label: str,
    variable: tk.Variable,
    *,
    column: int = 0,
    width: int = 24,
    show: str | None = None,
) -> ttk.Entry:
    ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(0, 8), pady=4)
    entry = ttk.Entry(parent, textvariable=variable, width=width, show=show or "")
    entry.grid(row=row, column=column + 1, sticky="ew", pady=4)
    parent.columnconfigure(column + 1, weight=1)
    return entry
