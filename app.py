# -*- coding: utf-8 -*-
"""
app.py — FH6 涂装查看器 (GUI)

仅查看《极限竞速:地平线 6》存档中的涂装, 主视图为缩略图平铺, 只读。
车型名表 cars.json 随程序分发(打包时内嵌进 exe, 不再释放到用户目录)。

用法: python app.py
打包: python -m PyInstaller FH6LiveryViewer.spec(勿用损坏的 pyinstaller.exe shim)
"""

from __future__ import annotations

import ctypes
import os
import queue
import re
import sys
import threading
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import fh6save
import gamemem
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

APP_VERSION = "1.3.0"
PROJECT_URL = "https://github.com/Hx-zh/fh6-livery-viewer"
RELEASES_URL = PROJECT_URL + "/releases"

CARD_W, CARD_H = 196, 200      # 卡片尺寸
ROW_H = CARD_H + 8             # 网格行距(卡片高 + 上下间距)
COL_W = CARD_W + 8             # 网格列距
THUMB_W, THUMB_H = 184, 120    # 卡片缩略图区域

SORT_OPTIONS = ["下载日期(新→旧)", "下载日期(旧→新)", "名称", "车型", "作者", "游戏内顺序"]

# 重复场景规则(标签, 菜单显示文案); 标签与 fh6save.detect_duplicates 的规则标签一致
DUP_RULES = [
    ("同车复刻", "同车复刻(经典涂装多人复刻)"),
    ("同车微调", "同车微调(同一作者的 v1/v2 版本)"),
    ("跨车型移植", "跨车型移植(同一涂装的多车型版)"),
    ("无图同名", "无图同名(无缩略图的同名涂装)"),
]

# 按键节奏默认值(毫秒): 「我的設計」网格二分实测, 周期(按下保持+键间间隔)阈值 ≈30ms, 默认 40ms 留 10ms 余量(低帧率机器保险)
# 用户可在「设置」里调整; 为保持单文件零外部文件零注册表, 设置仅本次运行有效, 不落盘
DEFAULT_KEY_HOLD_MS = 15    # 按下保持
DEFAULT_KEY_GAP_MS = 25     # 键间间隔(无间隔会把连发合并吞键)
GAME_EXE = "forzahorizon6.exe"

# user32/kernel32 原型: HWND 是 64 位指针, ctypes 默认 restype=int 会截断, 必须显式声明
_u32, _k32 = ctypes.windll.user32, ctypes.windll.kernel32
_u32.GetForegroundWindow.restype = wintypes.HWND
_u32.GetWindow.restype = wintypes.HWND


def find_game_window(exe: str = GAME_EXE):
    """按进程名找游戏主窗口(可见、无 owner、有标题的顶层窗口), 返回 HWND 或 None。"""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lp):
        if (not _u32.IsWindowVisible(hwnd) or _u32.GetWindow(hwnd, 4)   # GW_OWNER
                or _u32.GetWindowTextLengthW(hwnd) == 0):
            return True
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = _k32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            try:
                buf = ctypes.create_unicode_buffer(260)
                n = wintypes.DWORD(len(buf))
                if (_k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
                        and buf.value.lower().endswith(exe)):
                    found.append(hwnd)
                    return False                       # 找到即停
            finally:
                _k32.CloseHandle(h)
        return True

    _u32.EnumWindows(_cb, 0)
    return found[0] if found else None


def force_foreground(hwnd) -> bool:
    """把窗口切到前台。AttachThreadInput 绕过 SetForegroundWindow 的前台权限限制。"""
    if _u32.IsIconic(hwnd):
        _u32.ShowWindow(hwnd, 9)                         # SW_RESTORE
    if _u32.GetForegroundWindow() == hwnd:
        return True
    fg = _u32.GetForegroundWindow()
    cur = _k32.GetCurrentThreadId()
    fg_tid = _u32.GetWindowThreadProcessId(fg, None)
    tgt_tid = _u32.GetWindowThreadProcessId(hwnd, None)
    _u32.AttachThreadInput(cur, fg_tid, True)
    if tgt_tid != fg_tid:
        _u32.AttachThreadInput(cur, tgt_tid, True)
    try:
        _u32.BringWindowToTop(hwnd)
        _u32.SetForegroundWindow(hwnd)
    finally:
        _u32.AttachThreadInput(cur, fg_tid, False)
        if tgt_tid != fg_tid:
            _u32.AttachThreadInput(cur, tgt_tid, False)
    return _u32.GetForegroundWindow() == hwnd


def fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def ellipsize(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# 「已喷涂检测」机制与风险说明: 「⚠ 检测喷涂状态」按钮与已喷涂筛选开关共用的确认框文案
# (本会话未扫描过时, 打开任一已喷涂开关也弹同款确认框, 取消则开关回退无效)
APPLIED_NOTICE = """已喷涂检测(标记哪些涂装正喷在车上)通过只读扫描游戏进程内存实现:

· 原理: 游戏车库表常驻内存, 工具只读匹配其中的涂装文件名记录;
· 方式: OpenProcess + ReadProcessMemory, 不修改内存、不 hook、不附加调试器;
· 前提: 需要游戏正在运行。

风险提示: 只读外部读取不触发调试器检测, 理论风险远低于修改器,
但不能保证绝对零风险。介意请勿使用相关开关, 或仅在离线模式下使用。"""


def _build_applied_sprite():
    """生成「已喷在车上」喷漆罐角标素材(34×34 RGBA, 贴图片左上角)。

    图案用 4 倍超采样绘制再缩小, 保证小尺寸下边缘平滑;
    琥珀色沿用 Okabe-Ito #E69F00(色盲安全), 与详情面板「喷涂状态」呼应。"""
    from PIL import ImageDraw
    S = 4                                # 超采样倍数(抗锯齿)
    leg = 34                             # 三角直角边长(贴图片左上角)
    badge = Image.new("RGBA", (leg * S, leg * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(badge)

    def p(v):
        return v * S

    d.polygon([(0, 0), (leg * S - 1, 0), (0, leg * S - 1)],
              fill=(230, 159, 0, 245))           # 贴角三角
    # 喷漆罐(深色): 先在 16px 小图里正立画好, 逆时针转 45° 让罐身顺斜边,
    # 再贴进三角靠角位置
    ink = (30, 25, 20, 255)
    tile = Image.new("RGBA", (p(16), p(16)), (0, 0, 0, 0))
    t = ImageDraw.Draw(tile)
    t.rounded_rectangle((p(6), p(7), p(10) - 1, p(13) - 1),
                        radius=p(1), fill=ink)           # 罐身
    t.rectangle((p(7), p(5), p(9) - 1, p(7) - 1), fill=ink)     # 罐顶
    t.rectangle((p(7), p(4), p(9) - 1, p(5) - 1), fill=ink)     # 喷嘴
    for cx, cy in ((4.5, 2.2), (2.3, 1.2), (2.2, 4.5)):         # 漆雾
        t.ellipse((p(cx - 0.9), p(cy - 0.9), p(cx + 0.9) - 1, p(cy + 0.9) - 1),
                  fill=ink)
    tile = tile.rotate(45, resample=Image.Resampling.BICUBIC)
    badge.alpha_composite(tile, (p(3), p(3)))
    return badge.resize((leg, leg), Image.Resampling.LANCZOS)


_applied_sprite = None


def _applied_badge_sprite():
    """喷漆罐角标预制素材单例: 全卡一致只生成一次, 之后每张缩略图只做一次贴图。"""
    global _applied_sprite
    if _applied_sprite is None:
        _applied_sprite = _build_applied_sprite()
    return _applied_sprite


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
            w.bind("<Button-3>", self._on_rclick)   # 右键菜单(预览/定位/复制)
        self.set_selected(False)   # 初始化边框

    def _on_rclick(self, e):
        """右键菜单: 查看缩略图/定位到涂装 + 复制位置/名称/车型/作者。"""
        self.app.select(self.item.base)
        it = self.item
        pos = self.app._pos_map.get(it.base, "")
        car = self.app.car_table.name("fh6", it.car_id)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="查看缩略图",
                         state=tk.NORMAL if it.thumb_big else tk.DISABLED,
                         command=self.app.show_big_preview)
        menu.add_command(label="定位到涂装位置",
                         state=tk.NORMAL if pos else tk.DISABLED,
                         command=self.app.auto_locate)
        menu.add_separator()
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
        """双击 = 自动定位到游戏内该涂装(右键菜单仍可查看缩略图)。"""
        self.app.select(self.item.base)
        self.app.auto_locate()

    def set_image(self, img):
        if img:
            self.img_label.configure(image=img, text="")
        else:
            self.img_label.configure(image="", text="(无预览图)")

    def set_selected(self, on: bool):
        self._sel = on
        self._refresh_colors()

    def _refresh_colors(self):
        if getattr(self, "_sel", False):
            fill, name_fg, car_fg, sub_fg = "#0078D7", "#ffffff", "#eaf4ff", "#cce6ff"
            border = "#005a9e"          # 选中: 蓝框蓝底
        else:
            fill, name_fg, car_fg, sub_fg = "#ffffff", "#000000", "#333333", "#888888"
            border = "#cccccc"          # 默认: 白底浅灰边
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
        self._img_badged: dict[str, bool] = {}    # base -> 缓存图是否已画「已喷涂」角标
        self._pos_map: dict[str, str] = {}        # base -> 游戏内位置 ("N行M列")
        self._dup_group: dict[str, int] = {}      # base -> 重复组号 (0 = 无重复)
        self._car_unique: dict[int, int] = {}     # 车型 ID -> 唯一涂装数
        self._dup_rules: dict[int, list[str]] = {}  # 重复组号 -> 命中规则标签
        self._dup_feats: dict | None = None       # 重复检测预计算特征(后台线程填充)
        self._dup_pending = False                 # 重复分析进行中(防重入)
        self._applied: set[str] | None = None     # 车上涂装 base 集合(运行时内存扫描, None=未扫描)
        self._applied_pending = False             # 已喷涂扫描进行中(防重入)
        self._mem_reader = None                   # 常驻 gamemem.GameMemoryReader(缓存命中区域)
        self._mem_pid = None                      # 上次扫描时的游戏 PID(变了则重建 reader)
        self._layout: dict[str, tuple[int, int]] = {}  # base -> (行, 列)
        self._total_cols = 0                      # 「我的涂装」总列数
        self._locate_running = False              # 方向键发送中
        self._locate_cancel = False               # 取消发送标志(发送线程逐键检查)
        self._locate_keys: list[tuple[str, int]] = []
        self._locate_hwnd = None                  # 游戏窗口句柄
        # 按键节奏(会话级, 不落盘): 默认 15+25=40ms, 「设置」里可改
        self.key_hold_ms = DEFAULT_KEY_HOLD_MS
        self.key_gap_ms = DEFAULT_KEY_GAP_MS
        self._thumb_pending: list[str] = []
        self._thumb_gen = 0                       # 缩略图解码代次(rebuild 递增, 过期结果丢弃)
        self._thumb_inflight = 0                  # 线程池中未回的解码任务数(drain 启停用)
        self._thumb_queue: queue.Queue = queue.Queue()   # 工作线程 -> 主线程的解码结果
        self._thumb_pool = (ThreadPoolExecutor(max_workers=4) if HAS_PIL else None)
        self._shown: list[str] = []               # 当前显示的 base(网格顺序, 增量重排用)
        self._applied_from_button = False         # 本次扫描来自确认流程(完成后弹「已标记喷涂」)
        self._selected: str | None = None
        self._detail_img = None
        self._cols = 1
        self._relayout_job = None
        self._win_rows = (0, 0)                   # 当前 grid 进画布的行窗口 (r0, r1)
        self._scroll_job = None                   # 滚动防抖 job(重铺可视窗口)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        if HAS_PIL:
            _applied_badge_sprite()            # 预热喷漆罐角标素材, 免得线程池里竞态生成
        self.rescan_saves()
        self._applied_rescan_tick()          # 启动「已喷涂」定期快速重扫(开关打开且游戏运行时生效)

    def _on_close(self):
        """关窗: 停掉缩略图线程池(cancel 未开始的任务)再销毁。"""
        if self._thumb_pool is not None:
            self._thumb_pool.shutdown(wait=False, cancel_futures=True)
        self.destroy()

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
        # 置顶开关: 窗口浮在游戏上方, 方便随时调用(配合自动定位)
        self.topmost_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="置顶", variable=self.topmost_var,
                        command=self._toggle_topmost).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text="设置", command=self.open_settings).pack(side=tk.RIGHT,
                                                                      padx=2)
        ttk.Button(bar, text="⚠ 检测喷涂状态",
                   command=self.confirm_detect_applied).pack(side=tk.RIGHT, padx=2)

        flt = ttk.Frame(self, padding=(6, 0, 6, 6))
        flt.pack(fill=tk.X)
        ttk.Label(flt, text="搜索:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        ent = ttk.Entry(flt, textvariable=self.search_var, width=28)
        ent.pack(side=tk.LEFT, padx=(2, 10))
        ent.bind("<KeyRelease>", lambda _e: self.rebuild_grid())
        # 涂装筛选: 全部为独立开关(可多选组合), 首次打开任一开关时才触发后台重复分析
        self.dup_only = tk.BooleanVar(value=False)
        self.rule_vars = {tag: tk.BooleanVar(value=False) for tag, _ in DUP_RULES}
        self.multi_only = tk.BooleanVar(value=False)
        self.single_only = tk.BooleanVar(value=False)
        self.applied_mark = tk.BooleanVar(value=False)
        self.applied_only = tk.BooleanVar(value=False)
        self.unapplied_only = tk.BooleanVar(value=False)
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

        # 右侧: 涂装筛选(全部独立开关, 多规则 OR 组合; 经典 tk.Menubutton 凸起边框)
        self.filter_mb = tk.Menubutton(flt, text="涂装筛选", relief=tk.RAISED, bd=1,
                                       bg="#e1e1e1", activebackground="#ececec",
                                       padx=10, pady=2)
        fmenu = tk.Menu(self.filter_mb, tearoff=0)
        fmenu.add_checkbutton(label="仅显示重复涂装(不限场景)", variable=self.dup_only,
                              command=self._on_dup_switch)
        fmenu.add_separator()
        for tag, label in DUP_RULES:
            fmenu.add_checkbutton(label=label, variable=self.rule_vars[tag],
                                  command=self._on_dup_switch)
        fmenu.add_separator()
        # 车型维度(可叠加, 二者互斥)
        fmenu.add_checkbutton(label="仅显示多涂装车型(≥2 种)", variable=self.multi_only,
                              command=lambda: self._select_dup_filter("multi"))
        fmenu.add_checkbutton(label="仅显示单涂装车型(仅 1 种)", variable=self.single_only,
                              command=lambda: self._select_dup_filter("single"))
        fmenu.add_separator()
        fmenu.add_checkbutton(label="标记喷涂状态(喷漆角标)", variable=self.applied_mark,
                              command=self._on_applied_switch)
        fmenu.add_checkbutton(label="仅显示已喷涂(在车上)", variable=self.applied_only,
                              command=lambda: self._select_applied_filter("applied"))
        fmenu.add_checkbutton(label="仅显示未喷涂(不在车上)", variable=self.unapplied_only,
                              command=lambda: self._select_applied_filter("unapplied"))
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
                          ("自动定位到游戏", self.auto_locate),
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
                      "工具仅读取本地内容，不提供任何修改、解锁或联机功能。\n"
                      "使用本工具产生的任何后果由使用者自行承担。\n").pack(
                     fill=tk.X, pady=(4, 0))

        # 左侧: 可滚动的平铺画布, 占满剩余空间
        grid_wrap = ttk.Frame(main)
        grid_wrap.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(grid_wrap, highlightthickness=0, bg="#fafafa")
        vsb = ttk.Scrollbar(grid_wrap, orient=tk.VERTICAL, command=self.canvas.yview)
        self.vsb = vsb
        self.canvas.configure(yscrollcommand=self._on_yview)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.grid_frame = tk.Frame(self.canvas, bg="#fafafa")
        self._canvas_win = self.canvas.create_window((0, 0), window=self.grid_frame,
                                                     anchor=tk.NW)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # 全局滚轮: 指针在网格区域内才滚动(不能靠 Enter/Leave, 卡片子窗体会打断)
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W, padding=(6, 2)).pack(fill=tk.X, side=tk.BOTTOM)
        self.bind("<Control-c>", self._on_ctrl_c)
        self.bind("<Escape>", lambda _e: self._cancel_locate())

    # ------------------------------------------------------------ 平铺布局

    def _on_wheel(self, e):
        """指针在左侧网格区域时滚动画布(含卡片子窗体上方)。
        winfo_containing 可能 KeyError(如悬在 ttk Combobox 下拉 popdown 上——
        该窗口只有 Tcl 对象没有 Python 控件), 此时直接忽略即可。"""
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except KeyError:
            return
        while w is not None:
            if w in (self.canvas, self.grid_frame):
                self.canvas.yview_scroll(int(-e.delta / 120), "units")
                return
            w = getattr(w, "master", None)

    def _on_yview(self, first, last):
        """滚动位置变化: 更新滚动条, 防抖后重铺可视窗口(见 _apply_visible_window)。"""
        self.vsb.set(first, last)
        if self._scroll_job:
            self.after_cancel(self._scroll_job)
        self._scroll_job = self.after(120, self._scroll_settled)

    def _scroll_settled(self):
        self._scroll_job = None
        self._apply_visible_window()

    def _on_canvas_configure(self, e):
        self.canvas.itemconfigure(self._canvas_win, width=e.width)
        cols = max(1, e.width // COL_W)
        if cols != self._cols:
            self._cols = cols
            if self._relayout_job:
                self.after_cancel(self._relayout_job)
            self._relayout_job = self.after(120, self._relayout)

    def _relayout(self):
        self._relayout_job = None
        self._apply_visible_window(force=True)

    def _apply_visible_window(self, force: bool = False):
        """只把可视区(上下各留余量)内的卡片 grid 进画布, 其余 grid_remove(控件保留)。

        Tk/Windows 下画布内嵌子控件在内容坐标超过约 8200px 后渲染会坏
        (缩略图只剩条带、文字整片丢失, 实测 9360px 高画布必现), 所以画布内
        任何时刻只保留可视窗口附近的行; grid_frame 移到窗口对应的画布 y,
        滚动区仍按全高设置, 滚动条与滚动位置语义不变。
        卡片对象全部保留(增量卡片池语义不变, 选中/缩略图缓存不受影响)。"""
        total = -(-len(self._shown) // self._cols) if self._shown else 0
        if not total:
            for card in self.cards.values():
                if card.winfo_manager():
                    card.grid_remove()
            self._win_rows = (0, 0)
            self.canvas.coords(self._canvas_win, 0, 0)
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
            return
        view_rows = max(1, -(-self.canvas.winfo_height() // ROW_H))
        top_row = min(total - 1, int(self.canvas.yview()[0] * total))
        r0 = max(0, top_row - 3)
        r1 = min(total, top_row + view_rows + 4)
        if not force and self._win_rows == (r0, r1):
            return
        self._win_rows = (r0, r1)
        base_row = r0 * self._cols
        win = set()
        for i in range(base_row, min(len(self._shown), r1 * self._cols)):
            card = self.cards.get(self._shown[i])
            if card:
                card.grid(row=(i - base_row) // self._cols, column=i % self._cols,
                          padx=4, pady=4, sticky=tk.NW)
                win.add(self._shown[i])
        for base, card in self.cards.items():
            if base not in win and card.winfo_manager():
                card.grid_remove()
        self.canvas.coords(self._canvas_win, 0, r0 * ROW_H)
        self.canvas.configure(
            scrollregion=(0, 0, self._cols * COL_W, total * ROW_H))

    def rebuild_grid(self):
        """增量卡片池: 卡片只建一次, 筛选/排序/刷新只重排(grid)或移出网格(grid_remove),
        不销毁重建(实测销毁 1000 卡 ≈1.8s, 纯重排 ≈1ms); 切存档才在 scan_items 全量销毁。"""
        self._thumb_gen += 1                    # 作废旧解码结果(不注销缓存, 只防在途结果串档)
        self._thumb_pending = []
        shown = self._sorted(self._filtered())
        shown_bases = {it.base for it in shown}
        for it in shown:
            card = self.cards.get(it.base)
            if card is None:                    # 首次显示才创建(存档新增涂装也走这里)
                card = Card(self.grid_frame, self, it)
                self.cards[it.base] = card
            # 缓存图带不带「已喷涂」角标必须与当前状态一致, 否则重解码合成
            if (it.base not in self._img_cache
                    or self._img_badged.get(it.base) != self._applied_badged(it.base)):
                self._thumb_pending.append(it.base)
            # 已有缓存的直接贴图
            elif self._img_cache.get(it.base):
                card.set_image(self._img_cache[it.base])
        # 被过滤掉的卡片移出网格但保留控件, 再显示时复用; 选中状态随卡片存活
        for base, card in self.cards.items():
            if base not in shown_bases and card.winfo_manager():
                card.grid_remove()
        self._shown = [it.base for it in shown]
        self._relayout()
        self.after(30, self._load_thumbs_batch)
        parts = []
        if self.dup_only.get():
            parts.append("仅重复")
        parts += [tag for tag, _ in DUP_RULES if self.rule_vars[tag].get()]
        if self.multi_only.get():
            parts.append("多涂装车型")
        if self.single_only.get():
            parts.append("单涂装车型")
        if self.applied_mark.get():
            parts.append("标记喷涂")
        if self.applied_only.get():
            parts.append("仅已喷涂")
        if self.unapplied_only.get():
            parts.append("仅未喷涂")
        self.filter_mb.configure(
            text=f"涂装筛选: {'+'.join(parts)}" if parts else "涂装筛选")
        known = self.car_table.known_count("fh6")
        dup_groups = len({g for g in self._dup_group.values() if g})
        dup_files = sum(1 for g in self._dup_group.values() if g)
        dup_txt = f"重复 {dup_groups} 组({dup_files} 个)  |  " if dup_groups else ""
        applied_txt = f"已上车 {len(self._applied)}  |  " if self._applied is not None else ""
        self.status_var.set(
            f"共 {len(self.items)} 个条目, 显示 {len(shown)} 个  |  {applied_txt}{dup_txt}"
            f"已识别车型 {known} 个  |  {self.current['dir'] if self.current else ''}")

    def _load_thumbs_batch(self, n: int = 8):
        """加载缩略图: 有 PIL 时解码+合成放线程池, 主线程只做 PhotoImage 贴图;
        无 PIL 退回主线程分批(tk.PhotoImage)。"""
        if self._thumb_pool is not None:
            self._submit_thumb_jobs()
            return
        batch, self._thumb_pending = self._thumb_pending[:n], self._thumb_pending[n:]
        for base in batch:
            card = self.cards.get(base)
            if not card:
                continue
            applied = self._applied_badged(base)
            img = self._load_thumb(card.item.thumb_big, THUMB_W, THUMB_H,
                                   badge=self._pos_map.get(base, ""),
                                   applied=applied)
            if img:
                # 解码失败(如游戏正在写入该文件)不缓存, 下次重建网格时自动重试,
                # 否则 None 进缓存会让卡片永远卡在「加载中…」
                self._img_cache[base] = img
                self._img_badged[base] = applied
            card.set_image(img)
        if self._thumb_pending:
            self.after(10, self._load_thumbs_batch)

    def _submit_thumb_jobs(self):
        """待解码条目全部提交线程池; 结果经队列回主线程 drain。"""
        pool = self._thumb_pool
        if pool is None:                # 调用方已保证有 PIL 才走到这, 防御一下
            return
        gen = self._thumb_gen
        pending, self._thumb_pending = self._thumb_pending, []
        for base in pending:
            card = self.cards.get(base)
            if not card:
                continue
            self._thumb_inflight += 1
            pool.submit(self._decode_thumb_job, gen, base,
                        card.item.thumb_big,
                        self._pos_map.get(base, ""),
                        self._applied_badged(base))
        if self._thumb_inflight:
            self.after(30, self._drain_thumb_queue)

    def _decode_thumb_job(self, gen: int, base: str, path: Path | None,
                          badge: str, applied: bool):
        """工作线程: 解码+合成(PIL 解码/缩放释放 GIL), 结果入队等主线程贴图。"""
        img = self._compose_thumb(path, THUMB_W, THUMB_H, badge, applied) \
            if path and path.exists() else None
        self._thumb_queue.put((gen, base, img, applied))

    def _drain_thumb_queue(self):
        """主线程: 取回解码好的图, 转 PhotoImage 贴卡并写缓存(每轮最多 16 张保持响应)。"""
        for _ in range(16):
            try:
                gen, base, img, applied = self._thumb_queue.get_nowait()
            except queue.Empty:
                break
            self._thumb_inflight -= 1
            if gen != self._thumb_gen:
                continue                        # 期间又 rebuild 过, 过期结果丢弃
            card = self.cards.get(base)
            if not card:
                continue
            photo = ImageTk.PhotoImage(img) if img is not None else None
            if photo:
                # 解码失败不缓存, 下次重建网格时自动重试(同主线程路径的容错)
                self._img_cache[base] = photo
                self._img_badged[base] = applied
            card.set_image(photo)
        if self._thumb_inflight > 0 or not self._thumb_queue.empty():
            self.after(30, self._drain_thumb_queue)

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
            self._dup_pending = False
            self._applied = None
            self._applied_pending = False
            self._layout, self._total_cols = {}, 0
            self._img_cache.clear()
            self._img_badged.clear()
            for c in self.cards.values():
                c.destroy()
            self.cards = {}
            self._shown = []
            self._selected = None
            self._set_info("未选择条目")
            self.thumb_label.configure(image="", text="(无预览)")
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
        self._cancel_locate()
        self._total_cols, self._layout = fh6save.game_layout(self.items)
        self._pos_map = {b: f"{x}行{y}列" for b, (x, y) in self._layout.items()}
        self._dup_group, self._car_unique = {}, {}
        self._dup_rules = {}
        self._dup_feats = None
        self._dup_pending = False
        self._applied = None                 # 车上涂装标记与游戏实时档案绑定, 切存档即失效
        self._applied_pending = False
        self._img_cache.clear()
        self._img_badged.clear()
        for c in self.cards.values():       # 切存档: 卡片池全量销毁(平时 rebuild 增量复用)
            c.destroy()
        self.cards = {}
        self._shown = []
        self._selected = None
        self._set_info("未选择条目")
        self.thumb_label.configure(image="", text="(无预览)")
        # 车厂下拉: 只列出当前存档涂装实际涉及的车厂
        brands = sorted({b for it in self.items if it.itype == "Livery"
                         for b in [self._brand_of(it)] if b}, key=str.lower)
        self.brand_combo.configure(values=["全部车厂"] + brands)
        if self.brand_var.get() not in ("全部车厂", *brands):
            self.brand_var.set("全部车厂")
        # 重复检测按需触发(解码缩略图算哈希很慢): 见 _ensure_dup_analysis
        self.rebuild_grid()

    def _dup_ready(self, items: list[SaveItem], feats: dict):
        if items is not self.items:
            return                     # 等待期间已切换存档, 丢弃(不碰新存档的状态)
        self._dup_pending = False
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

    def _on_dup_switch(self):
        """任一筛选开关切换: 刷新列表, 并按需启动重复分析。"""
        self.rebuild_grid()
        self._ensure_dup_analysis()   # 放 rebuild 之后, 状态栏"分析中"不被覆盖

    def _ensure_dup_analysis(self):
        """重复检测按需触发: 只在首次打开依赖它的开关时才后台解码缩略图算哈希。"""
        if self._dup_feats is not None or self._dup_pending or not self.items:
            return
        self._dup_pending = True
        items = self.items
        self.status_var.set("重复涂装分析中(解码缩略图计算哈希)…")

        def _work():
            feats = fh6save.extract_dup_features(items)
            self.after(0, lambda: self._dup_ready(items, feats))

        threading.Thread(target=_work, daemon=True).start()

    def _select_dup_filter(self, which: str):
        """车型维度的「多涂装」与「单涂装」互斥。"""
        if which == "multi" and self.multi_only.get():
            self.single_only.set(False)
        elif which == "single" and self.single_only.get():
            self.multi_only.set(False)
        self.rebuild_grid()
        self._ensure_dup_analysis()

    # ------------------------------------------------------------ 已喷涂检测(运行时内存)

    def _on_applied_switch(self):
        """「标记喷涂状态」开关: 与「⚠ 检测喷涂状态」按钮同款确认门——
        本会话未扫描过时须确认才生效, 取消则开关回退(选择无效); 已扫描过直接生效。"""
        if self.applied_mark.get() and self._applied is None:
            if not self._confirm_applied_scan():
                self.applied_mark.set(False)
                return
            self._applied_from_button = True     # 完成同样弹「已标记喷涂」
        self.rebuild_grid()
        self._ensure_applied_scan()

    def _select_applied_filter(self, which: str):
        """「已喷涂」与「未喷涂」筛选互斥; 确认门与「标记喷涂状态」开关相同。"""
        var = self.applied_only if which == "applied" else self.unapplied_only
        if which == "applied" and self.applied_only.get():
            self.unapplied_only.set(False)
        elif which == "unapplied" and self.unapplied_only.get():
            self.applied_only.set(False)
        if var.get() and self._applied is None:
            if not self._confirm_applied_scan():
                var.set(False)
                return
            self._applied_from_button = True
        self.rebuild_grid()
        self._ensure_applied_scan()

    def _applied_status(self, base: str) -> str:
        """详情面板用: 喷涂状态文案(未扫描/不适用返回空串)。"""
        if self._applied is None:
            return ""
        return "已喷在车上 ✓" if base in self._applied else "未喷在车上"

    def _applied_badged(self, base: str) -> bool:
        """该卡片缩略图当前是否应带「已喷在车上」角标(缓存合成比对用)。"""
        return bool(self.applied_mark.get() and self._applied
                    and base in self._applied)

    def _ensure_applied_scan(self, force: bool = False):
        """已喷涂检测按需触发: 仅 FH6 存档 + 游戏运行中; 后台只读扫描游戏内存。
        确认弹窗不在此处——统一由入口(按钮/开关)的 _confirm_applied_scan() 把关。"""
        if not self.current or self.current.get("game") != "fh6":
            return
        if self._applied_pending or (self._applied is not None and not force):
            return
        pid = gamemem.find_game_pid()
        if not pid:
            self.status_var.set("已喷涂检测: 未检测到游戏进程 (游戏启动后可重试)")
            return
        self._applied_pending = True
        self.status_var.set("已喷涂检测: 只读扫描游戏内存中…")

        def _work():
            try:
                if self._mem_reader is None or self._mem_pid != pid:
                    if self._mem_reader is not None:
                        self._mem_reader.close()
                    self._mem_reader = gamemem.GameMemoryReader(pid)
                    self._mem_pid = pid
                names = self._mem_reader.scan_applied_liveries()
            except OSError as e:
                names, err = None, str(e)
            else:
                err = None
            self.after(0, lambda: self._applied_ready(names, err))

        threading.Thread(target=_work, daemon=True).start()

    def _applied_ready(self, names, err):
        self._applied_pending = False
        from_button = self._applied_from_button
        self._applied_from_button = False
        if err or names is None:
            self.status_var.set(f"已喷涂检测失败: {err or '读取失败'}")
            if from_button:
                messagebox.showwarning("检测喷涂状态",
                                       f"检测失败: {err or '读取失败'}", parent=self)
            return
        self._applied = names
        self.rebuild_grid()
        self.status_var.set(f"已喷涂检测: {len(names)} 个涂装正在车上")
        if from_button:
            messagebox.showinfo("检测喷涂状态",
                                f"已标记喷涂\n\n{len(names)} 个涂装正喷在车上, 已用喷漆角标标出。",
                                parent=self)

    def _applied_rescan_tick(self):
        """任一已喷涂相关开关打开时: 未扫描且游戏在跑则补全量扫描, 已扫描则定期快速重扫。"""
        want = (self.applied_mark.get() or self.applied_only.get()
                or self.unapplied_only.get())
        if want and not self._applied_pending:
            if self._applied is None:
                if gamemem.find_game_pid():
                    self._ensure_applied_scan()     # 开关已开但游戏刚启动, 补一次全量扫描
            elif self._mem_reader is not None:
                self._applied_pending = True
                reader = self._mem_reader

                def _work():
                    try:
                        names = reader.rescan_applied_liveries()
                    except OSError:
                        names = None
                    self.after(0, lambda: self._applied_update(names))
                threading.Thread(target=_work, daemon=True).start()
        self.after(5000, self._applied_rescan_tick)

    def _applied_update(self, names):
        self._applied_pending = False
        if names is not None and names != self._applied:
            self._applied = names
            self.rebuild_grid()
            self.status_var.set(f"已喷涂检测: {len(names)} 个涂装正在车上")

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
        active_rules = [tag for tag, _ in DUP_RULES if self.rule_vars[tag].get()]
        out = []
        for it in self.items:
            if it.itype != "Livery":          # 仅限涂装
                continue
            if self.dup_only.get() or active_rules:
                gid = self._dup_group.get(it.base, 0)
                if not gid:
                    continue
                # 多规则 OR: 组命中任一选中的场景即保留
                if active_rules and not any(
                        r in self._dup_rules.get(gid, []) for r in active_rules):
                    continue
            unique = self._car_unique.get(it.car_id, 0)
            if self.multi_only.get() and unique < 2:
                continue
            if self.single_only.get() and unique != 1:
                continue
            if self.applied_only.get():
                if self._applied is None:
                    self._ensure_applied_scan()     # 未扫描先触发, 扫描完成前不过滤
                elif it.base not in self._applied:
                    continue
            if self.unapplied_only.get():
                if self._applied is None:
                    self._ensure_applied_scan()
                elif it.base in self._applied:
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
                    badge: str = "", applied: bool = False):
        """优先用 PIL(支持 webp/jpg 且缩放质量好), 否则退回 tk.PhotoImage。
        badge 非空时把位置角标画到图片右上角(浅色底黑字);
        applied 为真时在左上角贴「已喷在车上」喷漆罐角标(仅 PIL 路径支持角标)。"""
        if not path or not path.exists():
            return None
        if HAS_PIL:
            img = self._compose_thumb(path, max_w, max_h, badge, applied)
            return ImageTk.PhotoImage(img) if img is not None else None
        try:
            img = tk.PhotoImage(file=str(path))
        except tk.TclError:
            return None
        factor = max(1, -(-max(img.width(), img.height()) // min(max_w, max_h)))
        if factor > 1:
            img = img.subsample(factor, factor)
        return img

    def _compose_thumb(self, path: Path, max_w: int, max_h: int,
                       badge: str = "", applied: bool = False):
        """PIL 解码+缩放+角标合成, 返回 PIL 图(失败 None)。
        纯 PIL 无 tk 依赖, 可在工作线程里跑(缩略图线程池用)。"""
        try:
            img = Image.open(path)
            img.thumbnail((max_w, max_h))
            if badge or applied:
                img = img.convert("RGBA")
                if badge:
                    self._draw_badge(img, badge)
                if applied:
                    self._draw_applied_badge(img)
            return img
        except Exception:
            return None

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

    @staticmethod
    def _draw_applied_badge(img):
        """把预制的「已喷在车上」喷漆罐角标素材贴到图片左上角(一次 alpha_composite,
        不再逐张调用绘图原语; 素材见模块级 _applied_badge_sprite())。"""
        img.alpha_composite(_applied_badge_sprite(), (0, 0))

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
            lines.append(f"游戏内位置: {pos} (共 {self._total_cols} 列)")
            keys = fh6save.locate_keys(*self._layout[it.base], self._total_cols)
            path = " ".join(f"{d}×{n}" for d, n in keys)
            lines.append(f"按键路径: {path or '无需按键(就在 1行1列)'}")
        lines += [
            f"日期: {it.ts.strftime('%Y-%m-%d %H:%M:%S') if it.ts else '?'}",
            f"状态: {'已分享' if it.published else '本地'}",
            f"大小: {fmt_size(it.total_size)}",
        ]
        applied_txt = self._applied_status(it.base)
        if applied_txt:
            lines.append(f"喷涂状态: {applied_txt}")
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

    def _toggle_topmost(self):
        """置顶开关: 窗口浮在游戏上方, 方便随时调用。"""
        self.attributes("-topmost", self.topmost_var.get())

    def _confirm_applied_scan(self) -> bool:
        """机制/风险说明 + 确认框(「⚠ 检测喷涂状态」按钮与已喷涂筛选开关共用):
        用户确认且游戏运行中才返回 True。"""
        if not messagebox.askokcancel("检测喷涂状态",
                                      APPLIED_NOTICE + "\n\n确认开始检测？",
                                      parent=self):
            return False
        if not gamemem.find_game_pid():
            messagebox.showwarning("检测喷涂状态",
                                   "未检测到游戏进程。\n已喷涂检测需要游戏正在运行, 请启动游戏后重试。",
                                   parent=self)
            return False
        return True

    def confirm_detect_applied(self):
        """顶栏「⚠ 检测喷涂状态」: 确认后开始内存扫描并打开喷涂标记(喷漆角标);
        检测完成由 _applied_ready 弹「已标记喷涂」。"""
        if not self._confirm_applied_scan():
            return
        self._applied_from_button = True     # 标记本次扫描来自确认流程, 完成后弹结果
        self.applied_mark.set(True)          # 检测完即用喷漆角标标出
        self._ensure_applied_scan(force=True)

    def open_settings(self):
        """设置窗口: 自动定位的按键节奏(毫秒), 仅本次运行有效(不落盘)。"""
        dlg = tk.Toplevel(self)
        dlg.title("设置")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()                          # 模态

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="自动定位按键节奏 (毫秒, 周期 = 保持 + 间隔):").grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 6))
        ttk.Label(body, text="按下保持:").grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Label(body, text="键间间隔:").grid(row=2, column=0, sticky=tk.W, pady=2)
        hold_var = tk.StringVar(value=str(self.key_hold_ms))
        gap_var = tk.StringVar(value=str(self.key_gap_ms))
        ttk.Spinbox(body, from_=0, to=2000, width=8,
                    textvariable=hold_var).grid(row=1, column=1, sticky=tk.W, pady=2)
        ttk.Spinbox(body, from_=0, to=2000, width=8,
                    textvariable=gap_var).grid(row=2, column=1, sticky=tk.W, pady=2)
        ttk.Label(body, text="(仅本次运行有效)").grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=(2, 0))

        btns = ttk.Frame(body)
        btns.grid(row=4, column=0, columnspan=2, sticky=tk.E, pady=(10, 0))

        def _save():
            try:
                hold, gap = int(hold_var.get()), int(gap_var.get())
            except ValueError:
                messagebox.showerror("设置", "请输入整数毫秒值", parent=dlg)
                return
            if not (0 <= hold <= 2000 and 0 <= gap <= 2000):
                messagebox.showerror("设置", "取值范围 0~2000 毫秒", parent=dlg)
                return
            self.key_hold_ms, self.key_gap_ms = hold, gap
            self.status_var.set(f"按键节奏: 保持 {hold}ms + 间隔 {gap}ms (本次运行有效)")
            dlg.destroy()

        def _reset():
            hold_var.set(str(DEFAULT_KEY_HOLD_MS))
            gap_var.set(str(DEFAULT_KEY_GAP_MS))

        ttk.Button(btns, text="恢复默认", command=_reset).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="确定", command=_save).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text="取消", command=dlg.destroy).pack(side=tk.LEFT, padx=2)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        # 居中于主窗口
        dlg.update_idletasks()
        dw, dh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        dlg.geometry(f"+{self.winfo_rootx() + (self.winfo_width() - dw) // 2}"
                     f"+{self.winfo_rooty() + (self.winfo_height() - dh) // 2}")

    # ------------------------------------------------------------ 自动定位(发送方向键)

    def auto_locate(self):
        """立即切到游戏窗口并经 keybd_event 发送方向键; 发送中再点按钮或按 Esc 取消。"""
        if self._locate_running:
            self._cancel_locate()
            return
        it = self.selected_item()
        rc = self._layout.get(it.base) if it else None
        if not rc:
            msg = "该涂装没有游戏内位置信息" if it else "请先在左侧选中一个涂装"
            messagebox.showinfo("自动定位", msg)
            return
        keys = fh6save.locate_keys(rc[0], rc[1], self._total_cols)
        if not keys:
            self.status_var.set("该涂装就在 1行1列, 无需按键")
            return
        hwnd = find_game_window()
        if hwnd is None:
            messagebox.showinfo("自动定位", "未找到游戏窗口, 请先打开游戏")
            return
        if not force_foreground(hwnd):
            self.status_var.set("无法切换到游戏窗口, 已取消")
            return
        self._locate_hwnd = hwnd
        self._locate_keys = keys
        self._send_keys()

    def _cancel_locate(self):
        """取消发送; 无定位在进行时为空操作。"""
        if not self._locate_running:
            return
        self._locate_cancel = True      # 发送线程每个按键前检查
        self.status_var.set("自动定位已取消")

    def _send_keys(self):
        """后台线程依次发送方向键(Windows keybd_event, 纯 stdlib)。"""
        keys = self._locate_keys
        hold = self.key_hold_ms / 1000        # 主线程取快照, 避免发送中改设置
        gap = self.key_gap_ms / 1000
        self._locate_running = True
        self._locate_cancel = False

        def _work():
            import time
            if self._locate_hwnd is not None:
                time.sleep(0.5)                 # 等游戏拿到焦点再按键
            user32 = ctypes.windll.user32
            vk = {"←": 0x25, "↑": 0x26, "→": 0x27, "↓": 0x28}
            finished = True
            for sym, n in keys:
                for _ in range(n):
                    if self._locate_cancel:
                        finished = False
                        break
                    user32.keybd_event(vk[sym], 0, 0, 0)        # 按下
                    time.sleep(hold)
                    user32.keybd_event(vk[sym], 0, 2, 0)        # 抬起(KEYEVENTF_KEYUP)
                    time.sleep(gap)                             # 无间隔会吞键(实测)
                if not finished:
                    break
            self.after(0, lambda: self._locate_done(finished))

        threading.Thread(target=_work, daemon=True).start()

    def _locate_done(self, finished: bool):
        self._locate_running = False
        if finished:
            path = " ".join(f"{d}×{n}" for d, n in self._locate_keys)
            self.status_var.set(f"自动定位完成: {path}")
        else:
            self.status_var.set("自动定位已取消")

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
