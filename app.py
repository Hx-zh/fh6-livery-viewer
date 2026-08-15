# -*- coding: utf-8 -*-
"""
app.py — FH6 涂装查看器 (GUI)

仅查看《极限竞速:地平线 6》存档中的涂装, 主视图为缩略图平铺, 只读。
车型名表 cars.json 随程序分发(打包时内嵌进 exe, 不再释放到用户目录)。

用法: python app.py
打包: python -m PyInstaller FH6LiveryViewer.spec(勿用损坏的 pyinstaller.exe shim)
"""

from __future__ import annotations

import os
import re
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import fh6save
from fh6save import CarTable, SaveItem, SaveOps

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

if getattr(sys, "frozen", False):
    # PyInstaller 打包后: cars.json 内嵌在 exe 的临时解包目录(_MEIPASS), 只读使用
    APP_DIR = Path(sys.executable).resolve().parent
    CARS_JSON = Path(getattr(sys, "_MEIPASS", APP_DIR)) / "cars.json"
else:
    APP_DIR = Path(__file__).resolve().parent
    CARS_JSON = APP_DIR / "cars.json"
BACKUP_DIR = APP_DIR / "backups"

APP_VERSION = "1.1.0"
PROJECT_URL = "https://github.com/Hx-zh/fh6-livery-viewer"
RELEASES_URL = PROJECT_URL + "/releases"

CARD_W, CARD_H = 196, 200      # 卡片尺寸
THUMB_W, THUMB_H = 184, 120    # 卡片缩略图区域

SORT_OPTIONS = ["下载日期(新→旧)", "下载日期(旧→新)", "名称", "车型", "作者", "游戏内顺序"]


def fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def ellipsize(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


class Card(tk.Frame):
    """平铺视图里的一张条目卡片。"""

    def __init__(self, master, app: "App", item: SaveItem):
        # 外层 Frame 的背景色即描边色; 内容装内层 Frame, 避免 highlight 被裁
        super().__init__(master, width=CARD_W, height=CARD_H, bg="#cccccc")
        self.app = app
        self.item = item
        self.pack_propagate(False)

        self.inner = tk.Frame(self, bg="#ffffff")
        self.inner.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        self.img_label = tk.Label(self.inner, text="加载中…", bg="#f0f0f0",
                                  fg="#888888", anchor=tk.CENTER)
        self.img_label.place(x=3, y=3, width=THUMB_W, height=THUMB_H)

        self.name_label = tk.Label(self.inner, text=ellipsize(item.name or "(未解析)", 24),
                                   anchor=tk.W, bg="#ffffff",
                                   font=("Microsoft YaHei UI", 9, "bold"))
        self.name_label.place(x=5, y=THUMB_H + 4, width=THUMB_W)

        # 车型名允许换行(最多两行), 尽量完整显示
        car = app.car_display(item)
        self.car_label = tk.Label(self.inner, text=car, anchor=tk.NW, justify=tk.LEFT,
                                  wraplength=THUMB_W - 4, bg="#ffffff", fg="#333333",
                                  font=("Microsoft YaHei UI", 8))
        self.car_label.place(x=5, y=THUMB_H + 24, width=THUMB_W, height=32)

        # 第三行: 车型已识别(或无车型 ID) → 显示作者; 未识别 → 显示 ID 和日期(便于排查)
        known = bool(app.car_table.name("fh6", item.car_id))
        date = item.ts.strftime("%Y-%m-%d") if item.ts else ""
        if known or not item.car_id:
            third = item.creator or "?"
        else:
            third = f"ID {item.car_id}  {date}"
        self.sub_label = tk.Label(self.inner, text=ellipsize(third, 30),
                                  anchor=tk.W, bg="#ffffff", fg="#888888",
                                  font=("Microsoft YaHei UI", 8))
        self.sub_label.place(x=5, y=THUMB_H + 56, width=THUMB_W)

        for w in (self, self.inner, self.img_label, self.name_label,
                  self.car_label, self.sub_label):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Double-1>", self._on_dbl)
            w.bind("<Button-3>", self._on_rclick)   # 右键复制游戏内位置
        self.set_selected(False)   # 初始化边框

    def _on_rclick(self, e):
        """右键菜单: 复制位置/名称/车型/作者。"""
        self.app.select(self.item.base)
        it = self.item
        pos = self.app._pos_map.get(it.base, "")
        car = self.app.car_table.name("fh6", it.car_id)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=f"复制游戏内位置 ({pos})" if pos else "复制游戏内位置",
                         state=tk.NORMAL if pos else tk.DISABLED,
                         command=lambda: self.app.copy_text(pos))
        menu.add_command(label="复制名称",
                         state=tk.NORMAL if it.name else tk.DISABLED,
                         command=lambda: self.app.copy_text(it.name))
        menu.add_command(label="复制车型",
                         state=tk.NORMAL if car else tk.DISABLED,
                         command=lambda: self.app.copy_text(car))
        menu.add_command(label="复制作者",
                         state=tk.NORMAL if it.creator else tk.DISABLED,
                         command=lambda: self.app.copy_text(it.creator))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _on_click(self, _e):
        self.app.select(self.item.base)

    def _on_dbl(self, _e):
        self.app.select(self.item.base)
        self.app.show_big_preview()

    def set_image(self, img):
        if img:
            self.img_label.configure(image=img, text="")
        else:
            self.img_label.configure(image="", text="(无预览图)")

    def set_selected(self, on: bool):
        fill = "#0078D7" if on else "#ffffff"
        name_fg = "#ffffff" if on else "#000000"
        car_fg = "#eaf4ff" if on else "#333333"
        sub_fg = "#cce6ff" if on else "#888888"
        # 外层背景即描边: 选中深蓝, 未选中浅灰
        border = "#005a9e" if on else "#cccccc"
        self.configure(bg=border)
        self.inner.configure(bg=fill)
        self.name_label.configure(bg=fill, fg=name_fg)
        self.car_label.configure(bg=fill, fg=car_fg)
        self.sub_label.configure(bg=fill, fg=sub_fg)


class ZoomPreview(tk.Toplevel):
    """可缩放的大图预览: 滚轮/按钮缩放, 左键拖动平移。"""

    MIN_SCALE, MAX_SCALE = 0.05, 12.0

    def __init__(self, app: "App", item: SaveItem):
        super().__init__(app)
        self.title(f"{item.name or item.base} — 预览(滚轮缩放, 拖动平移)")
        self.geometry("1000x700")
        self.fit_mode = True
        self.scale = 1.0
        self._tk_img = None
        self._src: "Image.Image | None" = None
        if HAS_PIL and item.thumb_big:
            try:
                self._src = Image.open(item.thumb_big)
            except Exception:
                self._src = None

        bar = ttk.Frame(self, padding=4)
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="放大 (+)", command=lambda: self._zoom(1.25)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="缩小 (−)", command=lambda: self._zoom(1 / 1.25)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="原始尺寸", command=self._actual).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="适应窗口", command=self._fit).pack(side=tk.LEFT, padx=2)
        self.scale_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.scale_var).pack(side=tk.LEFT, padx=10)

        self.canvas = tk.Canvas(self, bg="#202020", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>",
                         lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind("<MouseWheel>",
                         lambda e: self._zoom(1.15 if e.delta > 0 else 1 / 1.15))
        self.canvas.bind("<Configure>", self._on_configure)
        self.after(60, self._render)

    def _on_configure(self, _e):
        if self.fit_mode:
            self._render()

    def _fit_scale(self) -> float:
        if not self._src:
            return 1.0
        w = max(self.canvas.winfo_width() - 20, 50)
        h = max(self.canvas.winfo_height() - 20, 50)
        return min(w / self._src.width, h / self._src.height)

    def _zoom(self, factor: float):
        self.fit_mode = False
        self.scale = min(self.MAX_SCALE, max(self.MIN_SCALE, self.scale * factor))
        self._render()

    def _actual(self):
        self.fit_mode = False
        self.scale = 1.0
        self._render()

    def _fit(self):
        self.fit_mode = True
        self._render()

    def _render(self):
        if not self._src:
            ttk.Label(self.canvas, text="无法加载预览图(需要 Pillow)",
                      foreground="#ffffff").place(relx=0.5, rely=0.5, anchor=tk.CENTER)
            return
        if self.fit_mode:
            self.scale = self._fit_scale()
        w = max(1, int(self._src.width * self.scale))
        h = max(1, int(self._src.height * self.scale))
        disp = self._src.resize((w, h), Image.Resampling.LANCZOS)
        self._tk_img = ImageTk.PhotoImage(disp)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self._tk_img)
        self.canvas.configure(scrollregion=(0, 0, w, h))
        self.scale_var.set(f"{self.scale * 100:.0f}%  ({self._src.width}×{self._src.height})")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FH6 涂装查看器")
        # 默认按屏幕大小自适应(约 4/5 屏), 居中显示
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = int(sw * 0.8), int(sh * 0.84)
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        self.minsize(1000, 620)

        self.car_table = CarTable(CARS_JSON)
        self.ops = SaveOps(BACKUP_DIR)
        self.saves: list[dict] = []
        self.current: dict | None = None
        self.items: list[SaveItem] = []
        self.item_map: dict[str, SaveItem] = {}
        self.cards: dict[str, Card] = {}
        self._img_cache: dict[str, object] = {}   # base -> PhotoImage
        self._pos_map: dict[str, str] = {}        # base -> 游戏内位置 ("N行M列")
        self._dup_group: dict[str, int] = {}      # base -> 重复组号 (0 = 无重复)
        self._car_unique: dict[int, int] = {}     # 车型 ID -> 唯一涂装数
        self._dup_rules: dict[int, list[str]] = {}  # 重复组号 -> 命中规则标签
        self._dup_feats: dict | None = None       # 重复检测预计算特征(后台线程填充)
        self._thumb_pending: list[str] = []
        self._selected: str | None = None
        self._detail_img = None
        self._cols = 1
        self._relayout_job = None

        self._build_ui()
        self.rescan_saves()

    # ------------------------------------------------------------ UI 构建

    def _build_ui(self):
        bar = ttk.Frame(self, padding=6)
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="FH6 存档:").pack(side=tk.LEFT)
        self.save_var = tk.StringVar()
        self.save_combo = ttk.Combobox(bar, textvariable=self.save_var,
                                       state="readonly", width=60)
        self.save_combo.pack(side=tk.LEFT, padx=(2, 6))
        self.save_combo.bind("<<ComboboxSelected>>", lambda _e: self.load_current())
        for text, cmd in (("刷新", self.rescan_saves),
                          ("手动选择目录…", self.browse_folder),
                          ("备份整个存档", self.backup_all),
                          ("打开存档目录", self.open_folder)):
            ttk.Button(bar, text=text, command=cmd).pack(side=tk.LEFT, padx=2)

        flt = ttk.Frame(self, padding=(6, 0, 6, 6))
        flt.pack(fill=tk.X)
        ttk.Label(flt, text="搜索:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        ent = ttk.Entry(flt, textvariable=self.search_var, width=28)
        ent.pack(side=tk.LEFT, padx=(2, 10))
        ent.bind("<KeyRelease>", lambda _e: self.rebuild_grid())
        # 重复涂装场景筛选: "all" 不筛选, "dup" 不限场景, 其余为规则标签(同车复刻 等)
        self.dup_view = tk.StringVar(value="all")
        self._dup_view_last = "all"    # 供"再点一次取消"判断
        self.multi_only = tk.BooleanVar(value=False)
        self.single_only = tk.BooleanVar(value=False)
        ttk.Label(flt, text="车厂:").pack(side=tk.LEFT, padx=(14, 2))
        self.brand_var = tk.StringVar(value="全部车厂")
        self.brand_combo = ttk.Combobox(flt, textvariable=self.brand_var,
                                        state="readonly", width=16,
                                        values=["全部车厂"])
        self.brand_combo.pack(side=tk.LEFT)
        self.brand_combo.bind("<<ComboboxSelected>>", lambda _e: self.rebuild_grid())
        ttk.Label(flt, text="排序:").pack(side=tk.LEFT, padx=(14, 2))
        self.sort_var = tk.StringVar(value=SORT_OPTIONS[0])
        scb = ttk.Combobox(flt, textvariable=self.sort_var, state="readonly",
                           width=16, values=SORT_OPTIONS)
        scb.pack(side=tk.LEFT)
        scb.bind("<<ComboboxSelected>>", lambda _e: self.rebuild_grid())

        # 右侧: 重复涂装筛选(场景单选, 再点一次已选项取消; 经典 tk.Menubutton 凸起边框)
        self.filter_mb = tk.Menubutton(flt, text="重复涂装筛选", relief=tk.RAISED, bd=1,
                                       bg="#e1e1e1", activebackground="#ececec",
                                       padx=10, pady=2)
        fmenu = tk.Menu(self.filter_mb, tearoff=0)
        # 场景单选(互斥); "all" = 不筛选(不能用空串做 radio 值, 会选不中)
        for label, val in (("全部涂装(不筛选)", "all"),
                           ("仅重复涂装(不限场景)", "dup"),
                           ("同车复刻(经典涂装多人复刻)", "同车复刻"),
                           ("同车微调(同一作者的 v1/v2 版本)", "同车微调"),
                           ("跨车型移植(同一涂装的多车型版)", "跨车型移植"),
                           ("无图同名(无缩略图的同名涂装)", "无图同名")):
            fmenu.add_radiobutton(label=label, variable=self.dup_view, value=val,
                                  command=lambda v=val: self._toggle_dup_view(v))
        fmenu.add_separator()
        # 车型维度(可叠加, 二者互斥)
        fmenu.add_checkbutton(label="仅显示多涂装车型(≥2 种)", variable=self.multi_only,
                              command=lambda: self._select_dup_filter("multi"))
        fmenu.add_checkbutton(label="仅显示单涂装车型(仅 1 种)", variable=self.single_only,
                              command=lambda: self._select_dup_filter("single"))
        self.filter_mb.configure(menu=fmenu)
        self.filter_mb.pack(side=tk.RIGHT)

        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        # 右侧: 固定宽度详情面板(先 pack, 保证宽度不被网格挤压)
        right = tk.Frame(main, width=430)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        right.pack_propagate(False)
        self.thumb_label = ttk.Label(right, text="(无预览)", anchor=tk.CENTER,
                                     relief=tk.SUNKEN)
        self.thumb_label.pack(fill=tk.X, pady=(0, 6))
        self.thumb_label.bind("<Double-1>", lambda _e: self.show_big_preview())
        # 详情: 只读文本框, 支持鼠标选择 / 原生 Ctrl+C / 右键菜单
        self.info_text = tk.Text(right, height=12, wrap=tk.WORD, relief=tk.FLAT,
                                 bd=0, bg="#f0f0f0", font=("Microsoft YaHei UI", 9),
                                 cursor="arrow")
        self.info_text.pack(fill=tk.X, pady=(0, 6))
        self.info_text.bind("<Key>", self._info_key)
        self.info_text.bind("<<Cut>>", lambda _e: "break")
        self.info_text.bind("<<Paste>>", lambda _e: "break")
        self.info_text.bind("<Button-3>", self._info_menu)
        self._set_info("未选择条目")
        btns = tk.Frame(right)
        btns.pack(fill=tk.X)
        for text, cmd in (("查看大图", self.show_big_preview),
                          ("所在文件夹", self.open_item_folder)):
            ttk.Button(btns, text=text, command=cmd).pack(fill=tk.X, pady=1)

        # 右下: 版本/项目地址/声明
        footer = tk.Frame(right)
        footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 2))
        ttk.Separator(footer).pack(fill=tk.X, pady=(0, 6))
        tk.Label(footer, text=f"FH6 涂装查看器 v{APP_VERSION}",
                 font=("Microsoft YaHei UI", 9, "bold"), anchor=tk.W).pack(fill=tk.X)
        link = tk.Label(footer, text=f"更新链接: {RELEASES_URL}", fg="#0066cc",
                        cursor="hand2", anchor=tk.W,
                        font=("Microsoft YaHei UI", 8, "underline"))
        link.pack(fill=tk.X)
        link.bind("<Button-1>", lambda _e: webbrowser.open(RELEASES_URL))
        tk.Label(footer, anchor=tk.NW, justify=tk.LEFT, wraplength=400,
                 fg="#777777", font=("Microsoft YaHei UI", 8),
                 text="本工具与 Microsoft、Xbox、Playground Games、Turn 10 无关，Forza 相关商标归其各自所有者。\n"
                      "工具仅读取本地存档文件，不提供任何修改、解锁或联机功能。\n"
                      "使用本工具产生的任何后果由使用者自行承担。\n").pack(
                     fill=tk.X, pady=(4, 0))

        # 左侧: 可滚动的平铺画布, 占满剩余空间
        grid_wrap = ttk.Frame(main)
        grid_wrap.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(grid_wrap, highlightthickness=0, bg="#fafafa")
        vsb = ttk.Scrollbar(grid_wrap, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.grid_frame = tk.Frame(self.canvas, bg="#fafafa")
        self._canvas_win = self.canvas.create_window((0, 0), window=self.grid_frame,
                                                     anchor=tk.NW)
        self.grid_frame.bind("<Configure>", self._on_grid_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # 全局滚轮: 指针在网格区域内才滚动(不能靠 Enter/Leave, 卡片子窗体会打断)
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W, padding=(6, 2)).pack(fill=tk.X, side=tk.BOTTOM)
        self.bind("<Control-c>", self._on_ctrl_c)

    # ------------------------------------------------------------ 平铺布局

    def _on_wheel(self, e):
        """指针在左侧网格区域时滚动画布(含卡片子窗体上方)。"""
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None:
            if w in (self.canvas, self.grid_frame):
                self.canvas.yview_scroll(int(-e.delta / 120), "units")
                return
            w = getattr(w, "master", None)

    def _on_grid_configure(self, _e):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, e):
        self.canvas.itemconfigure(self._canvas_win, width=e.width)
        cols = max(1, e.width // (CARD_W + 8))
        if cols != self._cols:
            self._cols = cols
            if self._relayout_job:
                self.after_cancel(self._relayout_job)
            self._relayout_job = self.after(120, self._relayout)

    def _relayout(self):
        self._relayout_job = None
        visible = [c for c in self.cards.values() if c.winfo_exists()]
        for i, card in enumerate(visible):
            card.grid(row=i // self._cols, column=i % self._cols,
                      padx=4, pady=4, sticky=tk.NW)

    def rebuild_grid(self):
        for c in self.cards.values():
            c.destroy()
        self.cards = {}
        self._thumb_pending = []
        shown = self._sorted(self._filtered())
        for it in shown:
            card = Card(self.grid_frame, self, it)
            self.cards[it.base] = card
            if it.base not in self._img_cache:
                self._thumb_pending.append(it.base)
            # 已有缓存的直接贴图
            elif self._img_cache.get(it.base):
                card.set_image(self._img_cache[it.base])
        self._relayout()
        self.after(30, self._load_thumbs_batch)
        self._selected = None
        self._set_info("未选择条目")
        self.thumb_label.configure(image="", text="(无预览)")
        parts = []
        if self.dup_view.get() == "dup":
            parts.append("仅重复")
        elif self.dup_view.get() != "all":
            parts.append(self.dup_view.get())
        if self.multi_only.get():
            parts.append("多涂装车型")
        if self.single_only.get():
            parts.append("单涂装车型")
        self.filter_mb.configure(
            text=f"重复涂装筛选: {'+'.join(parts)}" if parts else "重复涂装筛选")
        known = self.car_table.known_count("fh6")
        dup_groups = len({g for g in self._dup_group.values() if g})
        dup_files = sum(1 for g in self._dup_group.values() if g)
        dup_txt = f"重复 {dup_groups} 组({dup_files} 个)  |  " if dup_groups else ""
        self.status_var.set(
            f"共 {len(self.items)} 个条目, 显示 {len(shown)} 个  |  {dup_txt}"
            f"已识别车型 {known} 个  |  {self.current['dir'] if self.current else ''}")

    def _load_thumbs_batch(self, n: int = 8):
        """分批在主线程解码缩略图, 保持界面响应。"""
        batch, self._thumb_pending = self._thumb_pending[:n], self._thumb_pending[n:]
        for base in batch:
            card = self.cards.get(base)
            if not card:
                continue
            img = self._load_thumb(card.item.thumb_big, THUMB_W, THUMB_H,
                                   badge=self._pos_map.get(base, ""))
            self._img_cache[base] = img
            card.set_image(img)
        if self._thumb_pending:
            self.after(10, self._load_thumbs_batch)

    # ------------------------------------------------------------ 存档加载

    def rescan_saves(self):
        self.saves = [s for s in fh6save.find_saves() if s["game"] == "fh6"]
        labels = []
        src_names = {"steam": "Steam", "pgs": "商店版/pgs"}
        for s in self.saves:
            src_key = str(s.get("source") or "?")
            src = src_names.get(src_key, src_key)
            labels.append(f"[{src}]  用户 {s['steam_user']}  |  {s['dir']}")
        self.save_combo.configure(values=labels)
        if labels:
            self.save_combo.current(0)
            self.load_current()
        else:
            self.current = None
            self.items = []
            self._dup_group, self._car_unique, self._pos_map = {}, {}, {}
            self._dup_rules = {}
            self._dup_feats = None
            self._img_cache.clear()
            self.rebuild_grid()
            self.status_var.set("未找到 FH6 存档, 请用「手动选择目录」指定存档文件夹")

    def load_current(self):
        i = self.save_combo.current()
        if i < 0 or i >= len(self.saves):
            return
        self.current = self.saves[i]
        self.scan_items()

    def scan_items(self):
        if not self.current:
            return
        self.items = fh6save.scan_folder("fh6", self.current["steam_user"],
                                         Path(self.current["dir"]))
        self.item_map = {it.base: it for it in self.items}
        self._pos_map = fh6save.game_position_map(self.items)
        self._dup_group, self._car_unique = {}, {}
        self._dup_rules = {}
        self._dup_feats = None
        self._img_cache.clear()
        # 车厂下拉: 只列出当前存档涂装实际涉及的车厂
        brands = sorted({b for it in self.items if it.itype == "Livery"
                         for b in [self._brand_of(it)] if b}, key=str.lower)
        self.brand_combo.configure(values=["全部车厂"] + brands)
        if self.brand_var.get() not in ("全部车厂", *brands):
            self.brand_var.set("全部车厂")
        # 后台线程解码缩略图计算感知哈希(慢), 完成后再算重复分组
        items = self.items

        def _work():
            feats = fh6save.extract_dup_features(items)
            self.after(0, lambda: self._dup_ready(items, feats))

        threading.Thread(target=_work, daemon=True).start()
        self.rebuild_grid()

    def _dup_ready(self, items: list[SaveItem], feats: dict):
        if items is not self.items:
            return                     # 等待期间已切换存档, 丢弃
        self._dup_feats = feats
        self._rerun_dup()

    def _rerun_dup(self):
        """重算重复分组并刷新界面。"""
        if self._dup_feats is None:
            return
        (self._dup_group, self._car_unique,
         self._dup_rules) = fh6save.detect_duplicates(self.items,
                                                      features=self._dup_feats)
        self.rebuild_grid()

    def browse_folder(self):
        d = filedialog.askdirectory(title="选择 FH6 存档目录(remote 或 ContainersRoot)")
        if not d:
            return
        folder = Path(d)

        def _has_items(p: Path) -> bool:
            try:
                return any(fh6save.ITEM_RE.match(f.name) for f in p.iterdir())
            except OSError:
                return False

        if not _has_items(folder):
            for sub in folder.iterdir():
                if sub.is_dir() and not sub.is_symlink() and _has_items(sub):
                    folder = sub
                    break
        self.current = {"game": "fh6", "steam_user": "手动", "dir": folder}
        self.scan_items()

    # ------------------------------------------------------------ 过滤

    def _toggle_dup_view(self, val: str):
        """场景单选: 再次点击已选中的场景 = 取消(回到不筛选)。"""
        if val != "all" and self._dup_view_last == val:
            self.dup_view.set("all")
        self._dup_view_last = self.dup_view.get()
        self.rebuild_grid()

    def _select_dup_filter(self, which: str):
        """车型维度的「多涂装」与「单涂装」互斥。"""
        if which == "multi" and self.multi_only.get():
            self.single_only.set(False)
        elif which == "single" and self.single_only.get():
            self.multi_only.set(False)
        self.rebuild_grid()

    def car_display(self, it: SaveItem) -> str:
        if it.car_id == 0:
            return "-"
        name = self.car_table.name("fh6", it.car_id)
        return name if name else f"ID {it.car_id}"

    def _brand_of(self, it: SaveItem) -> str:
        """条目的车厂名; 车型未标注时为空串。"""
        name = self.car_table.name("fh6", it.car_id)
        return fh6save.car_brand(name) if name else ""

    def _filtered(self) -> list[SaveItem]:
        q = self.search_var.get().strip().lower()
        brand = self.brand_var.get()
        out = []
        for it in self.items:
            if it.itype != "Livery":          # 仅限涂装
                continue
            view = self.dup_view.get()
            if view != "all":
                gid = self._dup_group.get(it.base, 0)
                if not gid:
                    continue
                if view != "dup" and view not in self._dup_rules.get(gid, []):
                    continue
            unique = self._car_unique.get(it.car_id, 0)
            if self.multi_only.get() and unique < 2:
                continue
            if self.single_only.get() and unique != 1:
                continue
            if brand != "全部车厂" and self._brand_of(it) != brand:
                continue
            if q:
                hay = f"{it.name} {it.creator} {it.car_id} {self.car_display(it)}".lower()
                if q not in hay:
                    continue
            out.append(it)
        return out

    def _sorted(self, items: list[SaveItem]) -> list[SaveItem]:
        from datetime import datetime as _dt, timezone as _tz
        tmin = _dt.min.replace(tzinfo=_tz.utc)
        tmax = _dt.max.replace(tzinfo=_tz.utc)
        mode = self.sort_var.get()
        if mode == "下载日期(新→旧)":
            return sorted(items, key=lambda i: i.ts or tmin, reverse=True)
        if mode == "下载日期(旧→新)":
            return sorted(items, key=lambda i: i.ts or tmax)
        if mode == "名称":
            return sorted(items, key=lambda i: (i.name or "").lower())
        if mode == "车型":
            def _car_key(i: SaveItem):
                n = self.car_table.name("fh6", i.car_id)
                if not n:
                    return (1, f"{i.car_id:06d}")        # 未识别的排最后, 按 ID 排
                n = re.sub(r"^\d{4}\s+", "", n)          # 剥掉年份前缀, 按品牌车型排
                return (0, n.lower())
            return sorted(items, key=_car_key)
        if mode == "作者":
            return sorted(items, key=lambda i: (i.creator or "").lower())
        if mode == "游戏内顺序":
            # 与游戏内「我的涂装」排列一致: 车型 ID 升序, 同车型按时间戳升序
            return sorted(items, key=lambda i: (i.car_id, i.base))
        return items

    # ------------------------------------------------------------ 选中与预览

    def select(self, base: str | None):
        self._selected = base
        for b, card in self.cards.items():
            card.set_selected(b == base)
        self.show_preview()

    def selected_item(self) -> SaveItem | None:
        return self.item_map.get(self._selected) if self._selected else None

    def _load_thumb(self, path: Path | None, max_w: int, max_h: int,
                    badge: str = ""):
        """优先用 PIL(支持 webp/jpg 且缩放质量好), 否则退回 tk.PhotoImage。
        badge 非空时把位置角标画到图片右上角(浅色底黑字)。"""
        if not path or not path.exists():
            return None
        if HAS_PIL:
            try:
                img = Image.open(path)
                img.thumbnail((max_w, max_h))
                if badge:
                    img = img.convert("RGBA")
                    self._draw_badge(img, badge)
                return ImageTk.PhotoImage(img)
            except Exception:
                return None
        try:
            img = tk.PhotoImage(file=str(path))
        except tk.TclError:
            return None
        factor = max(1, -(-max(img.width(), img.height()) // min(max_w, max_h)))
        if factor > 1:
            img = img.subsample(factor, factor)
        return img

    @staticmethod
    def _draw_badge(img, text: str):
        """在图片右上角画浅色底黑字的位置角标。"""
        from PIL import ImageDraw, ImageFont
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("msyh.ttc", 13)
        except OSError:
            font = ImageFont.load_default()
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad, r, m = 4, 4, 3           # 文字内边距, 圆角半径, 距图边缘外边距
        x1, y1 = img.width - tw - pad * 2 - m, m
        d.rounded_rectangle((x1, y1, img.width - m, y1 + th + pad * 2),
                            radius=r, fill="#f0f0f0")
        d.text((x1 + pad - bbox[0], y1 + pad - bbox[1]), text,
               font=font, fill="#000000")

    def show_preview(self):
        it = self.selected_item()
        if not it:
            return
        img = self._load_thumb(it.thumb_big, 380, 300,
                               badge=self._pos_map.get(it.base, ""))
        self._detail_img = img
        if img:
            self.thumb_label.configure(image=img, text="")
        else:
            self.thumb_label.configure(image="", text="(无预览图)")
        pos = self._pos_map.get(it.base)
        lines = [
            f"名称: {it.name or '(未解析)'}",
            f"车型: {self.car_display(it)}",
            f"作者: {it.creator or '?'}",
        ]
        if pos:
            lines.append(f"游戏内位置: {pos}")
        lines += [
            f"日期: {it.ts.strftime('%Y-%m-%d %H:%M:%S') if it.ts else '?'}",
            f"状态: {'已分享' if it.published else '本地'}",
            f"大小: {fmt_size(it.total_size)}",
        ]
        gid = self._dup_group.get(it.base, 0)
        if gid:
            n = sum(1 for g in self._dup_group.values() if g == gid)
            rules = "、".join(self._dup_rules.get(gid, []))
            suffix = f" ({rules})" if rules else ""
            lines.append(f"重复: 第 {gid} 组, 共 {n} 个相同涂装{suffix}")
        if it.layer_count:
            lines.append(f"层数: {it.layer_count}")
        lines.append(f"文件: {it.base}")
        if it.header_car_id and it.header_car_id != it.car_id:
            lines.append(f"注意: header 内嵌车型 ID {it.header_car_id} 与文件名不一致")
        if it.desc:
            lines.append(f"描述: {it.desc}")
        self._set_info("\n".join(lines))

    def show_big_preview(self):
        it = self.selected_item()
        if not it or not it.thumb_big:
            messagebox.showinfo("预览", "该条目没有预览图")
            return
        ZoomPreview(self, it)

    def copy_text(self, text: str):
        """复制文本到剪贴板并在状态栏反馈。"""
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set(f"已复制: {ellipsize(text, 60)}")

    # ------------------------------------------------------------ 详情文本(只读/复制)

    def _set_info(self, text: str):
        """更新详情文本。Text 保持 NORMAL(才能鼠标选择), 靠按键拦截实现只读。"""
        self.info_text.delete("1.0", tk.END)
        self.info_text.insert("1.0", text)

    def _info_key(self, e):
        """只读: 放行 Ctrl+C(原生复制), 其余按键拦截。"""
        if e.keysym.lower() == "c" and e.state & 0x4:
            return None
        return "break"

    def _info_selection(self) -> str:
        try:
            return self.info_text.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            return ""

    def _info_menu(self, e):
        menu = tk.Menu(self, tearoff=0)
        sel = self._info_selection()
        menu.add_command(label="复制", state=tk.NORMAL if sel else tk.DISABLED,
                         command=lambda: self.copy_text(sel))
        menu.add_command(label="全选", command=self._info_select_all)
        menu.add_command(label="复制全部",
                         command=lambda: self.copy_text(
                             self.info_text.get("1.0", tk.END).strip()))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _info_select_all(self):
        self.info_text.tag_add(tk.SEL, "1.0", tk.END)
        self.info_text.mark_set(tk.INSERT, "1.0")
        self.info_text.see(tk.INSERT)

    def _on_ctrl_c(self, _e):
        """焦点不在文本/输入控件时, Ctrl+C 复制选中涂装的游戏内位置。"""
        if isinstance(self.focus_get(), (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox)):
            return   # 文本控件走原生复制
        pos = self._pos_map.get(self._selected or "", "")
        if pos:
            self.copy_text(pos)

    # ------------------------------------------------------------ 操作(只读)

    def backup_all(self):
        if not self.current:
            return
        try:
            out = self.ops.backup_all(Path(self.current["dir"]))
            messagebox.showinfo("备份完成", f"已备份整个存档目录到:\n{out}")
            self.status_var.set(f"备份完成: {out}")
        except OSError as e:
            messagebox.showerror("备份失败", str(e))

    def open_folder(self):
        if self.current:
            os.startfile(self.current["dir"])  # noqa: S606

    def open_item_folder(self):
        it = self.selected_item()
        if not it:
            return
        target = it.folder / it.base if it.is_dir else it.folder
        os.startfile(target)  # noqa: S606


if __name__ == "__main__":
    App().mainloop()
