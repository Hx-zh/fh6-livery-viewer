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
from bisect import bisect_right
import threading
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

import fh6save
import gamemem
from fh6save import CarTable, SaveItem, SaveOps
from i18n import tr as _

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

APP_VERSION = "1.6.0"
PROJECT_URL = "https://github.com/Hx-zh/fh6-livery-viewer"
RELEASES_URL = PROJECT_URL + "/releases"

CARD_W, CARD_H = 196, 200      # 卡片尺寸
ROW_H = CARD_H + 8             # 网格行距(卡片高 + 上下间距)
COL_W = CARD_W + 8             # 网格列距
THUMB_W, THUMB_H = 184, 120    # 卡片缩略图区域
HEADER_H = 28                  # 重复分组标题行高(通栏色带)

# 字体: UI 铬件(按钮/菜单/标签/状态)用微软雅黑; 用户数据文本(涂装名/作者/车型/
# 详情/搜索输入/位置角标)用 Consolas——实测微软雅黑的 I/l 都是光杆竖线不可区分,
# Consolas 的 I 有衬线横杠、l 带弯尾(Windows 自带, Vista 起); 其中的 CJK 字符
# 经 Windows 字体链接自动回退渲染(已实测 tkinter 下 CJK/假名/谚文无缺字)
FONT_UI = "Microsoft YaHei UI"
FONT_DATA = "Consolas"

SORT_OPTIONS = [_("下载日期(新→旧)"), _("下载日期(旧→新)"), _("名称"), _("车型"),
                _("作者"), _("车厂"), _("游戏内顺序")]
SUB_SORT_NONE = _("无")        # 次选下拉的「不启用」选项文案(默认选中)
GROUP_OPTIONS = [_("无"), _("车厂"), _("作者"), _("车型")]   # 「分组显示」维度(默认无=纯平铺)

# 重复检测无预制模式: 判定条件(车型/作者/图片距离/名称相似度/创建/下载时间)
# 由用户在「重复检测参数」对话框里自由组合, 出厂默认见 fh6save.DEFAULT_DUP_RULE。
# 对话框提供常用「模板」一键填表(仅表单预设, 引擎仍只有单条组合条件)
DUP_TEMPLATES = [
    (_("同车微调(v1/v2)"), _("同车型+同作者, 图片距离≤10 且名称相似度≥0.6"),
     dict(author="same", img=("dist", 10), name=("min", 0.6))),
    (_("跨车型移植"), _("不同车型+同作者, 图片距离≤6 且名称相似度≥0.6"),
     dict(car="diff", author="same", img=("dist", 6), name=("min", 0.6))),
    (_("同名异版"), _("车型/名称/作者相同而创建时间不同——疑似版本迭代"),
     dict(author="same", name=("min", 1.0), created="diff")),
    (_("多次下载"), _("车型/名称/作者/创建时间全同而下载时间不同——真重复副本, 可任选保留"),
     dict(author="same", name=("min", 1.0), created="same", downloaded="diff")),
]

# 按键节奏默认值(毫秒): 「我的設計」网格二分实测, 周期(按下保持+键间间隔)阈值 ≈30ms, 默认 40ms 留 10ms 余量(低帧率机器保险)
# 用户可在「设置」里调整; 为保持单文件零外部文件零注册表, 设置仅本次运行有效, 不落盘
DEFAULT_KEY_HOLD_MS = 15    # 按下保持
DEFAULT_KEY_GAP_MS = 25     # 键间间隔(无间隔会把连发合并吞键)
GAME_EXE = "forzahorizon6.exe"

# 自动刷新(改动1): 存档目录签名轮询周期 + 检测到变化后的稳定等待。
# 游戏/云同步写存档可能分批, 防抖期间再有变化顺延; 读到写了一半的文件
# 由现有容错兜底(webp 瞬态失败退避重试, header 解析失败下轮签名再变自愈)
WATCH_INTERVAL_MS = 3000
WATCH_DEBOUNCE_MS = 2000

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
APPLIED_NOTICE = _("""已喷涂检测(标记哪些涂装正喷在车上)通过只读扫描游戏进程内存实现:

· 原理: 游戏车库表常驻内存, 工具只读匹配其中的涂装文件名记录;
· 方式: OpenProcess + ReadProcessMemory, 不修改内存、不 hook、不附加调试器;
· 前提: 需要游戏正在运行。

风险提示: 只读外部读取不触发调试器检测, 理论风险远低于修改器,
但不能保证绝对零风险。介意请勿使用相关开关, 或仅在离线模式下使用。""")


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


class ZoomPreview(tk.Toplevel):
    """可缩放的大图预览: 滚轮/按钮缩放, 左键拖动平移。"""

    MIN_SCALE, MAX_SCALE = 0.05, 12.0

    def __init__(self, app: "App", item: SaveItem):
        super().__init__(app)
        self.title(_("{name} — 预览(滚轮缩放, 拖动平移)").format(name=item.name or item.base))
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
        ttk.Button(bar, text=_("放大 (+)"), command=lambda: self._zoom(1.25)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text=_("缩小 (−)"), command=lambda: self._zoom(1 / 1.25)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text=_("原始尺寸"), command=self._actual).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text=_("适应窗口"), command=self._fit).pack(side=tk.LEFT, padx=2)
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
            ttk.Label(self.canvas, text=_("无法加载预览图(需要 Pillow)"),
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
        self.title(_("FH6 涂装查看器"))
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
        self._img_cache: dict[str, object] = {}   # base -> PhotoImage
        self._pos_map: dict[str, str] = {}        # base -> 游戏内位置 ("N行M列")
        self._dup_group: dict[str, int] = {}      # base -> 重复组号 (0 = 无重复)
        self._car_unique: dict[int, int] = {}     # 车型 ID -> 唯一涂装数
        self._dup_rules: dict[int, list[str]] = {}  # 重复组号 -> 命中规则标签
        self._dup_feats: dict | None = None       # 重复检测预计算特征(后台线程填充)
        self._dup_pending = False                 # 重复分析进行中(防重入)
        self._dup_cfg = fh6save.DEFAULT_DUP_RULE  # 重复判定条件(参数对话框可改, 会话级)
        self.quick_filter: tuple | None = None    # 右键快筛 ("car",id)/("creator",name)/None
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
        self._thumb_fails: dict[str, int] = {}    # base -> 连续解码失败次数(瞬态重试用, 封顶放弃)
        self._thumb_gen = 0                       # 缩略图解码代次(rebuild 递增, 过期结果丢弃)
        self._thumb_inflight = 0                  # 线程池中未回的解码任务数(drain 启停用)
        self._thumb_queue: queue.Queue = queue.Queue()   # 工作线程 -> 主线程的解码结果
        self._thumb_pool = (ThreadPoolExecutor(max_workers=4) if HAS_PIL else None)
        self._applied_photo = None                # 喷漆罐角标的 PhotoImage(卡片角共用一份)
        self._pos_badge_font = None               # 位置角标字体(像素字号, 懒建缓存)
        self._shown: list[str] = []               # 当前显示的 base(行布局展开顺序, 计数用)
        self._rows: list[tuple] = []              # 行模型: ("cards",[base..]) / ("hdr",gid,文本)
        self._row_y: list[int] = []               # 每行起始 y 前缀和(长度 len(_rows)+1)
        self._content_h = 0                       # 画布内容总高(scrollregion 用)
        self._vis_cards: dict[str, dict] = {}     # 可视区各卡的 canvas item id 表(重配色/贴图用)
        self._redraw_win = None                   # 已绘行窗口 (r0, r1), 同窗跳过重绘
        self._filtered_items: list[SaveItem] = [] # 最近一次筛选结果(变列数重排复用, 不再走筛选)
        self._applied_from_button = False         # 本次扫描来自确认流程(完成后弹「已标记喷涂」)
        self._selected: str | None = None
        self._detail_img = None
        self._cols = 1
        self._relayout_job = None                 # 变列数防抖 job(重算行布局)
        self._save_sig: dict | None = None        # 自动刷新: items 当前反映的存档目录签名基线
        self._watch_job = None                    # 自动刷新: 变化防抖 job(None=无待触发刷新)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        if HAS_PIL:
            _applied_badge_sprite()            # 预热喷漆罐角标素材(贴卡时只做一次 PhotoImage 转换)
        self.rescan_saves()
        self._applied_rescan_tick()          # 启动「已喷涂」定期快速重扫(开关打开且游戏运行时生效)
        self.after(WATCH_INTERVAL_MS, self._watch_tick)   # 启动「自动刷新」轮询(默认开)

    def _on_close(self):
        """关窗: 取消自动刷新防抖 job, 停掉缩略图线程池(cancel 未开始的任务)再销毁。"""
        if self._watch_job is not None:
            self.after_cancel(self._watch_job)
            self._watch_job = None
        if self._thumb_pool is not None:
            self._thumb_pool.shutdown(wait=False, cancel_futures=True)
        self.destroy()

    # ------------------------------------------------------------ UI 构建

    def _build_ui(self):
        bar = ttk.Frame(self, padding=6)
        bar.pack(fill=tk.X)
        ttk.Label(bar, text=_("FH6 存档:")).pack(side=tk.LEFT)
        self.save_var = tk.StringVar()
        self.save_combo = ttk.Combobox(bar, textvariable=self.save_var,
                                       state="readonly", width=44)   # 60→44: 非中文顶栏更宽, 防右侧按钮被挤出
        self.save_combo.pack(side=tk.LEFT, padx=(2, 6))
        self.save_combo.bind("<<ComboboxSelected>>", lambda _e: self.load_current())
        for text, cmd in ((_("刷新"), self.rescan_saves),
                          (_("手动选择目录…"), self.browse_folder),
                          (_("备份整个存档"), self.backup_all),
                          (_("打开存档目录"), self.open_folder)):
            ttk.Button(bar, text=text, command=cmd).pack(side=tk.LEFT, padx=2)
        # 置顶开关: 窗口浮在游戏上方, 方便随时调用(配合自动定位)
        self.topmost_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text=_("置顶"), variable=self.topmost_var,
                        command=self._toggle_topmost).pack(side=tk.RIGHT, padx=2)
        # 自动刷新开关(默认开): 轮询存档目录, 变化时增量插入新卡片(见 _watch_tick)
        self.auto_refresh = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=_("自动刷新"),
                        variable=self.auto_refresh).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bar, text=_("设置"), command=self.open_settings).pack(side=tk.RIGHT,
                                                                         padx=2)
        ttk.Button(bar, text=_("⚠ 检测喷涂状态"),
                   command=self.confirm_detect_applied).pack(side=tk.RIGHT, padx=2)

        flt = ttk.Frame(self, padding=(6, 0, 6, 6))
        flt.pack(fill=tk.X)
        ttk.Label(flt, text=_("搜索:")).pack(side=tk.LEFT)
        # 双搜索框: 两个非空关键字都命中同一匹配串(名称/作者/车型 ID/车型名)才保留,
        # 即「与」组合——中间的 + 即此意; 只用第一个框时行为与旧版单框完全一致
        self.search_var = tk.StringVar()
        ent = ttk.Entry(flt, textvariable=self.search_var, width=16,
                        font=(FONT_DATA, 9))
        ent.pack(side=tk.LEFT, padx=(2, 0))
        ent.bind("<KeyRelease>", lambda _e: self.rebuild_grid())
        tk.Label(flt, text="+").pack(side=tk.LEFT, padx=3)
        self.search2_var = tk.StringVar()
        ent2 = ttk.Entry(flt, textvariable=self.search2_var, width=16,
                         font=(FONT_DATA, 9))
        ent2.pack(side=tk.LEFT)
        ent2.bind("<KeyRelease>", lambda _e: self.rebuild_grid())
        # 涂装筛选: 全部为独立开关(可多选组合), 首次打开任一开关时才触发后台重复分析
        self.dup_only = tk.BooleanVar(value=False)
        self.multi_only = tk.BooleanVar(value=False)
        self.single_only = tk.BooleanVar(value=False)
        self.applied_only = tk.BooleanVar(value=False)
        self.unapplied_only = tk.BooleanVar(value=False)
        ttk.Label(flt, text=_("车厂:")).pack(side=tk.LEFT, padx=(14, 2))
        self.brand_var = tk.StringVar(value=_("全部车厂"))
        self.brand_combo = ttk.Combobox(flt, textvariable=self.brand_var,
                                        state="readonly", width=16,
                                        values=[_("全部车厂")])
        self.brand_combo.pack(side=tk.LEFT)
        self.brand_combo.bind("<<ComboboxSelected>>", lambda _e: self.rebuild_grid())
        ttk.Label(flt, text=_("排序:")).pack(side=tk.LEFT, padx=(14, 2))
        self.sort_var = tk.StringVar(value=SORT_OPTIONS[0])
        scb = ttk.Combobox(flt, textvariable=self.sort_var, state="readonly",
                           width=16, values=SORT_OPTIONS)
        scb.pack(side=tk.LEFT)
        scb.bind("<<ComboboxSelected>>", lambda _e: self.rebuild_grid())
        # 次选条件(平级打破时生效); 默认「无」= 不启用。与主选共用一套模式,
        # 经稳定排序实现字典序——分组展示下组内/组间同样遵循两级链
        ttk.Label(flt, text=_("次选:")).pack(side=tk.LEFT, padx=(6, 2))
        self.sub_sort_var = tk.StringVar(value=SUB_SORT_NONE)
        sscb = ttk.Combobox(flt, textvariable=self.sub_sort_var, state="readonly",
                            width=16, values=[SUB_SORT_NONE] + SORT_OPTIONS)
        sscb.pack(side=tk.LEFT)
        sscb.bind("<<ComboboxSelected>>", lambda _e: self.rebuild_grid())
        # 分组显示维度: 卡片墙按所选字段分桶, 每桶前插通栏标题行。
        # 重复类筛选激活时以重复组展示优先, 本下拉不生效
        ttk.Label(flt, text=_("分组:")).pack(side=tk.LEFT, padx=(6, 2))
        self.group_var = tk.StringVar(value=GROUP_OPTIONS[0])
        gcb = ttk.Combobox(flt, textvariable=self.group_var, state="readonly",
                           width=8, values=GROUP_OPTIONS)
        gcb.pack(side=tk.LEFT)
        gcb.bind("<<ComboboxSelected>>", self._on_group_select)

        # 重复检测参数: 独立按钮(原来在涂装筛选菜单里)
        ttk.Button(flt, text=_("重复检测参数"),
                   command=self.open_dup_params).pack(side=tk.RIGHT, padx=(0, 8))
        # 右侧: 涂装筛选(全部独立开关, 多规则 OR 组合; 经典 tk.Menubutton 凸起边框)
        self.filter_mb = tk.Menubutton(flt, text=_("涂装筛选"), relief=tk.RAISED, bd=1,
                                       bg="#e1e1e1", activebackground="#ececec",
                                       padx=10, pady=2)
        fmenu = tk.Menu(self.filter_mb, tearoff=0)
        fmenu.add_checkbutton(label=_("仅显示重复涂装(不限场景)"), variable=self.dup_only,
                              command=self._on_dup_switch)
        fmenu.add_separator()
        # 车型维度(可叠加, 二者互斥)
        fmenu.add_checkbutton(label=_("仅显示多涂装车型(≥2 种)"), variable=self.multi_only,
                              command=lambda: self._select_dup_filter("multi"))
        fmenu.add_checkbutton(label=_("仅显示单涂装车型(仅 1 种)"), variable=self.single_only,
                              command=lambda: self._select_dup_filter("single"))
        fmenu.add_separator()
        fmenu.add_checkbutton(label=_("仅显示已喷涂(在车上)"), variable=self.applied_only,
                              command=lambda: self._select_applied_filter("applied"))
        fmenu.add_checkbutton(label=_("仅显示未喷涂(不在车上)"), variable=self.unapplied_only,
                              command=lambda: self._select_applied_filter("unapplied"))
        self.filter_mb.configure(menu=fmenu)
        self.filter_mb.pack(side=tk.RIGHT)

        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        # 右侧: 固定宽度详情面板(先 pack, 保证宽度不被网格挤压)
        right = tk.Frame(main, width=430)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        right.pack_propagate(False)
        self.thumb_label = ttk.Label(right, text=_("(无预览)"), anchor=tk.CENTER,
                                     relief=tk.SUNKEN)
        self.thumb_label.pack(fill=tk.X, pady=(0, 6))
        self.thumb_label.bind("<Double-1>", lambda _e: self.show_big_preview())
        # 详情: 只读文本框, 支持鼠标选择 / 原生 Ctrl+C / 右键菜单
        self.info_text = tk.Text(right, height=12, wrap=tk.WORD, relief=tk.FLAT,
                                 bd=0, bg="#f0f0f0", font=(FONT_DATA, 9),
                                 cursor="arrow")
        self.info_text.pack(fill=tk.X, pady=(0, 6))
        self.info_text.bind("<Key>", self._info_key)
        self.info_text.bind("<<Cut>>", lambda _e: "break")
        self.info_text.bind("<<Paste>>", lambda _e: "break")
        self.info_text.bind("<Button-3>", self._info_menu)
        self._set_info(_("未选择条目"))
        btns = tk.Frame(right)
        btns.pack(fill=tk.X)
        for text, cmd in ((_("查看大图"), self.show_big_preview),
                          (_("自动定位到游戏"), self.auto_locate),
                          (_("所在文件夹"), self.open_item_folder)):
            ttk.Button(btns, text=text, command=cmd).pack(fill=tk.X, pady=1)

        # 右下: 版本/项目地址/声明
        footer = tk.Frame(right)
        footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 2))
        ttk.Separator(footer).pack(fill=tk.X, pady=(0, 6))
        tk.Label(footer, text=_("FH6 涂装查看器 v{v}").format(v=APP_VERSION),
                 font=(FONT_UI, 9, "bold"), anchor=tk.W).pack(fill=tk.X)
        link = tk.Label(footer, text=_("更新链接: {url}").format(url=RELEASES_URL),
                        fg="#0066cc", cursor="hand2", anchor=tk.W,
                        font=(FONT_DATA, 8, "underline"))
        link.pack(fill=tk.X)
        link.bind("<Button-1>", lambda _e: webbrowser.open(RELEASES_URL))
        tk.Label(footer, anchor=tk.NW, justify=tk.LEFT, wraplength=400,
                 fg="#777777", font=(FONT_UI, 8),
                 text=_("本工具与 Microsoft、Xbox、Playground Games、Turn 10 无关，Forza 相关商标归其各自所有者。\n"
                        "工具仅读取本地内容，不提供任何修改、解锁或联机功能。\n"
                        "使用本工具产生的任何后果由使用者自行承担。\n")).pack(
                     fill=tk.X, pady=(4, 0))

        # 左侧: 可滚动的平铺画布, 占满剩余空间
        grid_wrap = ttk.Frame(main)
        grid_wrap.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # 卡片墙用「单 Canvas 虚拟绘制」: 不建 Tk 子控件, 直接 create_image/text/rectangle
        # 画可视区卡片(v1.4.0 起)。旧方案(内嵌子控件+grid 窗口化重铺)在 Windows 上受
        # 大坐标(~8200px)渲染损坏限制, 且滚动重铺有中间态会闪屏; 虚拟绘制两者皆无。
        self.canvas = tk.Canvas(grid_wrap, highlightthickness=0, bg="#fafafa")
        vsb = ttk.Scrollbar(grid_wrap, orient=tk.VERTICAL, command=self.canvas.yview)
        self.vsb = vsb
        self.canvas.configure(yscrollcommand=self._on_yview)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # 卡片交互全部在画布层处理: 命中检测经 find_closest + tag(card:<base>) 解析条目
        self.canvas.bind("<Button-1>", self._on_card_click)
        self.canvas.bind("<Double-Button-1>", self._on_card_dbl)
        self.canvas.bind("<Button-3>", self._on_card_rclick)
        # 全局滚轮: 指针在网格区域内才滚动(不能靠 Enter/Leave)
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.status_var = tk.StringVar(value=_("就绪"))
        ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W, padding=(6, 2)).pack(fill=tk.X, side=tk.BOTTOM)
        self.bind("<Control-c>", self._on_ctrl_c)
        self.bind("<Escape>", lambda _e: self._cancel_locate())

    # ------------------------------------------------------------ 平铺布局(单 Canvas 虚拟绘制)

    def _on_wheel(self, e):
        """指针在左侧网格区域时滚动画布。
        winfo_containing 可能 KeyError(如悬在 ttk Combobox 下拉 popdown 上——
        该窗口只有 Tcl 对象没有 Python 控件), 此时直接忽略即可。"""
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except KeyError:
            return
        while w is not None:
            if w is self.canvas:
                self.canvas.yview_scroll(int(-e.delta / 120), "units")
                return
            w = getattr(w, "master", None)

    def _on_yview(self, first, last):
        """滚动位置变化: 更新滚动条并即时重绘可视区。
        虚拟绘制下重绘只是十几个绘图元素的 delete+create(无子控件增删、
        无中间态), 不需要旧方案的 120ms 防抖——这正是滚轮闪屏的根治点。"""
        self.vsb.set(first, last)
        self._redraw()

    def _on_canvas_configure(self, e):
        cols = max(1, e.width // COL_W)
        if cols != self._cols:
            self._cols = cols
            if self._relayout_job:
                self.after_cancel(self._relayout_job)
            self._relayout_job = self.after(120, self._relayout)
        else:
            self._redraw()          # 高度变化改变可视范围(首次映射时也靠这里补画)

    def _relayout(self):
        """列数变化后按缓存的筛选结果重算行布局(不重新筛选/排序, 代次不动)。"""
        self._relayout_job = None
        self._layout_rows(self._filtered_items)
        self._redraw(force=True)

    # ---- 行模型 -------------------------------------------------------------

    def _dup_grouping_active(self) -> bool:
        """重复分组展示激活条件: 打开「仅显示重复涂装」且重复分析已有结果;
        否则一律平铺(v1.4.0 改动2)。"""
        return bool(self._dup_group) and self.dup_only.get()

    def _order_chain(self) -> list:
        """当前生效的排序条件链 [(key 函数, 是否反向), ...]:
        「排序」下拉为主选, 「次选」下拉为次级条件(选中「无」=不启用),
        末级恒为文件名兜底(base 升序, 保证同键时顺序确定且与旧版
        「游戏内顺序」完全一致)。平铺与重复分组共用同一套链——分组时组内
        成员与组间顺序(取各组首项的各级键)都按整条链做字典序。"""
        modes = [self.sort_var.get()]
        if self.sub_sort_var.get() != SUB_SORT_NONE:    # 「无」= 不启用次选
            modes.append(self.sub_sort_var.get())
        chain = [self._mode_key(m) for m in modes]
        chain.append((lambda i: i.base, False))     # 末级兜底(确定性收尾)
        return chain

    def _mode_key(self, mode: str):
        """单个排序模式 -> (key 函数, 是否反向); 主选/次选下拉共用。"""
        from datetime import datetime as _dt, timezone as _tz
        if mode == _("下载日期(新→旧)"):
            tmin = _dt.min.replace(tzinfo=_tz.utc)
            return (lambda i: i.ts or tmin), True
        if mode == _("下载日期(旧→新)"):
            tmax = _dt.max.replace(tzinfo=_tz.utc)
            return (lambda i: i.ts or tmax), False
        if mode == _("名称"):
            return (lambda i: (i.name or "").lower()), False
        if mode == _("车型"):
            def _car_key(i: SaveItem):
                n = self.car_table.name("fh6", i.car_id)
                if not n:
                    return (1, f"{i.car_id:06d}")        # 未识别的排最后, 按 ID 排
                n = re.sub(r"^\d{4}\s+", "", n)          # 剥掉年份前缀, 按品牌车型排
                return (0, n.lower())
            return _car_key, False
        if mode == _("作者"):
            return (lambda i: (i.creator or "").lower()), False
        if mode == _("车厂"):
            def _brand_key(i: SaveItem):
                # 头段: 有车厂的在前按品牌字母序, 空车厂归到最后一段
                b = self._brand_of(i)
                head = (0, b.lower()) if b else (1, "")
                # 尾段: 同车厂内沿用「车型」模式的品牌车型序
                n = self.car_table.name("fh6", i.car_id)
                if not n:
                    sub = (1, f"{i.car_id:06d}")
                else:
                    sub = (0, re.sub(r"^\d{4}\s+", "", n).lower())
                return (head, sub)
            return _brand_key, False
        # 游戏内顺序: 与游戏内「我的涂装」排列一致, 车型 ID 升序。
        # 注意只放 car_id——绝对平级打破交给链条末级的 base 兜底, 否则会
        # 顶掉用户选的次选条件(次选在其下永远无法生效)
        return (lambda i: (i.car_id,)), False

    @staticmethod
    def _chain_sort(lst, chain: list):
        """按条件链做字典序多级排序: 每级方向独立, 从最低级到最高级
        逐级稳定排序(Timsort 稳定, 高级在前即为主导)。"""
        for keyf, rev in reversed(chain):
            lst.sort(key=keyf, reverse=rev)

    def _sorted(self, items: list[SaveItem]) -> list[SaveItem]:
        out = list(items)
        self._chain_sort(out, self._order_chain())
        return out

    def _layout_rows(self, items: list[SaveItem]):
        """把筛选后的条目编成行模型 _rows 并算出 y 前缀和 _row_y。
        平铺: 每行 _cols 张卡; 重复分组激活时按重复组聚拢(组标题「第 N 组 ·…」);
        「分组显示」下拉选了车厂/作者时按该字段分桶(标题「组名 · 数量」),
        空值归入「其他」恒置底。三种形态互斥, 重复分组优先。"""
        if self._dup_grouping_active():
            keyf_chain = self._order_chain()
            buckets: dict[int, list[SaveItem]] = {}
            for it in items:
                gid = self._dup_group.get(it.base, 0)
                buckets.setdefault(gid, []).append(it)
            reps = {}
            for gid, members in buckets.items():
                self._chain_sort(members, keyf_chain)     # 组内: 主选+次选整条链
                reps[gid] = tuple(kf(members[0]) for kf, _ in keyf_chain) \
                    if members else ()
            # 组间: 以各组首项(链排序后)的各级键做字典序, 每级方向同主选/次选
            gids = list(buckets)
            for idx in range(len(keyf_chain) - 1, -1, -1):
                gids.sort(key=lambda g, i=idx: reps[g][i],
                          reverse=keyf_chain[idx][1])
            rows = []
            for gid in gids:
                # 单条件引擎后组标签恒为「重复」, 标题不再拼接规则名
                rows.append(("hdr", gid, _("第 {gid} 组 · {n} 个").format(
                    gid=gid, n=len(buckets[gid]))))
                rows += self._card_rows([it.base for it in buckets[gid]])
            return self._finish_layout(rows)
        if self.group_var.get() != GROUP_OPTIONS[0]:      # 车厂/作者/车型维度
            def get_group(it):
                if self.group_var.get() == _("车厂"):
                    return self._brand_of(it)
                if self.group_var.get() == _("车型"):
                    return self.car_display(it)   # 未识别车型显示 "ID xxx" 自成一组
                return it.creator or ""
            return self._finish_layout(
                self._categorical_rows(items, get_group))
        ordered = self._sorted(items)
        return self._finish_layout(
            self._card_rows([it.base for it in ordered]))

    def _categorical_rows(self, items: list[SaveItem], get_group) -> list[tuple]:
        """按取组函数把条目分桶并生成带通栏标题的行序列。
        组间顺序与排序联动: 先按条件链整体排序, 再顺序切桶——各组自然以其
        链序最靠前的成员领头; 组名取不到值(空串)的成员归入「其他」恒置底。"""
        ordered = list(items)
        self._chain_sort(ordered, self._order_chain())
        seq: list[tuple[str, list[SaveItem]]] = []   # [(组名, 成员), ...] 保持链序
        index: dict[str, int] = {}
        other: list[SaveItem] = []
        for it in ordered:
            g = get_group(it).strip()
            if not g:
                other.append(it)                      # 无车厂/无作者 → 其他置底
                continue
            if g not in index:
                index[g] = len(seq)
                seq.append((g, []))
            seq[index[g]][1].append(it)
        rows = []
        for name, members in seq:
            rows.append(("hdr", name, _("{name} · {n} 个").format(name=name,
                                                                 n=len(members))))
            rows += self._card_rows([it.base for it in members])
        if other:                                     # 「其他」强制最后
            rows.append(("hdr", _("其他"), _("其他 · {n} 个").format(n=len(other))))
            rows += self._card_rows([it.base for it in other])
        return rows

    def _finish_layout(self, rows: list[tuple]):
        """行模型收尾: 计算 y 前缀和与内容总高, 写回实例字段并返回行列表。"""
        self._rows = rows
        y, ys = 0, [0]
        for r in rows:
            y += HEADER_H if r[0] == "hdr" else ROW_H
            ys.append(y)
        self._row_y = ys
        self._content_h = y
        return rows

    def _card_rows(self, bases: list[str]) -> list[tuple]:
        cols = max(1, self._cols)
        return [("cards", bases[i:i + cols]) for i in range(0, len(bases), cols)]

    # ---- 可视区重绘 ----------------------------------------------------------

    def _visible_rows(self) -> tuple[int, int]:
        """当前可视行范围 (r0, r1)(上下各留余量); 无内容返回 (0, 0)。"""
        n = len(self._rows)
        if not n:
            return 0, 0
        view_h = max(1, self.canvas.winfo_height())
        ytop = self.canvas.yview()[0] * self._content_h
        r0 = max(0, bisect_right(self._row_y, ytop) - 1)
        r1 = min(n, bisect_right(self._row_y, ytop + view_h))
        margin = 2                       # 余量行, 减少小幅滚动触发的重绘次数
        return max(0, r0 - margin), min(n, r1 + margin)

    def _redraw(self, force: bool = False):
        """重绘可视区的行(canvas 绘图元素)。delete+create 在同一回调内完成,
        Tk 空闲时统一上屏, 无中间态; 行窗未变且非强制时跳过。"""
        c = self.canvas
        win = self._visible_rows()
        if not force and win == self._redraw_win:
            return
        self._redraw_win = win
        self._vis_cards.clear()
        c.delete("all")
        if not self._rows:
            c.configure(scrollregion=(0, 0, 0, 0))
            return
        width = self._cols * COL_W
        for ri in range(win[0], win[1]):
            row = self._rows[ri]
            y = self._row_y[ri]
            if row[0] == "hdr":                      # 通栏组标题色带
                c.create_rectangle(0, y, width, y + HEADER_H,
                                   fill="#e8eef7", outline="")
                c.create_text(10, y + HEADER_H // 2, anchor=tk.W, text=row[2],
                              font=(FONT_UI, 9, "bold"),
                              fill="#34506b")
            else:
                for ci, base in enumerate(row[1]):
                    self._draw_card(base, ci, ri)
        c.configure(scrollregion=(0, 0, width, self._content_h))

    # 可配色与 Card 时代的 _refresh_colors 一致: 选中蓝底蓝框优先于其它标记
    _SEL_COLORS = ("#005a9e", "#0078D7", "#ffffff", "#eaf4ff", "#cce6ff")
    _NORM_COLORS = ("#cccccc", "#ffffff", "#000000", "#333333", "#888888")

    @staticmethod
    def _palette(sel: bool):
        return App._SEL_COLORS if sel else App._NORM_COLORS

    def _draw_card(self, base: str, col: int, ri: int):
        """画一张卡: 底板矩形(兼描边)+内面+三行文字+缩略图区, item 记入 _vis_cards。"""
        it = self.item_map.get(base)
        if it is None:
            return
        c = self.canvas
        tag = f"card:{base}"
        x = col * COL_W + 4
        y = self._row_y[ri] + 4
        border, face, name_fg, car_fg, sub_fg = self._palette(base == self._selected)

        def new(kind, *args, **kw):
            return getattr(c, f"create_{kind}")(*args, tags=(tag,), **kw)

        ids = {"x": x, "y": y}
        tx, ty = x + 5, y + 5
        ids["tx"], ids["ty"] = tx, ty
        ids["border"] = new("rectangle", x, y, x + CARD_W, y + CARD_H,
                            fill=border, outline="")
        ids["face"] = new("rectangle", x + 2, y + 2, x + CARD_W - 2, y + CARD_H - 2,
                          fill=face, outline="")
        # 缩略图底色(图片按比例缩放后的留白/未加载时的衬底, 同旧 Label 的灰底);
        # 缩略图与角标必须在文字之前绘制: 图片按比例居中不会盖到下方文字行
        new("rectangle", tx, ty, tx + THUMB_W, ty + THUMB_H,
            fill="#f0f0f0", outline="")
        self._paint_thumb(base, ids)
        # 注意 create_text 一律 anchor=NW(顶端对齐): 旧 Label 的 place(y=..) 是
        # 控件顶边定位, 若用 W(C=W 且垂直居中)整行文字会上移约半个行高压到图片
        ids["name"] = new("text", x + 7, y + THUMB_H + 6, anchor=tk.NW,
                          text=ellipsize(it.name or _("(未解析)"), 24),
                          font=(FONT_DATA, 9, "bold"), fill=name_fg)
        # 车型名允许换行(最多两行), 尽量完整显示(width=THUMB_W-4 即 wraplength)
        ids["car"] = new("text", x + 7, y + THUMB_H + 26, anchor=tk.NW,
                         justify=tk.LEFT, text=self.car_display(it),
                         width=THUMB_W - 4,
                         font=(FONT_DATA, 8), fill=car_fg)
        # 第三行: 车型已识别(或无车型 ID) → 显示作者; 未识别 → 显示 ID 和日期(便于排查)
        known = bool(self.car_table.name("fh6", it.car_id))
        date = it.ts.strftime("%Y-%m-%d") if it.ts else ""
        third = ((it.creator or "?") if known or not it.car_id
                 else _("ID {id}  {date}").format(id=it.car_id, date=date))
        ids["sub"] = new("text", x + 7, y + THUMB_H + 58, anchor=tk.NW,
                         text=ellipsize(third, 30),
                         font=(FONT_DATA, 8), fill=sub_fg)
        self._vis_cards[base] = ids

    def _paint_thumb(self, base: str, ids: dict):
        """绘制某可视卡的缩略图元素与两个角标。
        缩略图: 命中缓存贴 PhotoImage(居中); 条目无缩略图文件或解码连败封顶
        显示「无预览图」; 其余显示「加载中…」。
        角标(v1.4.0 起)均画在缩略图框角上、不再烙进图片(图片按比例居中后
        烙印位置会随留白漂移): 「已喷在车上」喷漆罐素材贴左上角(全卡共用一份
        PhotoImage, 需要 PIL); 位置「N行M列」文字牌贴右上角(固定边距/浅底黑字,
        观感同旧烙印版)。"""
        c = self.canvas
        for k in ("img", "ph", "badge", "posr", "post"):
            if ids.get(k):
                c.delete(ids[k])
        ids["img"] = ids["ph"] = ids["badge"] = None
        ids["posr"] = ids["post"] = None
        it = self.item_map.get(base)
        cx, cy = ids["tx"] + THUMB_W // 2, ids["ty"] + THUMB_H // 2
        tag = f"card:{base}"
        photo = self._img_cache.get(base)
        if photo:
            ids["img"] = c.create_image(cx, cy, anchor=tk.CENTER,
                                        image=photo, tags=(tag,))
        elif it is None or it.thumb_big is None or self._thumb_fails.get(base, 0) >= 6:
            ids["ph"] = c.create_text(cx, cy, text=_("(无预览图)"), fill="#888888",
                                      font=(FONT_UI, 8), tags=(tag,))
        else:
            ids["ph"] = c.create_text(cx, cy, text=_("加载中…"), fill="#888888",
                                      font=(FONT_UI, 8), tags=(tag,))
        if (HAS_PIL and self._applied and base in self._applied):
            if self._applied_photo is None:
                self._applied_photo = ImageTk.PhotoImage(_applied_badge_sprite())
            ids["badge"] = c.create_image(ids["x"], ids["y"], anchor=tk.NW,
                                          image=self._applied_photo, tags=(tag,))
        # 位置角标: 右上角浅底黑字(尺寸随文本自适应, 边距同旧烙印 m=3/pad=4)
        pos = self._pos_map.get(base, "")
        if pos:
            fnt = self._pos_badge_font
            if fnt is None:
                fnt = tkfont.Font(family=FONT_DATA, size=-13)
                self._pos_badge_font = fnt
            tw, th = fnt.measure(pos), fnt.metrics("linespace")
            m, pad = 3, 4
            x1, y0 = ids["tx"] + THUMB_W - m, ids["ty"] + m
            ids["posr"] = c.create_rectangle(x1 - tw - pad * 2, y0, x1, y0 + th,
                                             fill="#f0f0f0", outline="",
                                             tags=(tag,))
            ids["post"] = c.create_text(x1 - pad - tw // 2, y0 + th // 2,
                                        anchor=tk.CENTER, text=pos,
                                        font=fnt, fill="#000000", tags=(tag,))

    def _recolor_card(self, base: str | None):
        """就地切换某可视卡的选中配色(不重绘整屏); 不在可视区则是空操作。"""
        ids = self._vis_cards.get(base or "")
        if not ids:
            return
        border, face, name_fg, car_fg, sub_fg = self._palette(base == self._selected)
        c = self.canvas
        c.itemconfigure(ids["border"], fill=border)
        c.itemconfigure(ids["face"], fill=face)
        c.itemconfigure(ids["name"], fill=name_fg)
        c.itemconfigure(ids["car"], fill=car_fg)
        c.itemconfigure(ids["sub"], fill=sub_fg)

    def _set_card_image(self, base: str, _img):
        """缩略图解码结果回贴入口(替代旧 Card.set_image): 图已写入缓存,
        卡在可视区就按缓存状态重画其图片元素, 不在可视区无需动作(重绘自然生效)。"""
        ids = self._vis_cards.get(base)
        if ids:
            self._paint_thumb(base, ids)

    # ---- 画布上的卡片交互(原 Card 绑定迁移) ------------------------------------

    def _hit_base(self, e) -> str | None:
        """把画布事件解析成卡片 base(tag=card:<base>); 点在空白处返回 None。"""
        c = self.canvas
        try:
            cx, cy = c.canvasx(e.x), c.canvasy(e.y)
            cid = c.find_closest(cx, cy)[0]
        except (tk.TclError, IndexError):
            return None
        bbox = c.bbox(cid)
        if not bbox:
            return None
        x0, y0, x1, y1 = bbox
        if not (x0 - 4 <= cx <= x1 + 4 and y0 - 4 <= cy <= y1 + 4):
            return None                  # 最近元素也在数像素外的空白处
        for t in c.gettags(cid):
            if isinstance(t, str) and t.startswith("card:"):
                return t[len("card:"):]
        return None

    def _on_card_click(self, e):
        base = self._hit_base(e)
        if base:
            self.select(base)

    def _on_card_dbl(self, e):
        """双击 = 自动定位到游戏内该涂装(右键菜单仍可查看缩略图)。"""
        base = self._hit_base(e)
        if base:
            self.select(base)
            self.auto_locate()

    def _set_quick_filter(self, qf: tuple):
        """右键快筛: 只显示该车型/该作者(会话级; 切存档或「清除快筛」复原)。"""
        self.quick_filter = qf
        self.rebuild_grid()
        what, val = qf
        if what == "car":
            rep = next((x for x in self.items if x.car_id == val), None)
            desc = (_("车型 {name}").format(name=self.car_display(rep)) if rep
                    else _("车型 ID {id}").format(id=val))
        else:
            desc = _("作者 {name}").format(name=val)
        self.status_var.set(_("快筛: 只显示{desc} (右键菜单可清除)").format(desc=desc))

    def _clear_quick_filter(self):
        self.quick_filter = None
        self.rebuild_grid()
        self.status_var.set(_("已清除快筛"))

    def _on_card_rclick(self, e):
        """右键菜单: 查看缩略图/定位到涂装 + 复制位置/名称/车型/作者。"""
        base = self._hit_base(e)
        if not base or base not in self.item_map:
            return
        self.select(base)
        it = self.item_map[base]
        pos = self._pos_map.get(base, "")
        car = self.car_table.name("fh6", it.car_id)
        menu = tk.Menu(self.canvas, tearoff=0)
        if self.quick_filter:
            menu.add_command(label=_("清除快筛"), command=self._clear_quick_filter)
        menu.add_command(label=_("只显示该车型"),
                         command=lambda: self._set_quick_filter(("car", it.car_id)))
        menu.add_command(label=_("只显示该作者"),
                         state=tk.NORMAL if it.creator else tk.DISABLED,
                         command=lambda: self._set_quick_filter(
                             ("creator", it.creator or "")))
        menu.add_separator()
        menu.add_command(label=_("查看缩略图"),
                         state=tk.NORMAL if it.thumb_big else tk.DISABLED,
                         command=self.show_big_preview)
        menu.add_command(label=_("定位到涂装位置"),
                         state=tk.NORMAL if pos else tk.DISABLED,
                         command=self.auto_locate)
        menu.add_separator()
        menu.add_command(label=_("复制游戏内位置 ({pos})").format(pos=pos) if pos
                         else _("复制游戏内位置"),
                         state=tk.NORMAL if pos else tk.DISABLED,
                         command=lambda: self.copy_text(pos))
        menu.add_command(label=_("复制名称"),
                         state=tk.NORMAL if it.name else tk.DISABLED,
                         command=lambda: self.copy_text(it.name))
        menu.add_command(label=_("复制车型"),
                         state=tk.NORMAL if car else tk.DISABLED,
                         command=lambda: self.copy_text(car))
        menu.add_command(label=_("复制作者"),
                         state=tk.NORMAL if it.creator else tk.DISABLED,
                         command=lambda: self.copy_text(it.creator))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def rebuild_grid(self):
        """刷新卡片墙(筛选/排序/数据变化后调用): 重算行布局并重绘可视区。
        v1.4.0 起为「单 Canvas 虚拟绘制」——不再创建/复用 Tk 子控件, 卡片只是
        可视区的绘图元素; 布局 = 行模型(_layout_rows), 重绘零成本, 选中状态
        与缩略图缓存独立于重绘存活(与旧增量卡片池语义一致)。切存档清空画布。"""
        self._thumb_gen += 1                    # 作废旧解码结果(不注销缓存, 只防在途结果串档)
        self._thumb_pending = []
        items = self._filtered()
        self._filtered_items = items            # 变列数 _relayout 复用, 不再走一遍筛选
        self._layout_rows(items)
        self._shown = [b for r in self._rows if r[0] == "cards" for b in r[1]]
        # 待解码清单: 无缓存即入队(「已喷涂」角标已改为画在卡片上,
        # 不再烙进缩略图图片, 喷涂状态变化不触发缩略图重解码)
        for it in items:
            if it.base not in self._img_cache:
                self._thumb_pending.append(it.base)
        self._redraw(force=True)
        self.after(30, self._load_thumbs_batch)
        parts = []
        if self.dup_only.get():
            parts.append(_("仅重复"))
        if self.multi_only.get():
            parts.append(_("多涂装车型"))
        if self.single_only.get():
            parts.append(_("单涂装车型"))
        if self.applied_only.get():
            parts.append(_("仅已喷涂"))
        if self.unapplied_only.get():
            parts.append(_("仅未喷涂"))
        if self.quick_filter:
            parts.append(_("快筛"))
        self.filter_mb.configure(
            text=_("涂装筛选: {p}").format(p="+".join(parts)) if parts else _("涂装筛选"))
        known = self.car_table.known_count("fh6")
        dup_groups = len({g for g in self._dup_group.values() if g})
        dup_files = sum(1 for g in self._dup_group.values() if g)
        segs = [_("共 {n} 个条目, 显示 {m} 个").format(n=len(self.items),
                                                      m=len(self._shown))]
        if self._applied is not None:
            segs.append(_("已上车 {n}").format(n=len(self._applied)))
        if dup_groups:
            segs.append(_("重复 {g} 组({f} 个)").format(g=dup_groups, f=dup_files))
        segs.append(_("已识别车型 {n} 个").format(n=known))
        segs.append(str(self.current["dir"]) if self.current else "")
        self.status_var.set("  |  ".join(segs))

    def _load_thumbs_batch(self, n: int = 8):
        """加载缩略图: 有 PIL 时解码+合成放线程池, 主线程只做 PhotoImage 贴图;
        无 PIL 退回主线程分批(tk.PhotoImage)。"""
        if self._thumb_pool is not None:
            self._submit_thumb_jobs()
            return
        batch, self._thumb_pending = self._thumb_pending[:n], self._thumb_pending[n:]
        retry = []
        for base in batch:
            it = self.item_map.get(base)
            if not it:
                continue
            img = self._load_thumb(it.thumb_big, THUMB_W, THUMB_H)
            if img:
                # 解码失败(如游戏正在写入该文件)不缓存, 否则 None 进缓存
                # 会让卡片永远卡在「加载中…」
                self._img_cache[base] = img
                self._thumb_fails.pop(base, None)
                self._set_card_image(base, img)
            elif not self._thumb_transient_fail(base, it.thumb_big, retry):
                self._set_card_image(base, None)
        if self._thumb_pending:
            self.after(10, self._load_thumbs_batch)
        self._schedule_thumb_retry(retry)

    def _thumb_transient_fail(self, base: str, path, retry: list) -> bool:
        """解码失败但条目确有缩略图文件 → 视为瞬态失败(游戏/云同步正在写存档,
        读到锁定或半截文件): 保留卡上旧图不清空, 计数交给退避重试(同一 base
        连续 6 次失败才放弃, 回退显示「无预览图」)。"""
        if path is None:
            return False
        fails = self._thumb_fails.get(base, 0)
        if fails >= 6:
            return False
        self._thumb_fails[base] = fails + 1
        retry.append(base)
        return True

    def _schedule_thumb_retry(self, retry: list):
        """瞬态失败的条目退避重排(第 n 次失败延迟 n 秒); 重试由退避定时器触发,
        不进 10ms/30ms 的快通道, 免得对正被写入的文件空转猛刷。"""
        if not retry:
            return
        self._thumb_pending.extend(retry)
        self.after(1000 * max(self._thumb_fails.get(b, 1) for b in retry),
                   self._load_thumbs_batch)

    def _submit_thumb_jobs(self):
        """待解码条目全部提交线程池; 结果经队列回主线程 drain。"""
        pool = self._thumb_pool
        if pool is None:                # 调用方已保证有 PIL 才走到这, 防御一下
            return
        gen = self._thumb_gen
        pending, self._thumb_pending = self._thumb_pending, []
        for base in pending:
            it = self.item_map.get(base)
            if not it:
                continue
            self._thumb_inflight += 1
            pool.submit(self._decode_thumb_job, gen, base, it.thumb_big)
        if self._thumb_inflight:
            self.after(30, self._drain_thumb_queue)

    def _decode_thumb_job(self, gen: int, base: str, path: Path | None):
        """工作线程: 解码+缩放(PIL 解码释放 GIL), 结果入队等主线程贴图。
        卡片缩略图不再烙任何角标(位置/喷涂角标均改为画在卡片层)。"""
        img = self._compose_thumb(path, THUMB_W, THUMB_H) \
            if path and path.exists() else None
        self._thumb_queue.put((gen, base, img))

    def _drain_thumb_queue(self):
        """主线程: 取回解码好的图, 转 PhotoImage 贴卡并写缓存(每轮最多 16 张保持响应)。"""
        retry = []
        for _ in range(16):
            try:
                gen, base, img = self._thumb_queue.get_nowait()
            except queue.Empty:
                break
            self._thumb_inflight -= 1
            if gen != self._thumb_gen:
                continue                        # 期间又 rebuild 过, 过期结果丢弃
            it = self.item_map.get(base)
            if not it:
                continue
            photo = ImageTk.PhotoImage(img) if img is not None else None
            if photo:
                # 解码失败不缓存(同主线程路径的容错)
                self._img_cache[base] = photo
                self._thumb_fails.pop(base, None)
                self._set_card_image(base, photo)
            elif not self._thumb_transient_fail(base, it.thumb_big, retry):
                self._set_card_image(base, None)
        self._schedule_thumb_retry(retry)
        if self._thumb_inflight > 0 or not self._thumb_queue.empty():
            self.after(30, self._drain_thumb_queue)

    # ------------------------------------------------------------ 存档加载

    def rescan_saves(self):
        self.saves = [s for s in fh6save.find_saves() if s["game"] == "fh6"]
        labels = []
        src_names = {"steam": "Steam", "pgs": _("商店版/pgs")}
        for s in self.saves:
            src_key = str(s.get("source") or "?")
            src = src_names.get(src_key, src_key)
            labels.append(_("[{src}]  用户 {user}  |  {dir}").format(
                src=src, user=s["steam_user"], dir=s["dir"]))
        self.save_combo.configure(values=labels)
        if labels:
            self.save_combo.current(0)
            self.load_current()
        else:
            self.current = None
            self.items = []
            self._save_sig = None             # 自动刷新: 无存档, 停掉签名基线
            if self._watch_job is not None:
                self.after_cancel(self._watch_job)
                self._watch_job = None
            self._dup_group, self._car_unique, self._pos_map = {}, {}, {}
            self._dup_rules = {}
            self._dup_feats = None
            self._dup_pending = False
            self._applied = None
            self._applied_pending = False
            self.quick_filter = None
            self._layout, self._total_cols = {}, 0
            self._img_cache.clear()
            self._thumb_fails.clear()
            self.canvas.delete("all")           # 切存档/无存档: 清空虚拟绘制层
            self._vis_cards.clear()
            self._rows, self._row_y = [], []
            self._content_h = 0
            self._redraw_win = None
            self._shown = []
            self._selected = None
            self._set_info(_("未选择条目"))
            self.thumb_label.configure(image="", text=_("(无预览)"))
            self.rebuild_grid()
            self.status_var.set(_("未找到 FH6 存档, 请用「手动选择目录」指定存档文件夹"))

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
        self._pos_map = {b: _("{x}行{y}列").format(x=x, y=y)
                         for b, (x, y) in self._layout.items()}
        self._dup_group, self._car_unique = {}, {}
        self._dup_rules = {}
        self._dup_feats = None
        self._dup_pending = False
        self._applied = None                 # 车上涂装标记与游戏实时档案绑定, 切存档即失效
        self._applied_pending = False
        self.quick_filter = None
        self._img_cache.clear()
        self._thumb_fails.clear()
        self.canvas.delete("all")               # 切存档: 清空虚拟绘制层(rebuild 会重铺)
        self._vis_cards.clear()
        self._rows, self._row_y = [], []
        self._content_h = 0
        self._redraw_win = None
        self._shown = []
        self._selected = None
        self._set_info(_("未选择条目"))
        self.thumb_label.configure(image="", text=_("(无预览)"))
        # 车厂下拉: 只列出当前存档涂装实际涉及的车厂
        brands = sorted({b for it in self.items if it.itype == "Livery"
                         for b in [self._brand_of(it)] if b}, key=str.lower)
        self.brand_combo.configure(values=[_("全部车厂")] + brands)
        if self.brand_var.get() not in (_("全部车厂"), *brands):
            self.brand_var.set(_("全部车厂"))
        # 重复检测按需触发(解码缩略图算哈希很慢): 见 _ensure_dup_analysis
        # 自动刷新: 重建签名基线并取消待触发的增量刷新(本次全量扫描已是最新)
        self._save_sig = fh6save.save_signature(Path(self.current["dir"]))
        if self._watch_job is not None:
            self.after_cancel(self._watch_job)
            self._watch_job = None
        self.rebuild_grid()

    # ------------------------------------------------------------ 自动刷新(改动1)

    def _watch_tick(self):
        """自动刷新轮询: 每 ~3s 对当前存档目录做轻量签名对比, 有变化则(重)启动
        ~2s 防抖(游戏/云同步分批写, 等写稳定), 稳定后 _watch_fire 增量刷新。
        触发层与刷新层解耦: 将来若换事件驱动(ReadDirectoryChangesW), 只需让
        事件回调来(重)启动同一个防抖 job, _refresh_items 保持不变。"""
        if self.auto_refresh.get() and self.current and self._save_sig is not None:
            sig = fh6save.save_signature(Path(self.current["dir"]))
            if sig is not None and sig != self._save_sig:   # None=目录不可读, 跳过本轮
                if self._watch_job is not None:
                    self.after_cancel(self._watch_job)
                self._watch_job = self.after(WATCH_DEBOUNCE_MS, self._watch_fire)
        self.after(WATCH_INTERVAL_MS, self._watch_tick)

    def _watch_fire(self):
        """防抖到时: 重新取签名与基线 diff(刷新前最后状态), 增量刷新并更新基线。"""
        self._watch_job = None
        if not self.current or self._save_sig is None:
            return
        sig = fh6save.save_signature(Path(self.current["dir"]))
        if sig is None or sig == self._save_sig:
            return                            # 变化又消失(如临时文件), 无需动作
        old = self._save_sig
        self._save_sig = sig
        added = [b for b in sig if b not in old]
        removed = [b for b in old if b not in sig]
        changed = [b for b in sig if b in old and sig[b] != old[b]]
        self._refresh_items(added, removed, changed)

    def _refresh_items(self, added: list, removed: list, changed: list):
        """存档变化后的插入式增量刷新: 未变条目复用旧对象(缩略图缓存/选中随
        对象身份存活), 新条目按当前筛选/排序链进入行模型, 滚动位置按锚点
        恢复——视觉上就是新卡插入/旧卡消失, 无整墙闪烁与跳动。"""
        if not self.current:
            return
        anchor = self._scroll_anchor()
        old_bases = {it.base for it in self.items}
        self.items = fh6save.scan_folder_incremental(
            "fh6", self.current["steam_user"], Path(self.current["dir"]),
            self.items, set(added) | set(changed))
        self.item_map = {it.base: it for it in self.items}
        gone = old_bases - set(self.item_map)
        for b in gone:
            self._img_cache.pop(b, None)
            self._thumb_fails.pop(b, None)
        if self._selected in gone:
            self._selected = None
            self._set_info(_("未选择条目"))
            self.thumb_label.configure(image="", text=_("(无预览)"))
        # 派生数据: 游戏内位置(全量重算仅毫秒级)/喷涂集合求交/车厂下拉
        self._total_cols, self._layout = fh6save.game_layout(self.items)
        self._pos_map = {b: _("{x}行{y}列").format(x=x, y=y)
                         for b, (x, y) in self._layout.items()}
        if self._applied is not None:
            self._applied -= gone
        brands = sorted({b for it in self.items if it.itype == "Livery"
                         for b in [self._brand_of(it)] if b}, key=str.lower)
        self.brand_combo.configure(values=[_("全部车厂")] + brands)
        if self.brand_var.get() not in (_("全部车厂"), *brands):
            self.brand_var.set(_("全部车厂"))
        # 重复检测: 分析已跑过 → 增量提取新条目特征后重算; 未跑过 → 保持按需 lazy
        if self._dup_feats is not None:
            for b in gone | set(added) | set(changed):
                self._dup_feats.pop(b, None)
            fresh = [it for it in self.items if it.base not in self._dup_feats]
            if fresh:
                items = self.items

                def _work():
                    feats = fh6save.extract_dup_features(fresh)
                    try:
                        self.after(0, lambda: self._dup_feats_merged(items, feats))
                    except RuntimeError:
                        pass                # 窗口已销毁, 结果不再需要

                threading.Thread(target=_work, daemon=True).start()
            else:
                self._rerun_dup()
        self.rebuild_grid()
        self._restore_anchor(anchor)
        n_new = sum(1 for b in added if b in self.item_map)
        self.status_var.set(_("检测到存档更新: +{new} 新增 / -{gone} 删除, 已刷新").format(
            new=n_new, gone=len(gone)))

    def _dup_feats_merged(self, items: list, feats: dict):
        """自动刷新触发的增量特征提取完成: 合并特征并按当前条件重算分组。"""
        if items is not self.items:
            return                     # 等待期间已切换存档, 丢弃(不碰新存档的状态)
        self._dup_feats.update(feats)
        anchor = self._scroll_anchor()
        self._rerun_dup()
        self._restore_anchor(anchor)

    def _scroll_anchor(self):
        """记录滚动锚点(增量刷新后恢复用): 可视区首张卡的 base + 其行顶到视口顶
        的像素差。hdr 组标题行不作锚(组号/组名随数据变化), 向下找首张卡。"""
        if not self._rows or not self._content_h:
            return None
        ytop = self.canvas.yview()[0] * self._content_h
        ri = min(len(self._rows) - 1, max(0, bisect_right(self._row_y, ytop) - 1))
        for rj in range(ri, len(self._rows)):
            row = self._rows[rj]
            if row[0] == "cards" and row[1]:
                return (row[1][0], ytop - self._row_y[rj])
        return None

    def _restore_anchor(self, anchor):
        """按锚点恢复滚动位置; 锚点卡被删则保持 Tk 原分数(列表顶端附近时自然正确)。"""
        if not anchor or not self._rows or not self._content_h:
            return
        base, dy = anchor
        for ri, row in enumerate(self._rows):
            if row[0] == "cards" and base in row[1]:
                frac = max(0.0, min(1.0, (self._row_y[ri] + dy) / self._content_h))
                self.canvas.yview_moveto(frac)
                return

    def _dup_ready(self, items: list[SaveItem], feats: dict):
        if items is not self.items:
            return                     # 等待期间已切换存档, 丢弃(不碰新存档的状态)
        self._dup_pending = False
        self._dup_feats = feats
        self._rerun_dup()

    def _rerun_dup(self):
        """用已缓存特征按当前判定条件重算重复分组并刷新界面(毫秒级);
        特征未就绪则先触发分析。"""
        if self._dup_feats is None:
            self._ensure_dup_analysis()
            return
        (self._dup_group, self._car_unique,
         self._dup_rules) = fh6save.detect_duplicates(self.items,
                                                      features=self._dup_feats,
                                                      rules=[self._dup_cfg])
        self.rebuild_grid()

    def open_dup_params(self):
        """「重复检测参数」对话框: 直接暴露全部底层比较条件, 无预制模式。
        提供常用模板一键填表; 打开时回填上次应用的值(会话级), 确定后用已缓存
        特征毫秒级重算, 不落盘。"""
        dlg = tk.Toplevel(self)
        dlg.title(_("重复检测参数"))
        dlg.transient(self)
        dlg.grab_set()                          # 模态
        body = ttk.Frame(dlg, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        R3 = {"any": _("任意"), "same": _("相同"), "diff": _("不同")}
        T3 = [_("不参与"), _("相同"), _("不同")]
        ANY3 = [_("任意"), _("相同"), _("不同")]
        IMG3 = [_("不比对"), _("距离≤N"), _("任一方无图")]
        SIM3 = [_("不比对"), _("≥X(找相似名)"), _("<X(找不同名)")]

        def _opt(row, name, combo_vals, hint):
            """一行「条件下拉 + 灰色说明」。"""
            ttk.Label(body, text=name).grid(row=row, column=0, sticky=tk.W, pady=3)
            cb = ttk.Combobox(body, state="readonly", width=9, values=combo_vals)
            cb.grid(row=row, column=1, sticky=tk.W, padx=(4, 4))
            ttk.Label(body, text=hint, foreground="#777777").grid(
                row=row, column=4, sticky=tk.W)
            return cb

        # 模板: 一键填表(引擎无预制规则, 模板只是常用条件的起点)
        ttk.Label(body, text=_("模板:")).grid(row=0, column=0, sticky=tk.W, pady=3)
        cb_templ = ttk.Combobox(body, state="readonly", width=20,
                                values=[_("(自定义)")] + [n for n, _, _ in DUP_TEMPLATES])
        cb_templ.grid(row=0, column=1, columnspan=3, sticky=tk.W, padx=(4, 4))
        tmpl_desc = ttk.Label(body, text="", foreground="#777777")
        tmpl_desc.grid(row=0, column=4, sticky=tk.W)

        cb_car = _opt(1, _("车型:"), ANY3, _("条目文件名里的车型 ID"))
        cb_author = _opt(2, _("作者:"), ANY3,
                         _("涂装作者; Forza 默认名视为匿名, 匿名不参与相同/不同判定"))
        ttk.Label(body, text=_("图片:")).grid(row=3, column=0, sticky=tk.W, pady=3)
        cb_img = ttk.Combobox(body, state="readonly", width=9, values=IMG3)
        cb_img.grid(row=3, column=1, sticky=tk.W, padx=(4, 4))
        ttk.Label(body, text="N").grid(row=3, column=2, sticky=tk.E)
        img_var = tk.StringVar()
        ttk.Spinbox(body, from_=0, to=64, width=5,
                    textvariable=img_var).grid(row=3, column=3, sticky=tk.W,
                                               padx=(2, 10))
        ttk.Label(body, text=_("感知哈希汉明距离 0~64, 越小要求越像;「任一方无图」"
                               "=其中一条没有预览图文件(解码失败不算)"),
                  foreground="#777777").grid(row=3, column=4, sticky=tk.W)
        ttk.Label(body, text=_("名称相似度:")).grid(row=4, column=0, sticky=tk.W, pady=3)
        cb_sim = ttk.Combobox(body, state="readonly", width=9, values=SIM3)
        cb_sim.grid(row=4, column=1, sticky=tk.W, padx=(4, 4))
        ttk.Label(body, text="X").grid(row=4, column=2, sticky=tk.E)
        sim_var = tk.StringVar()
        ttk.Spinbox(body, from_=0.0, to=1.0, increment=0.05, width=5,
                    textvariable=sim_var).grid(row=4, column=3, sticky=tk.W,
                                               padx=(2, 10))
        ttk.Label(body, text=_("difflib 相似度 0~1; 任一方名称为空按 0 处理"),
                  foreground="#777777").grid(row=4, column=4, sticky=tk.W)
        cb_created = _opt(5, _("创建时间:"), T3,
                          _("文件名时间戳(≈作者创作时间); 任一方缺失则条件不成立"))
        cb_down = _opt(6, _("下载时间:"), T3,
                       _("文件 mtime(≈玩家下载落盘时间); 任一方缺失则条件不成立"))

        def _fill_form(r: fh6save.DupRule, tmpl_name: str | None = None):
            """把条件对象回填到表单(打开时回填上次应用的值 / 选模板时填表)。"""
            cb_car.set(R3[r.car])
            cb_author.set(R3[r.author])
            if r.img is None:
                cb_img.set(_("不比对"))
            elif r.img == "missing":
                cb_img.set(_("任一方无图"))
            else:
                cb_img.set(_("距离≤N"))
                img_var.set(str(r.img[1]))
            if r.name is None:
                cb_sim.set(_("不比对"))
            elif r.name[0] == "min":
                cb_sim.set(_("≥X(找相似名)"))
                sim_var.set(f"{r.name[1]:.2f}")
            else:
                cb_sim.set(_("<X(找不同名)"))
                sim_var.set(f"{r.name[1]:.2f}")
            cb_created.set(_("不参与") if r.created is None else
                           (_("相同") if r.created == "same" else _("不同")))
            cb_down.set(_("不参与") if r.downloaded is None else
                        (_("相同") if r.downloaded == "same" else _("不同")))
            if tmpl_name:
                tmpl_desc.configure(
                    text=next(d for n, d, _ in DUP_TEMPLATES if n == tmpl_name))
            else:
                tmpl_desc.configure(text=_("当前应用的条件"))

        def _on_templ(_e=None):
            name = cb_templ.get()
            if name == _("(自定义)"):
                return
            for n, _d, kw in DUP_TEMPLATES:
                if n == name:
                    _fill_form(fh6save.DupRule(**kw), name)
                    return
        cb_templ.bind("<<ComboboxSelected>>", _on_templ)

        # 打开时回填上次应用的值; 与某模板完全一致则选中该模板名
        _fill_form(self._dup_cfg)
        tmpl_name = _("(自定义)")
        for n, _d, kw in DUP_TEMPLATES:
            if fh6save.DupRule(key="重复", **kw) == self._dup_cfg:
                tmpl_name = n
                break
        cb_templ.set(tmpl_name)
        _fill_form(self._dup_cfg, None if tmpl_name == _("(自定义)") else tmpl_name)

        ttk.Label(body, foreground="#777777", wraplength=520, justify=tk.LEFT,
                  text=_("判定方式: 两两配对, 同时满足以上启用条件的两条涂装判为重复;"
                         "并按传递关系合并成组(A~B、B~C ⇒ 三者同组)。"
                         "条件越宽松组越大。改动仅本次运行有效。")).grid(
            row=7, column=0, columnspan=5, sticky=tk.W, pady=(8, 0))

        def _combo3(cb):
            return {_("任意"): "any", _("相同"): "same", _("不同"): "diff"}.get(
                cb.get(), "any")

        def _t3(cb):
            return {_("不参与"): None, _("相同"): "same", _("不同"): "diff"}.get(cb.get())

        def _build_rule() -> fh6save.DupRule:
            """表单 → 条件对象(阈值非法时弹错并抛 ValueError)。"""
            img = None
            name = None
            try:
                if cb_img.get() == _("距离≤N"):
                    d = int(img_var.get())
                    if not 0 <= d <= 64:
                        raise ValueError
                    img = ("dist", d)
                elif cb_img.get() == _("任一方无图"):
                    img = "missing"
                if cb_sim.get() != _("不比对"):
                    s = float(sim_var.get())
                    if not 0.0 <= s <= 1.0:
                        raise ValueError
                    name = (("min", s) if cb_sim.get() == _("≥X(找相似名)")
                            else ("max", s))
            except ValueError:
                messagebox.showerror(_("重复检测参数"),
                                     _("阈值无效: 距离 0~64 / 相似度 0.00~1.00"),
                                     parent=dlg)
                raise
            return fh6save.DupRule(key="重复", car=_combo3(cb_car),
                                   author=_combo3(cb_author), img=img, name=name,
                                   created=_t3(cb_created), downloaded=_t3(cb_down))

        def _save():
            try:
                self._dup_cfg = _build_rule()
            except ValueError:
                return                          # 错误框已在 _build_rule 弹过
            self._rerun_dup()
            if self._dup_feats is not None:     # 特征分析中时不覆盖「分析中」提示
                n_hit = sum(1 for g in self._dup_group.values() if g)
                n_grp = len({g for g in self._dup_group.values() if g})
                self.status_var.set(
                    _("重复检测条件已更新: {hit} 个条目在重复组里, 共 {grp} 组").format(
                        hit=n_hit, grp=n_grp))
            dlg.destroy()

        def _reset():
            _fill_form(fh6save.DEFAULT_DUP_RULE)
            cb_templ.set(_("(自定义)"))
            tmpl_desc.configure(text=_("出厂默认条件"))

        btns = ttk.Frame(body)
        btns.grid(row=8, column=0, columnspan=5, sticky=tk.E, pady=(10, 0))
        ttk.Button(btns, text=_("恢复默认"), command=_reset).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text=_("确定"), command=_save).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text=_("取消"), command=dlg.destroy).pack(side=tk.LEFT, padx=2)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        # 控件引用(测试与状态检查用; 对话框销毁后即失效)
        self._dup_param_state = {
            "car": cb_car, "author": cb_author, "img": cb_img, "img_var": img_var,
            "sim": cb_sim, "sim_var": sim_var, "created": cb_created,
            "down": cb_down, "templ": cb_templ, "desc": tmpl_desc,
            "save": _save, "reset": _reset, "apply_template": _on_templ,
        }
        # 居中于主窗口
        dlg.update_idletasks()
        dw, dh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        dlg.geometry(f"+{self.winfo_rootx() + (self.winfo_width() - dw) // 2}"
                     f"+{self.winfo_rooty() + (self.winfo_height() - dh) // 2}")

    def browse_folder(self):
        d = filedialog.askdirectory(title=_("选择 FH6 存档目录(remote 或 ContainersRoot)"))
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
        self.current = {"game": "fh6", "steam_user": _("手动"), "dir": folder}
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
        self.status_var.set(_("重复涂装分析中(解码缩略图计算哈希)…"))

        def _work():
            feats = fh6save.extract_dup_features(items)
            try:
                self.after(0, lambda: self._dup_ready(items, feats))
            except RuntimeError:
                pass                        # 窗口已销毁, 结果不再需要

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

    def _select_applied_filter(self, which: str):
        """「已喷涂」与「未喷涂」筛选互斥; 首次开启须过确认门(与「⚠ 检测喷涂状态」按钮相同)。"""
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
        return _("已喷在车上 ✓") if base in self._applied else _("未喷在车上")

    def _ensure_applied_scan(self, force: bool = False):
        """已喷涂检测按需触发: 仅 FH6 存档 + 游戏运行中; 后台只读扫描游戏内存。
        确认弹窗不在此处——统一由入口(按钮/开关)的 _confirm_applied_scan() 把关。"""
        if not self.current or self.current.get("game") != "fh6":
            return
        if self._applied_pending or (self._applied is not None and not force):
            return
        pid = gamemem.find_game_pid()
        if not pid:
            self.status_var.set(_("已喷涂检测: 未检测到游戏进程 (游戏启动后可重试)"))
            return
        self._applied_pending = True
        self.status_var.set(_("已喷涂检测: 只读扫描游戏内存中…"))

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
            try:
                self.after(0, lambda: self._applied_ready(names, err))
            except RuntimeError:
                pass                        # 窗口已销毁, 结果不再需要

        threading.Thread(target=_work, daemon=True).start()

    def _applied_ready(self, names, err):
        self._applied_pending = False
        from_button = self._applied_from_button
        self._applied_from_button = False
        if err or names is None:
            self.status_var.set(
                _("已喷涂检测失败: {err}").format(err=err or _("读取失败")))
            if from_button:
                messagebox.showwarning(
                    _("检测喷涂状态"),
                    _("检测失败: {err}").format(err=err or _("读取失败")),
                    parent=self)
            return
        self._applied = names
        self.rebuild_grid()
        self.status_var.set(_("已喷涂检测: {n} 个涂装正在车上").format(n=len(names)))
        if from_button:
            messagebox.showinfo(
                _("检测喷涂状态"),
                _("已标记喷涂\n\n{n} 个涂装正喷在车上, 已用喷漆角标标出。").format(
                    n=len(names)),
                parent=self)

    def _applied_rescan_tick(self):
        """已喷涂功能在用(本会话扫描过, 或已喷涂/未喷涂筛选开着)时:
        未扫描且游戏在跑则补全量扫描, 已扫描则定期快速重扫(保角标/计数实时)。"""
        want = (self._applied is not None or self.applied_only.get()
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
                    try:
                        self.after(0, lambda: self._applied_update(names))
                    except RuntimeError:
                        pass                # 窗口已销毁, 结果不再需要
                threading.Thread(target=_work, daemon=True).start()
        self.after(5000, self._applied_rescan_tick)

    def _applied_update(self, names):
        self._applied_pending = False
        if names is not None and names != self._applied:
            self._applied = names
            self.rebuild_grid()
            self.status_var.set(_("已喷涂检测: {n} 个涂装正在车上").format(n=len(names)))

    def car_display(self, it: SaveItem) -> str:
        if it.car_id == 0:
            return "-"
        name = self.car_table.name("fh6", it.car_id)
        return name if name else _("ID {id}").format(id=it.car_id)

    def _brand_of(self, it: SaveItem) -> str:
        """条目的车厂名; 车型未标注时为空串。"""
        name = self.car_table.name("fh6", it.car_id)
        return fh6save.car_brand(name) if name else ""

    def _on_group_select(self, _e=None):
        """「分组显示」切换: 选「车厂/作者」时把主选排序同步为对应模式,
        组间顺序(跟随排序链)自然按该字段排列; 切回「无」不动排序。"""
        want = {_("车厂"): _("车厂"), _("作者"): _("作者"),
                _("车型"): _("车型")}.get(self.group_var.get())
        if want and self.sort_var.get() != want:
            self.sort_var.set(want)
        self.rebuild_grid()

    def _filtered(self) -> list[SaveItem]:
        # 双搜索关键字: 都非空才都要求命中(与); 单框使用时等同旧版单关键字
        qwords = [s.strip().lower() for s in
                  (self.search_var.get(), self.search2_var.get())]
        qwords = [s for s in qwords if s]
        brand = self.brand_var.get()
        out = []
        for it in self.items:
            if it.itype != "Livery":          # 仅限涂装
                continue
            if self.dup_only.get():
                if not self._dup_group.get(it.base, 0):
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
            if brand != _("全部车厂") and self._brand_of(it) != brand:
                continue
            qf = self.quick_filter             # 右键快筛(车型/作者维度)
            if qf:
                if qf[0] == "car" and it.car_id != qf[1]:
                    continue
                if qf[0] == "creator" and (it.creator or "") != qf[1]:
                    continue
            if qwords:
                hay = f"{it.name} {it.creator} {it.car_id} {self.car_display(it)}".lower()
                if any(t not in hay for t in qwords):
                    continue
            out.append(it)
        return out

    # ------------------------------------------------------------ 选中与预览

    def select(self, base: str | None):
        prev = self._selected
        self._selected = base
        if prev != base:
            self._recolor_card(prev)            # 新旧两张就地换色(不在可视区的自动跳过)
            self._recolor_card(base)
        self.show_preview()

    def selected_item(self) -> SaveItem | None:
        return self.item_map.get(self._selected) if self._selected else None

    def _load_thumb(self, path: Path | None, max_w: int, max_h: int, badge: str = ""):
        """优先用 PIL(支持 webp/jpg 且缩放质量好), 否则退回 tk.PhotoImage。
        badge 非空时把位置角标画到图片右上角——仅详情面板大图预览用;
        卡片缩略图不烙任何角标(v1.4.0 起位置/喷涂角标都画在卡片层)。"""
        if not path or not path.exists():
            return None
        if HAS_PIL:
            img = self._compose_thumb(path, max_w, max_h, badge)
            return ImageTk.PhotoImage(img) if img is not None else None
        try:
            img = tk.PhotoImage(file=str(path))
        except tk.TclError:
            return None
        factor = max(1, -(-max(img.width(), img.height()) // min(max_w, max_h)))
        if factor > 1:
            img = img.subsample(factor, factor)
        return img

    def _compose_thumb(self, path: Path, max_w: int, max_h: int, badge: str = ""):
        """PIL 解码+缩放+角标合成, 返回 PIL 图(失败 None)。
        纯 PIL 无 tk 依赖, 可在工作线程里跑(缩略图线程池用)。"""
        try:
            img = Image.open(path)
            img.thumbnail((max_w, max_h))
            if badge:
                img = img.convert("RGBA")
                self._draw_badge(img, badge)
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
            self.thumb_label.configure(image="", text=_("(无预览图)"))
        pos = self._pos_map.get(it.base)
        lines = [
            _("名称: {name}").format(name=it.name or _("(未解析)")),
            _("车型: {car}").format(car=self.car_display(it)),
            _("作者: {creator}").format(creator=it.creator or "?"),
        ]
        if pos:
            lines.append(_("游戏内位置: {pos} (共 {total} 列)").format(
                pos=pos, total=self._total_cols))
            keys = fh6save.locate_keys(*self._layout[it.base], self._total_cols)
            path = " ".join(f"{d}×{n}" for d, n in keys)
            lines.append(_("按键路径: {path}").format(
                path=path or _("无需按键(就在 1行1列)")))
        lines += [
            _("日期: {ts}").format(
                ts=it.ts.strftime("%Y-%m-%d %H:%M:%S") if it.ts else "?"),
            _("状态: {status}").format(
                status=_("已分享") if it.published else _("本地")),
            _("大小: {size}").format(size=fmt_size(it.total_size)),
        ]
        applied_txt = self._applied_status(it.base)
        if applied_txt:
            lines.append(_("喷涂状态: {status}").format(status=applied_txt))
        gid = self._dup_group.get(it.base, 0)
        if gid:
            n = sum(1 for g in self._dup_group.values() if g == gid)
            lines.append(_("重复: 第 {gid} 组, 共 {n} 个相同涂装").format(gid=gid, n=n))
        if it.layer_count:
            lines.append(_("层数: {n}").format(n=it.layer_count))
        lines.append(_("文件: {base}").format(base=it.base))
        if it.header_car_id and it.header_car_id != it.car_id:
            lines.append(
                _("注意: header 内嵌车型 ID {id} 与文件名不一致").format(
                    id=it.header_car_id))
        if it.desc:
            lines.append(_("描述: {desc}").format(desc=it.desc))
        self._set_info("\n".join(lines))

    def show_big_preview(self):
        it = self.selected_item()
        if not it or not it.thumb_big:
            messagebox.showinfo(_("预览"), _("该条目没有预览图"))
            return
        ZoomPreview(self, it)

    def copy_text(self, text: str):
        """复制文本到剪贴板并在状态栏反馈。"""
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set(_("已复制: {text}").format(text=ellipsize(text, 60)))

    def _toggle_topmost(self):
        """置顶开关: 窗口浮在游戏上方, 方便随时调用。"""
        self.attributes("-topmost", self.topmost_var.get())

    def _confirm_applied_scan(self) -> bool:
        """机制/风险说明 + 确认框(「⚠ 检测喷涂状态」按钮与已喷涂筛选开关共用):
        用户确认且游戏运行中才返回 True。"""
        if not messagebox.askokcancel(_("检测喷涂状态"),
                                      APPLIED_NOTICE + "\n\n" + _("确认开始检测？"),
                                      parent=self):
            return False
        if not gamemem.find_game_pid():
            messagebox.showwarning(
                _("检测喷涂状态"),
                _("未检测到游戏进程。\n已喷涂检测需要游戏正在运行, 请启动游戏后重试。"),
                parent=self)
            return False
        return True

    def confirm_detect_applied(self):
        """顶栏「⚠ 检测喷涂状态」: 确认后开始内存扫描, 完成即用喷漆角标标出
        (角标常显, 无需开关); 检测完成由 _applied_ready 弹「已标记喷涂」。"""
        if not self._confirm_applied_scan():
            return
        self._applied_from_button = True     # 标记本次扫描来自确认流程, 完成后弹结果
        self._ensure_applied_scan(force=True)

    def open_settings(self):
        """设置窗口: 自动定位的按键节奏(毫秒), 仅本次运行有效(不落盘)。"""
        dlg = tk.Toplevel(self)
        dlg.title(_("设置"))
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()                          # 模态

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=_("自动定位按键节奏 (毫秒, 周期 = 保持 + 间隔):")).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 6))
        ttk.Label(body, text=_("按下保持:")).grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Label(body, text=_("键间间隔:")).grid(row=2, column=0, sticky=tk.W, pady=2)
        hold_var = tk.StringVar(value=str(self.key_hold_ms))
        gap_var = tk.StringVar(value=str(self.key_gap_ms))
        ttk.Spinbox(body, from_=0, to=2000, width=8,
                    textvariable=hold_var).grid(row=1, column=1, sticky=tk.W, pady=2)
        ttk.Spinbox(body, from_=0, to=2000, width=8,
                    textvariable=gap_var).grid(row=2, column=1, sticky=tk.W, pady=2)
        ttk.Label(body, text=_("(仅本次运行有效)")).grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=(2, 0))

        btns = ttk.Frame(body)
        btns.grid(row=4, column=0, columnspan=2, sticky=tk.E, pady=(10, 0))

        def _save():
            try:
                hold, gap = int(hold_var.get()), int(gap_var.get())
            except ValueError:
                messagebox.showerror(_("设置"), _("请输入整数毫秒值"), parent=dlg)
                return
            if not (0 <= hold <= 2000 and 0 <= gap <= 2000):
                messagebox.showerror(_("设置"), _("取值范围 0~2000 毫秒"), parent=dlg)
                return
            self.key_hold_ms, self.key_gap_ms = hold, gap
            self.status_var.set(
                _("按键节奏: 保持 {hold}ms + 间隔 {gap}ms (本次运行有效)").format(
                    hold=hold, gap=gap))
            dlg.destroy()

        def _reset():
            hold_var.set(str(DEFAULT_KEY_HOLD_MS))
            gap_var.set(str(DEFAULT_KEY_GAP_MS))

        ttk.Button(btns, text=_("恢复默认"), command=_reset).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text=_("确定"), command=_save).pack(side=tk.LEFT, padx=2)
        ttk.Button(btns, text=_("取消"), command=dlg.destroy).pack(side=tk.LEFT, padx=2)
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
            msg = _("该涂装没有游戏内位置信息") if it else _("请先在左侧选中一个涂装")
            messagebox.showinfo(_("自动定位"), msg)
            return
        keys = fh6save.locate_keys(rc[0], rc[1], self._total_cols)
        if not keys:
            self.status_var.set(_("该涂装就在 1行1列, 无需按键"))
            return
        hwnd = find_game_window()
        if hwnd is None:
            messagebox.showinfo(_("自动定位"), _("未找到游戏窗口, 请先打开游戏"))
            return
        if not force_foreground(hwnd):
            self.status_var.set(_("无法切换到游戏窗口, 已取消"))
            return
        self._locate_hwnd = hwnd
        self._locate_keys = keys
        self._send_keys()

    def _cancel_locate(self):
        """取消发送; 无定位在进行时为空操作。"""
        if not self._locate_running:
            return
        self._locate_cancel = True      # 发送线程每个按键前检查
        self.status_var.set(_("自动定位已取消"))

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
            self.status_var.set(_("自动定位完成: {path}").format(path=path))
        else:
            self.status_var.set(_("自动定位已取消"))

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
        menu.add_command(label=_("复制"), state=tk.NORMAL if sel else tk.DISABLED,
                         command=lambda: self.copy_text(sel))
        menu.add_command(label=_("全选"), command=self._info_select_all)
        menu.add_command(label=_("复制全部"),
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
            messagebox.showinfo(_("备份完成"),
                                _("已备份整个存档目录到:\n{out}").format(out=out))
            self.status_var.set(_("备份完成: {out}").format(out=out))
        except OSError as e:
            messagebox.showerror(_("备份失败"), str(e))

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
