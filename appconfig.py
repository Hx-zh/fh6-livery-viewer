# -*- coding: utf-8 -*-
"""appconfig.py — 应用小配置落盘(%LOCALAPPDATA%\\FH6LiveryViewer\\config.json)。

v1.8.0 之前为保持「单文件零外部文件零注册表」全部设置为会话级; 自车型表在线
更新引入该数据目录后, 低频小配置一并落在这里(仍零注册表、不写 exe 旁)。
当前存三项: 自动定位按键节奏(key_hold_ms/key_gap_ms, 与设置对话框校验同范围)、
「自动检测存档更新」开关(auto_refresh, v1.8.0 从顶栏移入设置)和
「自动检查车型表更新」开关(cars_auto_check, v1.8.0 起每次启动检查一次)。
文件缺失/损坏/字段非法 → 逐字段回默认值, 不抛异常、不影响启动。
写入一律走读-改-写(_write), 多个保存入口互不覆盖; 读取方: App 启动;
写入方: 设置对话框(按键节奏确定 / 开关切换)。"""
from __future__ import annotations

import json
import os
from typing import Any

import carupdate

CONFIG_NAME = "config.json"
KEY_TIMING_MAX = 2000   # 毫秒上限, 与设置对话框校验一致

_DEFAULTS: dict[str, Any] = {"key_hold_ms": 15, "key_gap_ms": 50,
                              "auto_refresh": True, "cars_auto_check": True}


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
            if isinstance(v, int) and 0 <= v <= KEY_TIMING_MAX:
                out[k] = v
        for k in ("auto_refresh", "cars_auto_check"):
            v = obj.get(k)
            if isinstance(v, bool):
                out[k] = v
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
