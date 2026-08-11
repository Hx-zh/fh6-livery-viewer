# -*- coding: utf-8 -*-
"""
app.py — FH6 涂装管理器 (GUI)

仅管理《极限竞速:地平线 6》存档,主视图为缩略图平铺。

用法: python app.py        启动图形界面
      python app.py --smoke  冒烟测试(构建界面后立即退出)
      python app.py --demo   启动并自动选中第一个条目(调试用)
"""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import fh6save
from fh6save import CarTable, SaveItem, SaveOps

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

APP_DIR = Path(__file__).resolve().parent
BACKUP_DIR = APP_DIR / "backups"

CARD_W, CARD_H = 196, 208      # 卡片尺寸
THUMB_W, THUMB_H = 184, 120    # 卡片缩略图区域

SORT_OPTIONS = ["下载日期(新→旧)", "下载日期(旧→新)", "名称", "车型", "作者"]


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
        super().__init__(master, width=CARD_W, height=CARD_H, bd=1,
                         relief=tk.GROOVE, bg="#ffffff")
        self.app = app
        self.item = item
        self.pack_propagate(False)

        self.img_label = tk.Label(self, text="加载中…", bg="#f0f0f0",
                                  fg="#888888", anchor=tk.CENTER)
        self.img_label.place(x=5, y=5, width=THUMB_W, height=THUMB_H)

        self.name_label = tk.Label(self, text=ellipsize(item.name or "(未解析)", 24),
                                   anchor=tk.W, bg="#ffffff",
                                   font=("Microsoft YaHei UI", 9, "bold"))
        self.name_label.place(x=7, y=THUMB_H + 8, width=THUMB_W)

        car = app.car_display(item)
        self.car_label = tk.Label(self, text=ellipsize(car, 30),
                                  anchor=tk.W, bg="#ffffff", fg="#333333",
                                  font=("Microsoft YaHei UI", 8))
        self.car_label.place(x=7, y=THUMB_H + 30, width=THUMB_W)

        # 第三行: 车型已识别 → 显示涂装作者; 未识别 → 显示 ID 和日期(便于排查)
        known = bool(app.car_table.name("fh6", item.car_id))
        date = item.ts.strftime("%Y-%m-%d") if item.ts else ""
        if known or not item.car_id:
            third = item.creator or "?"
        else:
            third = f"ID {item.car_id}  {date}"
        self.sub_label = tk.Label(self, text=ellipsize(third, 30),
                                  anchor=tk.W, bg="#ffffff", fg="#888888",
                                  font=("Microsoft YaHei UI", 8))
        self.sub_label.place(x=7, y=THUMB_H + 52, width=THUMB_W)

        for w in (self, self.img_label, self.name_label, self.car_label, self.sub_label):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Double-1>", self._on_dbl)

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
        color = "#0078D7" if on else "#ffffff"
        name_fg = "#ffffff" if on else "#000000"
        car_fg = "#eaf4ff" if on else "#333333"
        sub_fg = "#cce6ff" if on else "#888888"
        self.configure(bg=color, highlightthickness=2,
                       highlightbackground="#005a9e" if on else "#cccccc")
        self.name_label.configure(bg=color, fg=name_fg)
        self.car_label.configure(bg=color, fg=car_fg)
        self.sub_label.configure(bg=color, fg=sub_fg)


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
        self._src = None
        if HAS_PIL:
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
        disp = self._src.resize((w, h), Image.LANCZOS)
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

        self.car_table = CarTable(APP_DIR / "cars.json")
        self.ops = SaveOps(BACKUP_DIR)
        self.saves: list[dict] = []
        self.current: dict | None = None
        self.items: list[SaveItem] = []
        self.item_map: dict[str, SaveItem] = {}
        self.cards: dict[str, Card] = {}
        self._img_cache: dict[str, object] = {}   # base -> PhotoImage
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
        self.unknown_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(flt, text="仅显示未标注车型", variable=self.unknown_only,
                        command=self.rebuild_grid).pack(side=tk.LEFT)
        ttk.Label(flt, text="排序:").pack(side=tk.LEFT, padx=(14, 2))
        self.sort_var = tk.StringVar(value=SORT_OPTIONS[0])
        scb = ttk.Combobox(flt, textvariable=self.sort_var, state="readonly",
                           width=16, values=SORT_OPTIONS)
        scb.pack(side=tk.LEFT)
        scb.bind("<<ComboboxSelected>>", lambda _e: self.rebuild_grid())

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
        self.info_var = tk.StringVar(value="未选择条目")
        ttk.Label(right, textvariable=self.info_var, justify=tk.LEFT,
                  wraplength=400).pack(fill=tk.X, pady=(0, 6))
        btns = tk.Frame(right)
        btns.pack(fill=tk.X)
        for text, cmd in (("查看大图", self.show_big_preview),
                          ("标注车型…", self.tag_car),
                          ("所在文件夹", self.open_item_folder)):
            ttk.Button(btns, text=text, command=cmd).pack(fill=tk.X, pady=1)

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
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W, padding=(6, 2)).pack(fill=tk.X, side=tk.BOTTOM)

    # ------------------------------------------------------------ 平铺布局

    def _bind_wheel(self, _e):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _e):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), "units")

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
        self.info_var.set("未选择条目")
        self.thumb_label.configure(image="", text="(无预览)")
        known = self.car_table.known_count("fh6")
        self.status_var.set(
            f"共 {len(self.items)} 个条目, 显示 {len(shown)} 个  |  "
            f"已标注车型 {known} 个  |  {self.current['dir'] if self.current else ''}")

    def _load_thumbs_batch(self, n: int = 8):
        """分批在主线程解码缩略图, 保持界面响应。"""
        batch, self._thumb_pending = self._thumb_pending[:n], self._thumb_pending[n:]
        for base in batch:
            card = self.cards.get(base)
            if not card:
                continue
            img = self._load_thumb(card.item.thumb_big, THUMB_W, THUMB_H)
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
            src = src_names.get(s.get("source"), s.get("source", "?"))
            labels.append(f"[{src}]  用户 {s['steam_user']}  |  {s['dir']}")
        self.save_combo.configure(values=labels)
        if labels:
            self.save_combo.current(0)
            self.load_current()
        else:
            self.current = None
            self.items = []
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
        self._img_cache.clear()
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

    def car_display(self, it: SaveItem) -> str:
        if it.car_id == 0:
            return "-"
        name = self.car_table.name("fh6", it.car_id)
        return name if name else f"ID {it.car_id}"

    def _filtered(self) -> list[SaveItem]:
        q = self.search_var.get().strip().lower()
        out = []
        for it in self.items:
            if it.itype != "Livery":          # 仅限涂装
                continue
            if self.unknown_only.get() and it.car_id:
                if self.car_table.name("fh6", it.car_id):
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
            return sorted(items, key=lambda i: self.car_display(i).lower())
        if mode == "作者":
            return sorted(items, key=lambda i: (i.creator or "").lower())
        return items

    # ------------------------------------------------------------ 选中与预览

    def select(self, base: str | None):
        self._selected = base
        for b, card in self.cards.items():
            card.set_selected(b == base)
        self.show_preview()

    def selected_item(self) -> SaveItem | None:
        return self.item_map.get(self._selected) if self._selected else None

    def _load_thumb(self, path: Path | None, max_w: int, max_h: int):
        """优先用 PIL(支持 webp/jpg 且缩放质量好), 否则退回 tk.PhotoImage。"""
        if not path or not path.exists():
            return None
        if HAS_PIL:
            try:
                img = Image.open(path)
                img.thumbnail((max_w, max_h))
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

    def show_preview(self):
        it = self.selected_item()
        if not it:
            return
        img = self._load_thumb(it.thumb_big, 380, 300)
        self._detail_img = img
        if img:
            self.thumb_label.configure(image=img, text="")
        else:
            self.thumb_label.configure(image="", text="(无预览图)")
        lines = [
            f"类型: {it.type_cn}",
            f"名称: {it.name or '(未解析)'}",
            f"车型: {self.car_display(it)}",
            f"作者: {it.creator or '?'}",
            f"日期: {it.ts.strftime('%Y-%m-%d %H:%M:%S') if it.ts else '?'}",
            f"状态: {'已分享' if it.published else '本地'}",
            f"大小: {fmt_size(it.total_size)}",
        ]
        if it.layer_count:
            lines.append(f"层数: {it.layer_count}")
        lines.append(f"文件: {it.base}")
        if it.header_car_id and it.header_car_id != it.car_id:
            lines.append(f"注意: header 内嵌车型 ID {it.header_car_id} 与文件名不一致")
        if it.desc:
            lines.append(f"描述: {it.desc}")
        self.info_var.set("\n".join(lines))

    def show_big_preview(self):
        it = self.selected_item()
        if not it or not it.thumb_big:
            messagebox.showinfo("预览", "该条目没有预览图")
            return
        ZoomPreview(self, it)

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

    def tag_car(self):
        it = self.selected_item()
        if not it:
            return
        if not it.car_id:
            messagebox.showinfo("标注车型", "该条目没有车型 ID")
            return
        cur = self.car_table.name("fh6", it.car_id)
        new = simpledialog.askstring(
            "标注车型", f"车型 ID {it.car_id} 的名称\n(例如: Nissan GT-R NISMO 2020):",
            initialvalue=cur, parent=self)
        if new is None:
            return
        self.car_table.set_name("fh6", it.car_id, new.strip())
        base = it.base
        self.rebuild_grid()
        self.select(base)


if __name__ == "__main__":
    App().mainloop()
