# -*- coding: utf-8 -*-
"""flsbridge.py — Forza Livery Studio 外部桥接(3D 涂装预览)。

「3D 预览」功能不在本工具内渲染车模, 而是把存档容器里的 C_livery 交给
开源涂装编辑器 Forza Livery Studio(AGPL-3.0, github.com/Arstz/ForzaLiveryStudio)
打开——其命令行接受涂装文件路径并自动导入(main.cpp openStartupFiles,
isLiveryAssetFileName 认 ``C_livery`` / ``Livery_*.C_livery``), 车模取自
游戏目录明文 zip 内的 carbin。

本模块只负责: 探测 ForzaLiveryStudio.exe 位置 + 提供官网地址; 启动与
进程跟踪在 app.py(需主线程状态)。探测顺序:
  ① 调用方传入的已配置路径(appconfig fls_exe, 存在才用, 失效自动回落);
  ② 常见安装位置(%LOCALAPPDATA%\\Programs 等);
  ③ 开始菜单快捷方式(.lnk 内嵌目标路径, ANSI/UTF-16 两遍扫);
  ④ Downloads 目录浅层扫描(发布为 zip, 用户随处解压是常态)。
全部只读; 任何一步出错静默跳到下一步。"""
from __future__ import annotations

import os
import re
from pathlib import Path

FLS_EXE_NAME = "ForzaLiveryStudio.exe"
FLS_URL = "https://github.com/Arstz/ForzaLiveryStudio/releases/latest"

# .lnk 目标路径匹配: ANSI 与 UTF-16 各一遍(lnk 本地路径串无通配/引号)
_LNK_RE = re.compile(r"[A-Za-z]:\\[^\x00\r\n\"*?<>|\u0000]{0,240}"
                     + re.escape(FLS_EXE_NAME), re.IGNORECASE)
_LNK_SCAN_MAX = 400          # 开始菜单 .lnk 数量上限(防爆扫)
_LNK_BYTES_MAX = 64 * 1024   # 单个 .lnk 读取上限(目标串远在前部)


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


def find_fls(configured: str = "") -> Path | None:
    """探测 ForzaLiveryStudio.exe: 配置路径 → 常见位置 → 快捷方式 → Downloads。
    找到返回 Path, 找不到返回 None(调用方引导下载或手动指定)。"""
    if configured.strip():
        hit = _existing(configured.strip().strip('"'))
        if hit is not None:
            return hit
    return (_from_common_dirs() or _from_shortcuts() or _from_downloads())
