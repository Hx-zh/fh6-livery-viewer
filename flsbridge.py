# -*- coding: utf-8 -*-
"""flsbridge.py — 3D 预览外部程序桥接(自研查看器优先, Forza Livery Studio 兜底)。

「3D 预览」按钮把存档容器里的 C_livery 交给外部 3D 程序打开, 优先级:
  ① appconfig 配置路径(fls_exe, 用户手动指定——查看器或 FLS 均可);
  ② **内置查看器** FH6Livery3DViewer.exe(发布 zip 的 3DViewer/ 子目录自带;
     源码运行回退 viewer3d/build/ 本地构建产物)——我们的自研只读查看器,
     基于 vendored Forza Livery Studio 快照(viewer3d/, AGPL-3.0), 启动时
     自动传 --game 游戏目录, 用户零配置;
  ③ 用户自装的开源编辑器 Forza LiveryStudio(常见安装位置 → 开始菜单
     快捷方式(.lnk 内嵌路径 ANSI/UTF-16 两遍扫) → Downloads 浅层扫描)。
全部只读; 任何一步出错静默跳到下一步。"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

FLS_EXE_NAME = "ForzaLiveryStudio.exe"
VIEWER_EXE_NAME = "FH6Livery3DViewer.exe"
VIEWER_SUBDIR = "3DViewer"          # 发布 zip 内置查看器的子目录
FLS_URL = "https://github.com/Arstz/ForzaLiveryStudio/releases/latest"


# .lnk 目标路径匹配: ANSI 与 UTF-16 各一遍(lnk 本地路径串无通配/引号)
_LNK_RE = re.compile(r"[A-Za-z]:\\[^\x00\r\n\"*?<>|\u0000]{0,240}"
                     + re.escape(FLS_EXE_NAME), re.IGNORECASE)
_LNK_SCAN_MAX = 400          # 开始菜单 .lnk 数量上限(防爆扫)
_LNK_BYTES_MAX = 64 * 1024   # 单个 .lnk 读取上限(目标串远在前部)
_VDF_PATH = r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf"
_VDF_LIB_RE = re.compile(r'"path"\s+"([^"]+)"')
_GAME_REL = os.path.join("steamapps", "common", "ForzaHorizon6")


class FlsTarget:
    """3D 预览目标程序: 路径 + 是否自研查看器(支持 --game 参数免配置)。"""

    __slots__ = ("path", "bundled")

    def __init__(self, path: Path, bundled: bool):
        self.path = path
        self.bundled = bundled


def detect_game_root() -> Path | None:
    """探测 FH6 游戏安装根目录(只读): Steam libraryfolders.vdf 各库
    → 常见根(含各盘 SteamLibrary)。找不到返回 None。"""
    libs: list[str] = []
    try:
        text = Path(_VDF_PATH).read_text(encoding="utf-8", errors="replace")
        libs += _VDF_LIB_RE.findall(text)
    except OSError:
        pass
    libs.append(r"C:\Program Files (x86)\Steam")
    for drive in "CDEFGH":
        libs.append(f"{drive}:\\SteamLibrary")
    for lib in libs:
        cand = Path(lib) / _GAME_REL
        try:
            if cand.is_dir():
                return cand
        except OSError:
            continue
    return None


def _bundled_viewer() -> Path | None:
    """内置查看器: 打包运行 = exe 同目录 3DViewer\\; 源码运行 = 仓库 viewer3d\\build。"""
    candidates: list[Path] = []
    exe = Path(sys.executable)
    candidates.append(exe.parent / VIEWER_SUBDIR / VIEWER_EXE_NAME)
    if not exe.name.lower().startswith("python"):
        candidates.append(Path(sys.argv[0]).resolve().parent
                          / VIEWER_SUBDIR / VIEWER_EXE_NAME)
    candidates.append(Path(__file__).resolve().parent
                      / "viewer3d" / "build" / VIEWER_EXE_NAME)
    for c in candidates:
        if _existing(str(c)) is not None:
            return c
    return None


def _existing(path: str) -> Path | None:
    """路径存在且是文件才返回 Path, 否则 None。"""
    try:
        p = Path(path)
        return p if p.is_file() else None
    except OSError:
        return None


def _from_common_dirs() -> Path | None:
    """常见安装位置: 逐根目录尝试平铺与 ForzaLiveryStudio 子目录两种摆法。"""
    roots: list[str] = []
    for var in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        v = os.environ.get(var)
        if v:
            roots.append(v)
    for root in roots:
        for d in (root, os.path.join(root, "Programs"),
                  os.path.join(root, "ForzaLiveryStudio"),
                  os.path.join(root, "Programs", "ForzaLiveryStudio")):
            hit = _existing(os.path.join(d, FLS_EXE_NAME))
            if hit is not None:
                return hit
    return None


def _from_shortcuts() -> Path | None:
    """开始菜单 .lnk: 目标路径按 ANSI 与 UTF-16-LE 两遍正则提取。"""
    menu_roots: list[Path] = []
    for var in ("APPDATA", "PROGRAMDATA"):
        v = os.environ.get(var)
        if v:
            menu_roots.append(Path(v) / "Microsoft" / "Windows"
                              / "Start Menu" / "Programs")
    seen = 0
    for root in menu_roots:
        try:
            links = list(root.rglob("*.lnk"))
        except OSError:
            continue
        for lnk in links:
            seen += 1
            if seen > _LNK_SCAN_MAX:
                return None
            try:
                data = lnk.read_bytes()[:_LNK_BYTES_MAX]
            except OSError:
                continue
            for text in (data.decode("latin-1", errors="ignore"),
                         data.decode("utf-16-le", errors="ignore")):
                m = _LNK_RE.search(text)
                if m:
                    hit = _existing(m.group(0))
                    if hit is not None:
                        return hit
    return None


def _from_downloads() -> Path | None:
    """Downloads 浅层扫描(深度 ≤2): zip 解压出文件夹再放 exe 是常见摆法。"""
    dl = os.environ.get("USERPROFILE")
    if not dl:
        return None
    root = Path(dl) / "Downloads"
    try:
        hit = _existing(str(root / FLS_EXE_NAME))
        if hit is not None:
            return hit
        for sub in root.iterdir():
            if not sub.is_dir():
                continue
            hit = _existing(str(sub / FLS_EXE_NAME))
            if hit is not None:
                return hit
            # 再深一层(ForzaLiveryStudio-x.y.z/ForzaLiveryStudio.exe)
            try:
                for sub2 in sub.iterdir():
                    if sub2.is_dir():
                        hit = _existing(str(sub2 / FLS_EXE_NAME))
                        if hit is not None:
                            return hit
            except OSError:
                continue
    except OSError:
        return None
    return None


def find_fls(configured: str = "") -> FlsTarget | None:
    """探测 3D 预览程序: 配置路径 → 内置查看器 → FLS(常见位置→快捷方式→Downloads)。
    返回 FlsTarget(bundled=自研查看器, 支持 --game); 找不到返回 None(调用方引导)。"""
    if configured.strip():
        hit = _existing(configured.strip().strip('"'))
        if hit is not None:
            return FlsTarget(hit, hit.name.lower() == VIEWER_EXE_NAME.lower())
    bundled = _bundled_viewer()
    if bundled is not None:
        return FlsTarget(bundled, True)
    hit = (_from_common_dirs() or _from_shortcuts() or _from_downloads())
    return FlsTarget(hit, False) if hit is not None else None
