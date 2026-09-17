# -*- coding: utf-8 -*-
"""
carupdate.py — cars.json 车型名表在线更新(只读 GET, 无遥测)

数据源 = 双端仓库 main 分支的 cars.json(与程序内嵌文件同源同格式, 不加版本壳——
下载内容与当前表不同即更新):
  ① Gitee raw(国内快, 302 → raw.giteeusercontent.com, 实测 ~0.7s)
  ② GitHub raw(海外用户快; 大陆时通时不通)
  ③ fastly.jsdelivr.net(GitHub 仓库的 CDN 镜像, 大陆多数可用; 分支引用缓存有滞后)
按当前界面语言排序: 简中 Gitee 优先, 其余 GitHub 优先; 逐源尝试, 单源 6s 超时。

稳定性口径:
  - 下载内容严格校验(大小/JSON 结构/fh6 条目数下限/键值形态), 不合格即弃用换下源;
  - 全部失败抛 CarUpdateError, 调用方静默保持现有数据(内嵌表永远兜底);
  - 缓存写 %LOCALAPPDATA%\\FH6LiveryViewer\\(固定名 tmp + os.replace 原子覆盖,
    绝不写 exe 旁); 过期缓存仅由 app 的启动采用规则忽略、不删除——下次成功下载
    原地覆盖, 崩溃残留的 tmp 至多一个且被下次写入自然消化(固定名), 永不积攒;
  - 自动检查 24h 一次(状态文件 checked_at 节流), 手动检查不受限。

用法(app.py):
    carupdate.init_cache_dir()                    # 启动时准备缓存目录(失败则停用)
    cached, state = carupdate.load_cached()       # 读缓存 + 状态
    src, data = carupdate.fetch(ua, prefer_gitee, min_count)   # 工作线程里调
    carupdate.save_cache(data, src)               # 主线程写缓存 + 状态

URL 常量同时供 release_build.py 的 --data-only 发布校验复用(单一来源,
改仓库地址时只改这里)。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

SOURCE_URLS: dict[str, str] = {
    "gitee": "https://gitee.com/hx_zh/fh6-livery-viewer/raw/main/cars.json",
    "github": "https://raw.githubusercontent.com/Hx-zh/fh6-livery-viewer/main/cars.json",
    "jsdelivr": "https://fastly.jsdelivr.net/gh/hx-zh/fh6-livery-viewer@main/cars.json",
}

CACHE_DIR_NAME = "FH6LiveryViewer"
CARS_CACHE = "cars_online.json"
STATE_CACHE = "cars_state.json"

MIN_BYTES, MAX_BYTES = 1024, 2 * 1024 * 1024   # 下载/缓存大小合法区间(防错误页/截断)
MIN_FH6 = 600            # fh6 条目数下限(当前 671, 车表只增不减)
FETCH_TIMEOUT_S = 6      # 单源超时(三源串行最坏 ~18s, 全程后台线程不碰 UI)
CHECK_INTERVAL_S = 24 * 3600   # 自动检查最小间隔(状态文件节流)
UA_DEFAULT = "FH6LiveryViewer"

_STATE_DEFAULT: dict[str, object] = {
    "checked_at": 0.0,   # 上次检查时刻(epoch, 节流用; 检查失败也计入)
    "fetched_at": 0.0,   # 上次成功下载时刻(启动采用规则「缓存 vs 新 exe」比较用)
    "source": "",        # 上次成功下载来源名
    "count": 0,          # 上次成功下载的 fh6 条目数
    "auto": True,        # 每日自动检查开关(持久化; 唯一落盘设置)
}

_cache_dir: Path | None = None
_init_done = False


class CarUpdateError(RuntimeError):
    """在线检查失败(全部数据源不可用/内容不合法), message 汇总各源原因。"""


def init_cache_dir() -> bool:
    """解析并准备缓存目录(%LOCALAPPDATA%\\FH6LiveryViewer)。
    不可用(无 LOCALAPPDATA/建目录失败)返回 False, 缓存与状态功能整体停用(静默)。"""
    global _cache_dir, _init_done
    _init_done = True
    raw = os.environ.get("LOCALAPPDATA", "").strip()
    if not raw:
        return False
    d = Path(raw) / CACHE_DIR_NAME
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        _cache_dir = None
        return False
    _cache_dir = d
    return True


def cache_dir() -> Path | None:
    if not _init_done:
        init_cache_dir()
    return _cache_dir


def fh6_count(data: object) -> int:
    """数据的 fh6 表条目数(非 dict/无 fh6 返回 0)。"""
    if not isinstance(data, dict):
        return 0
    t = data.get("fh6")
    return len(t) if isinstance(t, dict) else 0


def validate(obj: object, min_count: int) -> dict:
    """校验下载/缓存的数据结构, 不合法抛 CarUpdateError。
    口径: 顶层 dict 含 fh6 dict; 条目数 ≥ min_count; 键纯数字、值非空串。"""
    if not isinstance(obj, dict) or not isinstance(obj.get("fh6"), dict):
        raise CarUpdateError("结构不符(缺少 fh6 表)")
    t = obj["fh6"]
    if len(t) < min_count:
        raise CarUpdateError(f"fh6 条目过少({len(t)} < {min_count})")
    for k, v in t.items():
        if not (isinstance(k, str) and k.isdigit()) or not (isinstance(v, str) and v):
            raise CarUpdateError(f"条目形态异常({k!r})")
    return obj


def fetch(user_agent: str = "", prefer_gitee: bool = True,
          min_count: int = MIN_FH6) -> tuple[str, dict]:
    """按序尝试各数据源, 返回 (源名, 数据dict); 全部失败抛 CarUpdateError。
    只在工作线程调用(阻塞网络 IO); 单源超时 FETCH_TIMEOUT_S。"""
    order = (["gitee", "github", "jsdelivr"] if prefer_gitee
             else ["github", "jsdelivr", "gitee"])
    errs: list[str] = []
    for name in order:
        try:
            req = urllib.request.Request(
                SOURCE_URLS[name],
                headers={"User-Agent": user_agent or UA_DEFAULT})
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as r:
                raw = r.read()
            if not (MIN_BYTES <= len(raw) <= MAX_BYTES):
                raise ValueError(f"大小异常 {len(raw)}B")
            obj = json.loads(raw.decode("utf-8"))
            return name, validate(obj, min_count)
        except (OSError, ValueError) as e:      # URLError/HTTPError ⊂ OSError
            errs.append(f"{name}: {e}")
    raise CarUpdateError("; ".join(errs)[:200] or "全部数据源不可用")


def state() -> dict:
    """读取状态文件(缺省/损坏回默认值; 不抛异常)。"""
    d = cache_dir()
    if d is not None:
        try:
            obj = json.loads((d / STATE_CACHE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            obj = None
        if isinstance(obj, dict):
            return {k: obj.get(k, v) for k, v in _STATE_DEFAULT.items()}
    return dict(_STATE_DEFAULT)


def _atomic_write(path: Path, text: str) -> None:
    """固定名 tmp + os.replace: 崩溃孤儿 tmp 至多一个, 被下次写入自然覆盖。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _save_state(st: dict) -> None:
    d = cache_dir()
    if d is None:
        return
    try:
        d.mkdir(parents=True, exist_ok=True)
        _atomic_write(d / STATE_CACHE, json.dumps(st, ensure_ascii=False))
    except OSError:
        pass                              # 状态写失败只损失节流/展示, 不影响功能


def mark_checked() -> None:
    """记录一次检查(无论成败)——自动检查的 24h 节流基准。"""
    st = state()
    st["checked_at"] = time.time()
    _save_state(st)


def set_auto(value: bool) -> None:
    st = state()
    st["auto"] = bool(value)
    _save_state(st)


def save_cache(data: dict, source: str) -> bool:
    """写入在线缓存 + 更新状态(失败返回 False, 调用方继续用内存数据)。"""
    d = cache_dir()
    if d is None:
        return False
    try:
        d.mkdir(parents=True, exist_ok=True)
        _atomic_write(d / CARS_CACHE, json.dumps(data, ensure_ascii=False))
    except OSError:
        return False
    st = state()
    st["fetched_at"] = time.time()
    st["source"] = source
    st["count"] = fh6_count(data)
    _save_state(st)
    return True


def load_cached() -> tuple[dict | None, dict]:
    """读取在线缓存, 返回 (数据|None, 状态)。缓存缺失/损坏/不合法 → 数据为 None。"""
    st = state()
    d = cache_dir()
    if d is None:
        return None, st
    try:
        raw = (d / CARS_CACHE).read_bytes()
        if not (MIN_BYTES <= len(raw) <= MAX_BYTES):
            return None, st
        obj = json.loads(raw.decode("utf-8"))
        return validate(obj, MIN_FH6), st
    except (OSError, ValueError, CarUpdateError):
        return None, st


def clear_cache() -> bool:
    """「恢复内置数据」用: 删缓存数据文件, 状态只清 fetch 相关
    (checked_at/auto 保留——节流与开关不受影响)。"""
    d = cache_dir()
    if d is None:
        return False
    try:
        (d / CARS_CACHE).unlink(missing_ok=True)
        (d / (CARS_CACHE + ".tmp")).unlink(missing_ok=True)
    except OSError:
        return False
    st = state()
    st["fetched_at"] = 0.0
    st["source"] = ""
    st["count"] = 0
    _save_state(st)
    return True
