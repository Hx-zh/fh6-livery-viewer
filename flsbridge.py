# -*- coding: utf-8 -*-
"""flsbridge.py — 内置 3D 查看器(FH6Livery3DViewer)桥接。

「3D 预览」只使用随发布包附带的 3DViewer\\FH6Livery3DViewer.exe(源码运行回退
仓库 viewer3d\\build\\ 本地构建产物)——基于 vendored Forza Livery Studio 快照
(viewer3d/, AGPL-3.0)构建的只读查看器。本模块负责: 定位内置查看器 + 探测
游戏安装目录(启动时自动传 --game, 用户零配置)。全部只读。

游戏目录探测(2026-10-17 增强, 用户反馈商店版/非默认 Steam 安装找不到):
  ① Steam 注册表(SteamPath/InstallPath) → libraryfolders.vdf 全部库目录;
  ② 各盘常见 SteamLibrary/Steam 目录;
  ③ 各盘 XboxGames\\ 浅层扫描(商店版); 判定一律以目录内含 media\\Cars 为准。"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

VIEWER_EXE_NAME = "FH6Livery3DViewer.exe"
VIEWER_SUBDIR = "3DViewer"          # 发布 zip 内置查看器的子目录

_VDF_LIB_RE = re.compile(r'"path"\s+"([^"]+)"')
_DRIVES = "CDEFGHIJKLMNOPQRSTUVWXYZ"


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


def is_game_root(path: Path | str) -> bool:
    """判定目录是 FH6 游戏安装根: 平铺(<root>\\media\\Cars)或 Content 子层
    (<root>\\Content\\media\\Cars) 任一存在即为真(商店版布局差异)。"""
    try:
        p = Path(path)
        return ((p / "media" / "Cars").is_dir()
                or (p / "Content" / "media" / "Cars").is_dir())
    except OSError:
        return False


def _steam_libraries() -> list[Path]:
    """Steam 各库根目录: 注册表 -> vdf 全库列表 -> 各盘常见目录。"""
    steam_roots: list[Path] = []

    def add_root(p: str) -> None:
        try:
            q = Path(p)
            if q.is_dir() and q not in steam_roots:
                steam_roots.append(q)
        except OSError:
            pass

    try:
        import winreg
        for hive, key, value in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    add_root(winreg.QueryValueEx(k, value)[0])
            except OSError:
                pass
    except ImportError:
        pass
    add_root(r"C:\Program Files (x86)\Steam")
    for drive in _DRIVES:
        add_root(f"{drive}:\\SteamLibrary")
        add_root(f"{drive}:\\Steam")

    libs: list[Path] = []
    for root in steam_roots:
        if root not in libs:
            libs.append(root)
        try:
            text = (root / "steamapps" / "libraryfolders.vdf").read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lib in _VDF_LIB_RE.findall(text):
            try:
                q = Path(lib.replace("\\\\", "\\"))
                if q not in libs:
                    libs.append(q)
            except OSError:
                continue
    return libs


def _xbox_candidates() -> list[Path]:
    """商店版: 各盘 XboxGames\\ 下最多两层的目录含 media\\Cars 即候选。"""
    found: list[Path] = []
    for drive in _DRIVES:
        root = Path(f"{drive}:\\XboxGames")
        try:
            if not root.is_dir():
                continue
            for sub in root.iterdir():
                if not sub.is_dir():
                    continue
                for cand in (sub, sub / "Content"):
                    if is_game_root(cand):
                        found.append(cand)
        except OSError:
            continue
    return found


def detect_game_root() -> Path | None:
    """探测 FH6 游戏安装根目录(只读)。全部候选一律以 media\\Cars 存在为准;
    Steam 侧兼容非默认安装位置与多库, 商店版扫各盘 XboxGames。找不到返回 None。"""
    for lib in _steam_libraries():
        cand = lib / "steamapps" / "common" / "ForzaHorizon6"
        if is_game_root(cand):
            return cand
    for cand in _xbox_candidates():
        return cand
    return None
