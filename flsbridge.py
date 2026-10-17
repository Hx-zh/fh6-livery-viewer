# -*- coding: utf-8 -*-
"""flsbridge.py — 内置 3D 查看器(FH6Livery3DViewer)桥接。

「3D 预览」只使用随发布包附带的 3DViewer\\FH6Livery3DViewer.exe(源码运行回退
仓库 viewer3d\\build\\ 本地构建产物)——基于 vendored Forza Livery Studio 快照
(viewer3d/, AGPL-3.0)构建的只读查看器。本模块负责: 定位内置查看器 + 探测
游戏安装目录(启动时自动传 --game, 用户零配置)。全部只读。"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

VIEWER_EXE_NAME = "FH6Livery3DViewer.exe"
VIEWER_SUBDIR = "3DViewer"          # 发布 zip 内置查看器的子目录

_VDF_PATH = r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf"
_VDF_LIB_RE = re.compile(r'"path"\s+"([^"]+)"')
_GAME_REL = os.path.join("steamapps", "common", "ForzaHorizon6")


def find_viewer() -> Path | None:
    """定位内置查看器: 打包运行 = exe 同目录 3DViewer\\; 源码运行 = 仓库 viewer3d\\build。
    找不到返回 None(调用方提示重新解压发布包 / 本地构建)。"""
    candidates: list[Path] = []
    exe = Path(sys.executable)
    candidates.append(exe.parent / VIEWER_SUBDIR / VIEWER_EXE_NAME)
    if not exe.name.lower().startswith("python"):
        candidates.append(Path(sys.argv[0]).resolve().parent
                          / VIEWER_SUBDIR / VIEWER_EXE_NAME)
    candidates.append(Path(__file__).resolve().parent
                      / "viewer3d" / "build" / VIEWER_EXE_NAME)
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


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
