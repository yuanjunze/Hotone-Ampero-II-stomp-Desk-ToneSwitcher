#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ampero II Stomp 桌面 MIDI 控制器（中文界面 / 深色风格）

功能：
- 自动识别 MIDI 输出端口
- 选择 MIDI 端口与通道
- 直接切换到任意 Patch：P00-1 ~ P99-3
- 上一音色 / 下一音色
- 上一组 / 下一组（保持当前 slot）
- 切换 Scene 1~5
- Tuner 开 / 关
- 记住上次使用的端口 / 通道 / bank
- 自动尝试加载 icon.ico / icon.png 作为程序图标
- 兼容 PyInstaller 打包

依赖：
    pip install mido python-rtmidi

运行：
    python ampero_gui.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import ttk, messagebox

# ---- PyInstaller / mido backend 兼容 ----
try:
    import rtmidi  # noqa: F401
except Exception:
    rtmidi = None  # noqa: F841

try:
    import mido.backends.rtmidi  # noqa: F401
except Exception:
    pass

try:
    import mido
except ImportError as exc:
    raise SystemExit(
        "缺少依赖 mido\n请先安装：pip install mido python-rtmidi"
    ) from exc

try:
    mido.set_backend("mido.backends.rtmidi")
except Exception:
    pass


TOTAL_BANKS = 100
SLOTS_PER_BANK = 3
CONFIG_PATH = Path.home() / ".ampero_gui_config.json"
WINDOW_TITLE = "Ampero II Stomp 控制器"

# 配色：参考深色音频控制面板风格
BG = "#15171d"
PANEL = "#20232b"
PANEL_2 = "#262a33"
PANEL_3 = "#10131a"
SIDEBAR = "#1a1d24"
TEXT = "#f2f4f8"
TEXT_DIM = "#b5bcc8"
TEXT_SOFT = "#8e97a6"
ACCENT = "#17c964"
ACCENT_RED = "#ff4d5d"
BUTTON = "#2b303a"
BUTTON_HOVER = "#353b47"
BUTTON_ACTIVE = "#3d4453"
WARNING = "#ffb020"
ERROR = "#ff5c6c"
BORDER = "#343945"
PATCH_COLORS = ["#d14b56", "#9f62ff", "#4a8dff"]
SCENE_COLORS = ["#2e9bff", "#1fbf75", "#ff9f1a", "#b86bff", "#ff5f7a"]
FONT_FAMILY = "Microsoft YaHei UI"


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = get_app_dir()


def get_resource_dirs() -> list[Path]:
    """
    图标搜索顺序：
    1) exe 同目录 / 脚本同目录
    2) PyInstaller onefile 解包目录 _MEIPASS
    """
    dirs: list[Path] = []
    dirs.append(APP_DIR)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass)
        if p not in dirs:
            dirs.append(p)
    return dirs


def find_resource(filename: str) -> Optional[Path]:
    for d in get_resource_dirs():
        p = d / filename
        if p.exists():
            return p
    return None


@dataclass(frozen=True)
class PatchAddress:
    bank: int
    slot: int

    @property
    def linear_index(self) -> int:
        return self.bank * SLOTS_PER_BANK + (self.slot - 1)

    @property
    def cc0_value(self) -> int:
        return self.linear_index // 128

    @property
    def program_change_value(self) -> int:
        return self.linear_index % 128

    @property
    def patch_label(self) -> str:
        return f"P{self.bank:02d}-{self.slot}"

    @classmethod
    def from_bank_slot(cls, bank: int, slot: int) -> "PatchAddress":
        if not (0 <= bank <= 99):
            raise ValueError("bank 必须在 0..99")
        if slot not in (1, 2, 3):
            raise ValueError("slot 必须是 1、2 或 3")
        return cls(bank=bank, slot=slot)

    @classmethod
    def from_patch_string(cls, patch: str) -> "PatchAddress":
        match = re.fullmatch(r"(?:P)?(\d{1,2})-(1|2|3)", patch.strip(), re.IGNORECASE)
        if not match:
            raise ValueError("Patch 格式应为 P12-3 或 12-3")
        bank = int(match.group(1))
        slot = int(match.group(2))
        return cls.from_bank_slot(bank, slot)


def list_output_ports() -> list[str]:
    try:
        return list(mido.get_output_names())
    except Exception as exc:
        raise RuntimeError(f"读取 MIDI 端口失败：{exc}") from exc


def resolve_port_name(requested: str) -> str:
    ports = list_output_ports()
    if not ports:
        raise RuntimeError("没有检测到 MIDI 输出端口。")
    if requested in ports:
        return requested
    matches = [p for p in ports if requested.lower() in p.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(f"端口 '{requested}' 匹配到多个结果，请手动精确选择。")
    raise RuntimeError(f"找不到 MIDI 端口：{requested}")


def send_messages(port_name: str, channel: int, messages: list, delay_ms: int = 30) -> None:
    if not (1 <= channel <= 16):
        raise ValueError("MIDI 通道必须在 1..16")
    midi_channel = channel - 1
    with mido.open_output(port_name) as outport:
        for i, msg in enumerate(messages):
            msg.channel = midi_channel
            outport.send(msg)
            if i != len(messages) - 1 and delay_ms > 0:
                time.sleep(delay_ms / 1000.0)


def build_patch_messages(address: PatchAddress) -> list:
    return [
        mido.Message("control_change", control=0, value=address.cc0_value),
        mido.Message("program_change", program=address.program_change_value),
    ]


class DarkButton(tk.Button):
    def __init__(self, master, bg_color=BUTTON, active_color=BUTTON_ACTIVE, **kwargs):
        options = {
            "bg": bg_color,
            "fg": TEXT,
            "activebackground": active_color,
            "activeforeground": TEXT,
            "relief": "flat",
            "bd": 0,
            "highlightthickness": 0,
            "cursor": "hand2",
            "font": (FONT_FAMILY, 11),
        }
        options.update(kwargs)
        super().__init__(master, **options)
        self.base_bg = options.get("bg", bg_color)
        self.hover_bg = options.get("activebackground", active_color)
        self.bind("<Enter>", lambda _e: self.configure(bg=self.hover_bg))
        self.bind("<Leave>", lambda _e: self.configure(bg=self.base_bg))


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry("1000x620")
        self.root.minsize(900, 560)
        self.root.configure(bg=BG)

        self.config = self.load_config()
        self.current_patch: Optional[PatchAddress] = None
        self.icon_image = None

        self.port_var = tk.StringVar()
        self.channel_var = tk.IntVar(value=int(self.config.get("channel", 1)))
        self.bank_var = tk.IntVar(value=int(self.config.get("bank", 0)))
        self.patch_entry_var = tk.StringVar(value=self.config.get("last_patch", "P00-1"))
        self.current_patch_var = tk.StringVar(value="当前目标：—")
        self.detail_var = tk.StringVar(value="CC0：—    PC：—")
        self.status_var = tk.StringVar(value="就绪")
        self.tuner_on = bool(self.config.get("tuner_on", False))
        self.tuner_btn_var = tk.StringVar(value="Tuner 关闭" if self.tuner_on else "Tuner")

        self.setup_styles()
        self.apply_icon()
        self.build_ui()
        self.refresh_ports(initial=True)
        self.bind_shortcuts()
        self.update_slot_labels()
        self.update_header_display()

    def setup_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(
            "Dark.TCombobox",
            fieldbackground=PANEL_3,
            background=PANEL_3,
            foreground=TEXT,
            arrowcolor=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            insertcolor=TEXT,
            padding=6,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", PANEL_3)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", PANEL_3)],
            selectforeground=[("readonly", TEXT)],
        )

    def apply_icon(self) -> None:
        """
        窗口图标与 exe 图标是两回事。
        这里设置的是“程序打开后的窗口图标”。

        优先级：
        1) icon.ico（最稳，Windows 推荐）
        2) icon.png
        """
        ico_path = find_resource("icon.ico")
        png_path = find_resource("icon.png")

        try:
            if ico_path is not None:
                self.root.iconbitmap(default=str(ico_path))
                return
        except Exception:
            pass

        try:
            if png_path is not None:
                self.icon_image = tk.PhotoImage(file=str(png_path))
                self.root.iconphoto(True, self.icon_image)
        except Exception:
            pass

    def build_ui(self) -> None:
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        sidebar = tk.Frame(self.root, bg=SIDEBAR, width=210)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)

        main = tk.Frame(self.root, bg=BG)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        self.build_sidebar(sidebar)
        self.build_header(main)
        self.build_content(main)
        self.build_footer(main)

    def build_sidebar(self, parent: tk.Frame) -> None:
        top = tk.Frame(parent, bg=SIDEBAR)
        top.pack(fill="x", padx=16, pady=(18, 10))

        logo = tk.Canvas(top, width=18, height=18, bg=SIDEBAR, highlightthickness=0)
        logo.create_oval(1, 1, 17, 17, fill=ACCENT, outline="")
        logo.create_oval(4, 4, 14, 14, fill="#d9455f", outline="")
        logo.create_oval(6, 6, 12, 12, fill="#1b1d24", outline="")
        logo.grid(row=0, column=0, padx=(0, 10))

        tk.Label(
            top,
            text="Ampero 控制器",
            bg=SIDEBAR,
            fg=TEXT,
            font=(FONT_FAMILY, 16, "bold"),
        ).grid(row=0, column=1, sticky="w")

        tk.Label(
            parent,
            text="USB / MIDI",
            bg=SIDEBAR,
            fg=TEXT_SOFT,
            font=(FONT_FAMILY, 10),
        ).pack(anchor="w", padx=18, pady=(0, 12))

        card = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", padx=14, pady=(0, 16))

        tk.Label(card, text="输出端口", bg=PANEL, fg=TEXT_DIM, font=(FONT_FAMILY, 10)).pack(anchor="w", padx=12, pady=(12, 6))
        self.port_combo = ttk.Combobox(card, textvariable=self.port_var, state="readonly", style="Dark.TCombobox")
        self.port_combo.pack(fill="x", padx=12)

        btn_row = tk.Frame(card, bg=PANEL)
        btn_row.pack(fill="x", padx=12, pady=10)
        DarkButton(btn_row, text="刷新端口", command=self.refresh_ports, font=(FONT_FAMILY, 10), height=1).pack(side="left")

        tk.Label(card, text="MIDI 通道", bg=PANEL, fg=TEXT_DIM, font=(FONT_FAMILY, 10)).pack(anchor="w", padx=12, pady=(4, 6))
        self.channel_spin = tk.Spinbox(
            card,
            from_=1,
            to=16,
            textvariable=self.channel_var,
            bg=PANEL_3,
            fg=TEXT,
            buttonbackground=BUTTON,
            relief="flat",
            bd=0,
            insertbackground=TEXT,
            font=(FONT_FAMILY, 11),
            justify="center",
            width=8,
            command=self.save_config,
        )
        self.channel_spin.pack(anchor="w", padx=12, pady=(0, 12))

        sep = tk.Frame(parent, bg=BORDER, height=1)
        sep.pack(fill="x", padx=14, pady=10)

        help_card = tk.Frame(parent, bg=PANEL)
        help_card.pack(fill="x", padx=14, pady=(0, 16))
        tk.Label(help_card, text="快捷键", bg=PANEL, fg=TEXT, font=(FONT_FAMILY, 12, "bold")).pack(anchor="w", padx=12, pady=(12, 6))
        shortcuts = [
            "← / →  上一音色 / 下一音色",
            "↑ / ↓  上一组 / 下一组",
            "1 / 2 / 3  选当前组音色",
            "Enter  跳转到输入的 Patch",
            "T  打开 / 关闭 Tuner",
        ]
        for line in shortcuts:
            tk.Label(help_card, text=line, bg=PANEL, fg=TEXT_DIM, font=(FONT_FAMILY, 10), anchor="w").pack(fill="x", padx=12, pady=2)

        bottom = tk.Frame(parent, bg=SIDEBAR)
        bottom.pack(side="bottom", fill="x", padx=16, pady=18)
        tk.Label(bottom, text="窗口图标建议使用 icon.ico", bg=SIDEBAR, fg=TEXT_SOFT, font=(FONT_FAMILY, 9)).pack(anchor="w")
        tk.Label(bottom, text="可放在 exe 同目录，或用 --add-data 打包进去", bg=SIDEBAR, fg=TEXT_SOFT, font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(4, 0))

    def build_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 12))
        header.grid_columnconfigure(0, weight=1)

        title = tk.Frame(header, bg=BG)
        title.grid(row=0, column=0, sticky="w")
        tk.Label(title, text="当前控制", bg=BG, fg=TEXT_DIM, font=(FONT_FAMILY, 11)).pack(anchor="w")
        tk.Label(title, textvariable=self.current_patch_var, bg=BG, fg=TEXT, font=(FONT_FAMILY, 22, "bold")).pack(anchor="w", pady=(2, 2))
        tk.Label(title, textvariable=self.detail_var, bg=BG, fg=TEXT_SOFT, font=(FONT_FAMILY, 10)).pack(anchor="w")

        status_wrap = tk.Frame(header, bg=BG)
        status_wrap.grid(row=0, column=1, sticky="e")
        status_card = tk.Frame(status_wrap, bg=PANEL, padx=14, pady=10, highlightbackground=BORDER, highlightthickness=1)
        status_card.pack()
        tk.Label(status_card, text="设备状态", bg=PANEL, fg=TEXT_DIM, font=(FONT_FAMILY, 10)).pack(anchor="w")
        self.status_indicator = tk.Canvas(status_card, width=160, height=24, bg=PANEL, highlightthickness=0)
        self.status_indicator.pack(anchor="w", pady=(6, 2))
        self.status_indicator.create_oval(2, 4, 18, 20, fill=ACCENT, outline="")
        self.status_indicator.create_text(88, 12, text="已连接 / 可发送", fill=TEXT, font=(FONT_FAMILY, 10))

    def build_content(self, parent: tk.Frame) -> None:
        content = tk.Frame(parent, bg=BG)
        content.grid(row=1, column=0, sticky="nsew", padx=18)
        content.grid_columnconfigure(0, weight=3)
        content.grid_columnconfigure(1, weight=2)
        content.grid_rowconfigure(0, weight=1)
        content.grid_rowconfigure(1, weight=1)

        left_top = tk.Frame(content, bg=BG)
        left_top.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=(0, 12))
        left_top.grid_columnconfigure(0, weight=1)
        left_top.grid_rowconfigure(1, weight=1)

        bank_card = tk.Frame(left_top, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        bank_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.build_bank_card(bank_card)

        slot_card = tk.Frame(left_top, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        slot_card.grid(row=1, column=0, sticky="nsew")
        self.build_slot_card(slot_card)

        right = tk.Frame(content, bg=BG)
        right.grid(row=0, column=1, rowspan=2, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        direct_card = tk.Frame(right, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        direct_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.build_direct_card(direct_card)

        nav_card = tk.Frame(right, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        nav_card.grid(row=1, column=0, sticky="nsew")
        self.build_nav_card(nav_card)

    def build_bank_card(self, parent: tk.Frame) -> None:
        head = tk.Frame(parent, bg=PANEL)
        head.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(head, text="音色组控制", bg=PANEL, fg=TEXT, font=(FONT_FAMILY, 14, "bold")).pack(side="left")
        tk.Label(head, text="Bank 0-99", bg=PANEL, fg=TEXT_SOFT, font=(FONT_FAMILY, 10)).pack(side="right")

        center = tk.Frame(parent, bg=PANEL)
        center.pack(fill="x", padx=16, pady=(0, 14))

        num_wrap = tk.Frame(center, bg=PANEL_3, padx=18, pady=12)
        num_wrap.pack(side="left", fill="x", expand=True)
        tk.Label(num_wrap, text="当前组", bg=PANEL_3, fg=TEXT_DIM, font=(FONT_FAMILY, 10)).pack(anchor="w")
        self.bank_big_label = tk.Label(num_wrap, text="00", bg=PANEL_3, fg=TEXT, font=(FONT_FAMILY, 28, "bold"))
        self.bank_big_label.pack(anchor="w", pady=(2, 0))

        right = tk.Frame(center, bg=PANEL)
        right.pack(side="left", padx=(12, 0))

        btns = tk.Frame(right, bg=PANEL)
        btns.pack(fill="x")
        DarkButton(btns, text="上一组", command=lambda: self.send_bank_step(prev=True), width=10, height=2).grid(row=0, column=0, padx=(0, 8))
        DarkButton(btns, text="下一组", command=lambda: self.send_bank_step(prev=False), width=10, height=2).grid(row=0, column=1)

        input_row = tk.Frame(right, bg=PANEL)
        input_row.pack(fill="x", pady=(10, 0))
        self.bank_spin = tk.Spinbox(
            input_row,
            from_=0,
            to=99,
            textvariable=self.bank_var,
            command=self.on_bank_changed,
            bg=PANEL_3,
            fg=TEXT,
            buttonbackground=BUTTON,
            relief="flat",
            bd=0,
            insertbackground=TEXT,
            font=(FONT_FAMILY, 12),
            justify="center",
            width=6,
        )
        self.bank_spin.grid(row=0, column=0)
        DarkButton(input_row, text="更新显示", command=self.on_bank_changed, width=10, height=1).grid(row=0, column=1, padx=(8, 0))

    def build_slot_card(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="当前组内音色", bg=PANEL, fg=TEXT, font=(FONT_FAMILY, 14, "bold")).pack(anchor="w", padx=16, pady=(14, 12))

        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        for col in range(3):
            row.grid_columnconfigure(col, weight=1)

        self.slot_btns: list[tk.Button] = []
        self.slot_subtitles: list[tk.Label] = []
        for idx, slot in enumerate((1, 2, 3)):
            card = tk.Frame(row, bg=PANEL_2, highlightbackground=BORDER, highlightthickness=1)
            card.grid(row=0, column=idx, sticky="nsew", padx=6)

            top_bar = tk.Frame(card, bg=PATCH_COLORS[idx], height=4)
            top_bar.pack(fill="x")

            tk.Label(card, text=f"音色 {slot}", bg=PANEL_2, fg=TEXT_DIM, font=(FONT_FAMILY, 11)).pack(anchor="center", pady=(14, 4))
            btn = DarkButton(
                card,
                text=str(slot),
                command=lambda s=slot: self.send_patch(slot=s),
                font=(FONT_FAMILY, 26, "bold"),
                width=6,
                height=2,
                bg_color=BUTTON,
                active_color=BUTTON_HOVER,
            )
            btn.pack(pady=(0, 8))
            sub = tk.Label(card, text=f"P00-{slot}", bg=PANEL_2, fg=TEXT_SOFT, font=(FONT_FAMILY, 11))
            sub.pack(pady=(0, 16))

            self.slot_btns.append(btn)
            self.slot_subtitles.append(sub)

    def build_direct_card(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="直接跳转", bg=PANEL, fg=TEXT, font=(FONT_FAMILY, 14, "bold")).pack(anchor="w", padx=16, pady=(14, 10))

        inner = tk.Frame(parent, bg=PANEL)
        inner.pack(fill="x", padx=16, pady=(0, 14))
        inner.grid_columnconfigure(0, weight=1)

        self.patch_entry_widget = tk.Entry(
            inner,
            textvariable=self.patch_entry_var,
            bg=PANEL_3,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            font=(FONT_FAMILY, 14),
        )
        self.patch_entry_widget.grid(row=0, column=0, sticky="ew", ipady=10)
        self.patch_entry_widget.bind("<Return>", lambda _e: self._send_patch_from_entry_and_release_focus())
        self.patch_entry_widget.bind("<Escape>", lambda _e: self._release_text_focus())
        DarkButton(inner, text="跳转", command=self._send_patch_from_entry_and_release_focus, width=10, height=2).grid(row=0, column=1, padx=(10, 0))
        tk.Label(inner, text="示例：P12-3", bg=PANEL, fg=TEXT_SOFT, font=(FONT_FAMILY, 10)).grid(row=1, column=0, sticky="w", pady=(8, 0))

    def build_nav_card(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="导航与 Scene", bg=PANEL, fg=TEXT, font=(FONT_FAMILY, 14, "bold")).pack(anchor="w", padx=16, pady=(14, 12))

        nav_row = tk.Frame(parent, bg=PANEL)
        nav_row.pack(fill="x", padx=16)
        DarkButton(nav_row, text="上一音色", command=lambda: self.send_cc_patch_step(prev=True), width=12, height=2).grid(row=0, column=0, padx=(0, 8), pady=(0, 10))
        DarkButton(nav_row, text="下一音色", command=lambda: self.send_cc_patch_step(prev=False), width=12, height=2).grid(row=0, column=1, padx=(0, 8), pady=(0, 10))
        DarkButton(nav_row, text="上一组", command=lambda: self.send_bank_step(prev=True), width=10, height=2).grid(row=0, column=2, padx=(0, 8), pady=(0, 10))
        DarkButton(nav_row, text="下一组", command=lambda: self.send_bank_step(prev=False), width=10, height=2).grid(row=0, column=3, pady=(0, 10))

        tk.Label(parent, text="Scene 切换", bg=PANEL, fg=TEXT_DIM, font=(FONT_FAMILY, 11)).pack(anchor="w", padx=16, pady=(8, 6))
        scenes = tk.Frame(parent, bg=PANEL)
        scenes.pack(fill="x", padx=16, pady=(0, 12))
        for i in range(5):
            scenes.grid_columnconfigure(i, weight=1)
        for i, scene in enumerate((1, 2, 3, 4, 5)):
            btn = DarkButton(
                scenes,
                text=f"Scene {scene}",
                command=lambda s=scene: self.send_scene(s),
                bg_color=SCENE_COLORS[i],
                active_color=SCENE_COLORS[i],
                width=10,
                height=2,
                font=(FONT_FAMILY, 10, "bold"),
            )
            btn.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0), pady=(0, 12))

        tk.Label(parent, text="工具", bg=PANEL, fg=TEXT_DIM, font=(FONT_FAMILY, 11)).pack(anchor="w", padx=16, pady=(4, 6))
        tool_row = tk.Frame(parent, bg=PANEL)
        tool_row.pack(fill="x", padx=16, pady=(0, 12))
        DarkButton(
            tool_row,
            textvariable=self.tuner_btn_var,
            command=self.toggle_tuner,
            bg_color=WARNING,
            active_color=WARNING,
            width=14,
            height=2,
            font=(FONT_FAMILY, 11, "bold"),
        ).grid(row=0, column=0, padx=(0, 8))
        tk.Label(
            tool_row,
            text="第一次按下打开调音器，再按一次关闭。",
            bg=PANEL,
            fg=TEXT_SOFT,
            font=(FONT_FAMILY, 10),
        ).grid(row=0, column=1, sticky="w")

        note = tk.Frame(parent, bg=PANEL_3)
        note.pack(fill="x", padx=16, pady=(4, 16))
        tk.Label(note, text="提示：组翻页会尽量保持当前音色编号不变。", bg=PANEL_3, fg=TEXT_SOFT, font=(FONT_FAMILY, 10)).pack(anchor="w", padx=12, pady=10)

    def build_footer(self, parent: tk.Frame) -> None:
        footer = tk.Frame(parent, bg=PANEL_3, height=44, highlightbackground=BORDER, highlightthickness=1)
        footer.grid(row=2, column=0, sticky="ew", padx=18, pady=(12, 18))
        footer.grid_propagate(False)
        self.status_label = tk.Label(footer, textvariable=self.status_var, bg=PANEL_3, fg=TEXT_DIM, font=(FONT_FAMILY, 10))
        self.status_label.pack(side="left", padx=14)

    def _focus_is_text_input(self) -> bool:
        widget = self.root.focus_get()
        if widget is None:
            return False
        cls = widget.winfo_class()
        return cls in {"Entry", "TEntry", "Spinbox", "TCombobox", "Combobox"}

    def _release_text_focus(self) -> None:
        self.root.focus_set()

    def _send_patch_from_entry_and_release_focus(self) -> None:
        self.send_patch_from_entry()
        self._release_text_focus()

    def bind_shortcuts(self) -> None:
        self.root.bind("<Control-l>", lambda _e: self.patch_entry_widget.focus_set())
        self.root.bind("<Escape>", lambda _e: self._release_text_focus())
        self.root.bind("<Left>", lambda _e: None if self._focus_is_text_input() else self.send_cc_patch_step(prev=True))
        self.root.bind("<Right>", lambda _e: None if self._focus_is_text_input() else self.send_cc_patch_step(prev=False))
        self.root.bind("<Up>", lambda _e: None if self._focus_is_text_input() else self.send_bank_step(prev=False))
        self.root.bind("<Down>", lambda _e: None if self._focus_is_text_input() else self.send_bank_step(prev=True))
        self.root.bind("1", lambda _e: None if self._focus_is_text_input() else self.send_patch(slot=1))
        self.root.bind("2", lambda _e: None if self._focus_is_text_input() else self.send_patch(slot=2))
        self.root.bind("3", lambda _e: None if self._focus_is_text_input() else self.send_patch(slot=3))
        self.root.bind("t", lambda _e: None if self._focus_is_text_input() else self.toggle_tuner())
        self.root.bind("T", lambda _e: None if self._focus_is_text_input() else self.toggle_tuner())

    def refresh_ports(self, initial: bool = False) -> None:
        try:
            ports = list_output_ports()
        except Exception as exc:
            self.port_combo["values"] = []
            self.port_var.set("")
            self.set_status(str(exc), error=True)
            self.update_connection_indicator(ok=False)
            return

        self.port_combo["values"] = ports
        if not ports:
            self.port_var.set("")
            self.set_status("未检测到 MIDI 输出端口", error=True)
            self.update_connection_indicator(ok=False)
            return

        remembered = str(self.config.get("port", ""))
        selected = ""
        if remembered and remembered in ports:
            selected = remembered
        else:
            for p in ports:
                if "ampero" in p.lower():
                    selected = p
                    break
            if not selected:
                selected = ports[0]

        self.port_var.set(selected)
        self.update_connection_indicator(ok=True)
        if not initial:
            self.set_status(f"已刷新端口，共检测到 {len(ports)} 个输出端口")
        self.save_config()

    def update_connection_indicator(self, ok: bool) -> None:
        self.status_indicator.delete("all")
        color = ACCENT if ok else ERROR
        text = "已连接 / 可发送" if ok else "未连接设备"
        self.status_indicator.create_oval(2, 4, 18, 20, fill=color, outline="")
        self.status_indicator.create_text(88, 12, text=text, fill=TEXT, font=(FONT_FAMILY, 10))

    def require_port(self) -> str:
        port = self.port_var.get().strip()
        if not port:
            raise RuntimeError("请先选择 MIDI 输出端口。")
        return resolve_port_name(port)

    def get_channel(self) -> int:
        channel = int(self.channel_var.get())
        if not (1 <= channel <= 16):
            raise ValueError("MIDI 通道必须在 1..16")
        return channel

    def on_bank_changed(self) -> None:
        self.normalize_bank_var()
        self.update_slot_labels()
        self.update_header_display()
        self.save_config()

    def normalize_bank_var(self) -> int:
        try:
            bank = int(self.bank_var.get())
        except Exception:
            bank = 0
        bank = max(0, min(99, bank))
        self.bank_var.set(bank)
        return bank

    def update_slot_labels(self) -> None:
        bank = self.normalize_bank_var()
        self.bank_big_label.configure(text=f"{bank:02d}")
        for i, sub in enumerate(self.slot_subtitles, start=1):
            sub.configure(text=f"P{bank:02d}-{i}")

    def update_header_display(self) -> None:
        if self.current_patch is None:
            bank = self.normalize_bank_var()
            self.current_patch_var.set(f"当前目标：P{bank:02d}-1")
            address = PatchAddress.from_bank_slot(bank, 1)
            self.detail_var.set(f"CC0：{address.cc0_value}    PC：{address.program_change_value}")
        else:
            self.current_patch_var.set(f"当前目标：{self.current_patch.patch_label}")
            self.detail_var.set(
                f"CC0：{self.current_patch.cc0_value}    PC：{self.current_patch.program_change_value}"
            )

    def set_status(self, text: str, error: bool = False) -> None:
        self.status_var.set(text)
        self.status_label.configure(fg=ERROR if error else TEXT_DIM)

    def send_patch(self, slot: int) -> None:
        try:
            bank = self.normalize_bank_var()
            address = PatchAddress.from_bank_slot(bank, slot)
            port = self.require_port()
            channel = self.get_channel()
            send_messages(port, channel, build_patch_messages(address), delay_ms=30)
            self.current_patch = address
            self.patch_entry_var.set(address.patch_label)
            self.update_header_display()
            self.set_status(f"已切换到 {address.patch_label}    端口：{port}")
            self.save_config(last_patch=address.patch_label)
        except Exception as exc:
            messagebox.showerror("MIDI 错误", str(exc))
            self.set_status(f"错误：{exc}", error=True)

    def send_patch_from_entry(self) -> None:
        try:
            text = self.patch_entry_var.get().strip()
            address = PatchAddress.from_patch_string(text)
            self.bank_var.set(address.bank)
            self.update_slot_labels()
            self.send_patch(address.slot)
        except Exception as exc:
            messagebox.showerror("Patch 错误", str(exc))
            self.set_status(f"错误：{exc}", error=True)

    def send_scene(self, scene: int) -> None:
        try:
            port = self.require_port()
            channel = self.get_channel()
            send_messages(
                port,
                channel,
                [mido.Message("control_change", control=25, value=scene)],
            )
            self.set_status(f"已切换到 Scene {scene}")
            self.save_config()
        except Exception as exc:
            messagebox.showerror("Scene 错误", str(exc))
            self.set_status(f"错误：{exc}", error=True)

    def toggle_tuner(self) -> None:
        try:
            port = self.require_port()
            channel = self.get_channel()
            turn_on = not self.tuner_on
            value = 127 if turn_on else 0
            send_messages(
                port,
                channel,
                [mido.Message("control_change", control=60, value=value)],
            )
            self.tuner_on = turn_on
            self.tuner_btn_var.set("Tuner 关闭" if self.tuner_on else "Tuner")
            self.set_status("已打开调音器" if self.tuner_on else "已关闭调音器")
            self.save_config(tuner_on=self.tuner_on)
        except Exception as exc:
            messagebox.showerror("Tuner 错误", str(exc))
            self.set_status(f"错误：{exc}", error=True)

    def send_cc_patch_step(self, prev: bool) -> None:
        try:
            port = self.require_port()
            channel = self.get_channel()
            cc = 26 if prev else 27
            send_messages(
                port,
                channel,
                [mido.Message("control_change", control=cc, value=127)],
            )
            direction = "上一音色" if prev else "下一音色"
            self.set_status(f"已发送：{direction}")
            self.save_config()
        except Exception as exc:
            messagebox.showerror("音色导航错误", str(exc))
            self.set_status(f"错误：{exc}", error=True)

    def send_bank_step(self, prev: bool) -> None:
        try:
            current_bank = self.normalize_bank_var()
            new_bank = max(0, min(99, current_bank - 1 if prev else current_bank + 1))
            if new_bank == current_bank:
                self.set_status("已经到边界组了")
                return

            slot = self.current_patch.slot if self.current_patch is not None else 1
            self.bank_var.set(new_bank)
            self.update_slot_labels()
            self.send_patch(slot=slot)
            direction = "上一组" if prev else "下一组"
            self.set_status(f"已切换：{direction} → P{new_bank:02d}-{slot}")
        except Exception as exc:
            messagebox.showerror("组切换错误", str(exc))
            self.set_status(f"错误：{exc}", error=True)

    def load_config(self) -> dict:
        try:
            if CONFIG_PATH.exists():
                return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def save_config(self, **updates) -> None:
        try:
            channel = int(self.channel_var.get())
        except Exception:
            channel = 1
        try:
            bank = int(self.bank_var.get())
        except Exception:
            bank = 0
        self.config.update(
            {
                "port": self.port_var.get().strip(),
                "channel": channel,
                "bank": bank,
                **updates,
            }
        )
        try:
            CONFIG_PATH.write_text(
                json.dumps(self.config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
