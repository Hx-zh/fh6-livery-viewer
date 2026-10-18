# -*- coding: utf-8 -*-
"""appconfig.py — 应用小配置落盘(%LOCALAPPDATA%\\FH6LiveryViewer\\config.json)。

v1.8.0 之前为保持「单文件零外部文件零注册表」全部设置为会话级; 自车型表在线
更新引入该数据目录后, 低频小配置一并落在这里(仍零注册表、不写 exe 旁)。
当前存八项: 自动定位按键节奏(key_hold_ms/key_gap_ms, 与设置对话框校验同范围)、
「自动检测存档更新」开关(auto_refresh, v1.8.0 从顶栏移入设置)、
「自动检查车型表更新」开关(cars_auto_check, v1.8.0 起每次启动检查一次)、
手动选择的存档目录(manual_dir; 「手动选择目录…」后记住, 启动时恢复到下拉首项,
改选其它存档或目录消失/被自动检测覆盖时自动清除, 空串=未设置)、
游戏安装目录(game_dir; 3D 预览的 --game 参数, 空串=自动探测; 手动指定后
优先于探测, 目录失效自动回落探测)、卡片墙缩放倍数(card_scale; 右上角
[−] 百分比下拉 [+] 选择后记住, 下次启动按原倍数恢复, 缺省 1.0 = 100%)
和 3D 预览窗口数上限(viewer_max_windows; 0=不限制, 1=复用同一窗口就地换涂装,
N=最多并存 N 个, 缺省 1)。
文件缺失/损坏/字段非法 → 逐字段回默认值, 不抛异常、不影响启动。
写入一律走读-改-写(_write), 多个保存入口互不覆盖; 读取方: App 启动;
写入方: 设置对话框(按键节奏 / 开关 / 窗口上限)、卡片墙缩放变更(_set_card_scale)。"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import carupdate

CONFIG_NAME = "config.json"
KEY_TIMING_MAX = 2000   # 毫秒上限, 与设置对话框校验一致
# 卡片墙缩放合法范围(app.py 的同名常量来自这里, 单一来源)
CARD_SCALE_MIN, CARD_SCALE_MAX = 0.2, 5.0
# 同时打开的 3D 预览窗口上限: 0 = 不限制; 默认 1 = 只保留一个窗口, 后续预览替换其内容
VIEWER_WINDOWS_MAX = 16

_DEFAULTS: dict[str, Any] = {"key_hold_ms": 15, "key_gap_ms": 50,
                              "auto_refresh": True, "cars_auto_check": True,
                              "manual_dir": "", "game_dir": "",
                              "card_scale": 1.0,
                              "viewer_max_windows": 1}


def load() -> dict[str, Any]:
    """读取配置(缺省/损坏/字段非法逐项回默认; 目录不可用返回全默认)。"""
    out = dict(_DEFAULTS)
    d = carupdate.cache_dir()
    if d is None:
        return out
    try:
        obj = json.loads((d / CONFIG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if isinstance(obj, dict):
        for k in ("key_hold_ms", "key_gap_ms"):
            v = obj.get(k)
            if (isinstance(v, int) and not isinstance(v, bool)
                    and 0 <= v <= KEY_TIMING_MAX):
                out[k] = v
        for k in ("auto_refresh", "cars_auto_check"):
            v = obj.get(k)
            if isinstance(v, bool):
                out[k] = v
        v = obj.get("manual_dir")
        if isinstance(v, str) and len(v) <= 1024:
            out["manual_dir"] = v
        v = obj.get("game_dir")
        if isinstance(v, str) and len(v) <= 1024:
            out["game_dir"] = v
        v = obj.get("card_scale")
        if (isinstance(v, (int, float)) and not isinstance(v, bool)
                and CARD_SCALE_MIN <= float(v) <= CARD_SCALE_MAX):
            out["card_scale"] = float(v)
        v = obj.get("viewer_max_windows")
        if (isinstance(v, int) and not isinstance(v, bool)
                and 0 <= v <= VIEWER_WINDOWS_MAX):
            out["viewer_max_windows"] = v
    return out


def _write(cfg: dict[str, Any]) -> bool:
    """固定名 tmp + os.replace 原子写整份配置(与 carupdate 缓存同款语义)。"""
    d = carupdate.cache_dir()
    if d is None:
        return False
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (CONFIG_NAME + ".tmp")
        tmp.write_text(json.dumps(cfg), encoding="utf-8")
        os.replace(tmp, d / CONFIG_NAME)
    except OSError:
        return False
    return True


def save_key_timing(hold_ms: int, gap_ms: int) -> bool:
    """保存按键节奏(读-改-写, 保留其它字段)。目录不可用/写失败返回 False
    ——设置退化为仅本次运行有效, 不报错。"""
    cfg = load()
    cfg["key_hold_ms"], cfg["key_gap_ms"] = int(hold_ms), int(gap_ms)
    return _write(cfg)


def set_auto_refresh(value: bool) -> bool:
    """保存「自动检测存档更新」开关(读-改-写, 保留其它字段)。"""
    cfg = load()
    cfg["auto_refresh"] = bool(value)
    return _write(cfg)


def set_cars_auto_check(value: bool) -> bool:
    """保存「自动检查车型表更新」开关(读-改-写, 保留其它字段)。"""
    cfg = load()
    cfg["cars_auto_check"] = bool(value)
    return _write(cfg)


def set_manual_dir(path: str) -> bool:
    """保存/清除手动选择的存档目录(读-改-写, 保留其它字段; 空串=清除)。"""
    cfg = load()
    cfg["manual_dir"] = str(path)
    return _write(cfg)


def set_game_dir(path: str) -> bool:
    """保存/清除手动指定的游戏安装目录(读-改-写, 保留其它字段; 空串=清除)。"""
    cfg = load()
    cfg["game_dir"] = str(path)
    return _write(cfg)


def set_card_scale(value: float) -> bool:
    """保存卡片墙缩放倍数(读-改-写, 保留其它字段; 越界收敛到合法范围)。
    写失败退化为仅本次运行有效, 不报错。"""
    cfg = load()
    cfg["card_scale"] = min(CARD_SCALE_MAX, max(CARD_SCALE_MIN, round(float(value), 2)))
    return _write(cfg)


def set_viewer_max_windows(value: int) -> bool:
    """保存同时打开的 3D 预览窗口上限(0=不限制; 越界收敛到合法范围)。"""
    cfg = load()
    cfg["viewer_max_windows"] = min(VIEWER_WINDOWS_MAX, max(0, int(value)))
    return _write(cfg)


# ---------------------------------------------------------------- 旧版本遗留
# 2026-10 之前, 查看器把游戏目录存在注册表 `HKCU\Software\ForzaTools\FH6Livery3DViewer`,
# 贴花缓存放在 `%APPDATA%\ForzaTools\FH6Livery3DViewer\raster\`。现在两样都归到
# `%LOCALAPPDATA%\FH6LiveryViewer`, 旧位置只读兼容、**绝不自动删除**——清理是用户显式动作
# (「设置 → 清除旧版本缓存/注册表」), 而不是启动时的副作用。
LEGACY_REG_SUBKEY = r"Software\ForzaTools\FH6Livery3DViewer"


def _legacy_appdata_dir() -> Path | None:
    raw = os.environ.get("APPDATA", "").strip()
    if not raw:
        return None
    return Path(raw) / "ForzaTools" / "FH6Livery3DViewer"


def _legacy_reg_values() -> list[str] | None:
    """旧注册表键里的值(形如 "gameFolder=..." ); 键不存在返回 None。"""
    try:
        import winreg          # noqa: PLC0415  Windows 专用, 保持模块可导入
    except ImportError:
        return None
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, LEGACY_REG_SUBKEY)
    except OSError:
        return None
    values: list[str] = []
    try:
        index = 0
        while True:
            try:
                name, data, _kind = winreg.EnumValue(key, index)
            except OSError:
                break
            values.append(f"{name}={data}")
            index += 1
    finally:
        key.Close()
    return values


def legacy_leftovers() -> list[tuple[str, int]]:
    """旧版本遗留项: [(可读路径, 字节数)]; 字节数 -1 表示注册表键。空列表 = 没有残留。"""
    found: list[tuple[str, int]] = []
    d = _legacy_appdata_dir()
    if d is not None and d.is_dir():
        total = 0
        try:
            for f in d.rglob("*"):
                try:
                    if f.is_file():
                        total += f.stat().st_size
                except OSError:
                    continue
        except OSError:
            pass
        found.append((str(d), total))
    values = _legacy_reg_values()
    if values is not None:
        key = f"HKCU\\{LEGACY_REG_SUBKEY}"
        found.append((f"{key}  ({'; '.join(values)})" if values else key, -1))
    return found


def _delete_reg_tree(root: Any, sub: str) -> None:
    """递归删除注册表子键(winreg.DeleteKey 拒绝非空键)。"""
    import winreg                      # noqa: PLC0415

    try:
        key = winreg.OpenKey(root, sub, 0, winreg.KEY_ALL_ACCESS)
    except OSError:
        return
    try:
        while True:
            try:
                child = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_reg_tree(key, child)
    finally:
        key.Close()
    winreg.DeleteKey(root, sub)


def clear_legacy() -> tuple[list[str], list[str]]:
    """删除旧版本遗留(目录 + 注册表键)。返回 (已删除, 失败说明)。

    只碰 `ForzaTools\\FH6Livery3DViewer` 这一层: 共享的 `ForzaTools` 上级目录若还装着
    别的东西就留着。当前配置与缓存(`%LOCALAPPDATA%\\FH6LiveryViewer`)不受影响。"""
    removed: list[str] = []
    failed: list[str] = []
    d = _legacy_appdata_dir()
    if d is not None and d.is_dir():
        try:
            shutil.rmtree(d)
            removed.append(str(d))
            try:
                d.parent.rmdir()       # 仅当 ForzaTools 已空
            except OSError:
                pass
        except OSError as e:
            failed.append(f"{d}: {e}")
    if _legacy_reg_values() is not None:
        try:
            import winreg          # noqa: PLC0415

            _delete_reg_tree(winreg.HKEY_CURRENT_USER, LEGACY_REG_SUBKEY)
            removed.append(f"HKCU\\{LEGACY_REG_SUBKEY}")
        except OSError as e:
            failed.append(f"HKCU\\{LEGACY_REG_SUBKEY}: {e}")
    return removed, failed
