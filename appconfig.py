# -*- coding: utf-8 -*-
"""appconfig.py — 应用小配置落盘(%LOCALAPPDATA%\\FH6LiveryViewer\\config.json)。

v1.8.0 之前为保持「单文件零外部文件零注册表」全部设置为会话级; 自车型表在线
更新引入该数据目录后, 低频小配置一并落在这里(仍零注册表、不写 exe 旁)。
当前仅存自动定位按键节奏(key_hold_ms/key_gap_ms, 与设置对话框校验同范围);
文件缺失/损坏/字段非法 → 逐字段回默认值, 不抛异常、不影响启动。
写入方: 设置对话框「确定」(app.py); 读取方: App 启动。"""
from __future__ import annotations

import json
import os

import carupdate

CONFIG_NAME = "config.json"
KEY_TIMING_MAX = 2000   # 毫秒上限, 与设置对话框校验一致

_DEFAULTS: dict[str, int] = {"key_hold_ms": 15, "key_gap_ms": 50}


def load() -> dict[str, int]:
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
        for k in out:
            v = obj.get(k)
            if isinstance(v, int) and 0 <= v <= KEY_TIMING_MAX:
                out[k] = v
    return out


def save_key_timing(hold_ms: int, gap_ms: int) -> bool:
    """保存按键节奏(固定名 tmp + os.replace 原子写, 与 carupdate 缓存同款语义)。
    目录不可用/写失败返回 False——设置退化为仅本次运行有效, 不报错。"""
    d = carupdate.cache_dir()
    if d is None:
        return False
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (CONFIG_NAME + ".tmp")
        tmp.write_text(
            json.dumps({"key_hold_ms": int(hold_ms), "key_gap_ms": int(gap_ms)}),
            encoding="utf-8")
        os.replace(tmp, d / CONFIG_NAME)
    except OSError:
        return False
    return True
