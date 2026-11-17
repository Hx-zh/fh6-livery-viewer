# -*- coding: utf-8 -*-
"""carlocal.py — 从本机 FH6 安装文件只读发现车型并维护独立缓存。

扫描 ``media/Cars/*.zip`` 内主 .carbin 的 Scene 头 Ordinal，以 EN.zip 的
Data_Car.str 和完整英文文案解析名称。缓存只写
``%LOCALAPPDATA%\\FH6LiveryViewer\\cars_local.json``，固定 .tmp + os.replace 原子替换。
本模块不依赖 tools/，源码与 PyInstaller 包均可直接使用。

用法（后台线程）::
    root = carlocal.resolve_game_root(appconfig.load().get("game_dir", ""))
    data, from_cache = carlocal.scan_or_load(root, force=False)  # root 为 None 时抛错
    effective = carlocal.merge_cars(current_builtin_or_online, data)

本地结果只补当前表没有的 ID，绝不覆盖内置/在线名称；没有唯一全名时使用短名并标记
pending，连短名也没有则仅报告 pending。扫描时间独立于 cars.json 的 ``_updated``。
"""
from __future__ import annotations

import json
import os
import re
import struct
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import carupdate
import flsbridge

SCHEMA = 1
CACHE_NAME = "cars_local.json"
EN_ZIP = Path("media") / "Stripped" / "StringTables" / "EN.zip"


class LocalScanError(RuntimeError):
    """本机游戏目录、车包或串表无法可靠读取。"""


def resolve_game_root(manual: str | Path | None = None) -> Path | None:
    """手动配置的有效 FH6 根优先，否则只读自动探测；绝不弹出目录选择框。"""
    if manual:
        try:
            root = flsbridge.resolve_game_root(manual)
            if root is not None and flsbridge.is_fh6_root(root):
                return root.resolve()
        except (OSError, TypeError, ValueError):
            pass
    try:
        root = flsbridge.detect_game_root()
        return root.resolve() if root is not None else None
    except (OSError, TypeError, ValueError):
        return None


def game_fingerprint(game_root: Path) -> dict[str, int]:
    """游戏数据指纹：Cars zip 数量/最新 mtime，以及 EN.zip mtime。"""
    cars_dir = game_root / "media" / "Cars"
    if not cars_dir.is_dir():
        raise LocalScanError(f"目录不存在: {cars_dir}")
    latest = 0
    count = 0
    try:
        for path in cars_dir.glob("*.zip"):
            if path.is_file():
                count += 1
                latest = max(latest, path.stat().st_mtime_ns)
        en = game_root / EN_ZIP
        en_mtime = en.stat().st_mtime_ns if en.is_file() else 0
    except OSError as exc:
        raise LocalScanError(str(exc)) from exc
    return {"zip_count": count, "latest_zip_mtime_ns": latest,
            "en_zip_mtime_ns": en_mtime}


def carbin_identity(data: bytes) -> tuple[int, str]:
    """解析 Scene 头 ``version | BuildGuid | BuildStrict | Ordinal | MediaName``。"""
    offset = 0

    def need(size: int) -> None:
        if offset + size > len(data):
            raise ValueError("carbin 头不完整")

    def u16() -> int:
        nonlocal offset
        need(2)
        value = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        return value

    def u32() -> int:
        nonlocal offset
        need(4)
        value = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        return value

    version = u16()
    if not 3 <= version <= 7:
        raise ValueError(f"不支持的 Scene 版本: {version}")
    if version >= 3:
        need(16)
        offset += 16
    if version >= 5:
        need(1)
        offset += 1
    ordinal = u32()
    need(4)
    length = struct.unpack_from("<i", data, offset)[0]
    offset += 4
    if length < 0 or length > (1 << 20):
        raise ValueError(f"MediaName 长度异常: {length}")
    need(length)
    media = data[offset:offset + length].decode("latin1")
    return ordinal, media


def scan_car_archives(game_root: Path) -> tuple[dict[int, str], list[str]]:
    """只读扫描 Cars zip；重复 Ordinal 的所有候选均不自动采用。"""
    cars_dir = game_root / "media" / "Cars"
    found: dict[int, str] = {}
    duplicates: set[int] = set()
    problems: list[str] = []
    try:
        paths = sorted(cars_dir.glob("*.zip"))
    except OSError as exc:
        raise LocalScanError(str(exc)) from exc
    if not paths:
        raise LocalScanError(f"没有车型包: {cars_dir}")
    for path in paths:
        code = path.stem
        try:
            with zipfile.ZipFile(path) as archive:
                infos = [info for info in archive.infolist()
                         if "/" not in info.filename.rstrip("/")
                         and info.filename.lower().endswith(".carbin")]
                exact = [info for info in infos
                         if info.filename.lower() == f"{code}.carbin".lower()]
                if len(exact) > 1:
                    raise ValueError("主 .carbin 重复")
                if len(exact) == 1:
                    infos = exact
                elif len(infos) != 1:
                    raise ValueError(f"无法确定主 .carbin（{len(infos)} 个）")
                info = infos[0]
                with archive.open(info) as stream:
                    header = stream.read((1 << 20) + 64)
                ordinal, media = carbin_identity(header)
        except (OSError, ValueError, struct.error, zipfile.BadZipFile,
                RuntimeError, NotImplementedError) as exc:
            problems.append(f"{code}: {exc}")
            continue
        if media.casefold() != code.casefold():
            problems.append(f"{code}: MediaName={media!r} 与文件名不符")
            continue
        if ordinal in found:
            duplicates.add(ordinal)
            problems.append(f"ID {ordinal} 重复: {found[ordinal]} / {code}")
        else:
            found[ordinal] = code
    for ordinal in duplicates:
        found.pop(ordinal, None)
    if not found:
        raise LocalScanError("没有可读取的车型 Ordinal；" + "; ".join(problems[:3]))
    return found, problems


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = (text.replace("‘", "'").replace("’", "'")
            .replace("“", '"').replace("”", '"'))
    return re.sub(r"\s+", " ", text).casefold().strip()


def _read_str_array(data: bytes, offset: int) -> list[str]:
    if offset < 0 or offset + 12 > len(data):
        raise ValueError("串表偏移越界")
    count = struct.unpack_from("<I", data, offset + 8)[0]
    entries = offset + 12
    base = entries + 8 * count
    if base > len(data):
        raise ValueError("串表索引越界")
    out: list[str] = []
    for index in range(count):
        item = entries + index * 8
        relative = struct.unpack_from("<I", data, item + 4)[0]
        start = base + relative
        if start >= len(data):
            raise ValueError("字符串偏移越界")
        end = data.index(b"\x00", start)
        out.append(data[start:end].decode("utf-8", "replace"))
    return out


def parse_str_table(data: bytes) -> tuple[list[str], list[str]]:
    """ForzaTech 串表：0x84/0x88 分别是 VALUES/KEYS 偏移。"""
    if len(data) < 0x8c:
        raise ValueError("串表头不完整")
    values_offset, keys_offset = struct.unpack_from("<II", data, 0x84)
    return (_read_str_array(data, keys_offset),
            _read_str_array(data, values_offset))


def car_entries(keys: list[str], values: list[str]) -> dict[int, tuple[str, str]]:
    """返回 ID → (DisplayName, ModelShort)。"""
    display_count = sum(key.startswith("IDS_DisplayName_") for key in keys)
    entries: dict[int, tuple[str, str]] = {}
    for index, key in enumerate(keys):
        match = re.match(r"^IDS_DisplayName_(\d+)$", key)
        if not match:
            continue
        short_index = index + display_count
        if index >= len(values) or short_index >= len(values):
            raise ValueError("Data_Car.str 键值数量不一致")
        entries[int(match.group(1))] = (values[index], values[short_index])
    if display_count == 0 or len(entries) != display_count:
        raise ValueError("Data_Car.str DisplayName 条目异常")
    return entries


def load_car_entries(en_zip: Path) -> dict[int, tuple[str, str]]:
    try:
        with zipfile.ZipFile(en_zip) as archive:
            data = archive.read("Data_Car.str")
        return car_entries(*parse_str_table(data))
    except (OSError, KeyError, ValueError, struct.error, zipfile.BadZipFile) as exc:
        raise LocalScanError(f"无法解析 Data_Car.str: {exc}") from exc


_CAPTION_PATTERNS = (
    re.compile(r"^Own the (\d{4}) (.+)$"),
    re.compile(r"^Take a photograph featuring the (\d{4}) (.+)$"),
    re.compile(r"^Forza Horizon 6 (\d{4}) (.+)$"),
)


def load_full_names(en_zip: Path) -> dict[str, tuple[str, int]]:
    """提取完整“年份 + 全名”句子；同一全名有多个年份时丢弃。"""
    years: dict[str, set[int]] = {}
    original: dict[str, str] = {}
    try:
        with zipfile.ZipFile(en_zip) as archive:
            for info in archive.infolist():
                if not info.filename.endswith(".str"):
                    continue
                try:
                    _keys, values = parse_str_table(archive.read(info))
                except (KeyError, ValueError, struct.error, IndexError):
                    continue
                for value in values:
                    for pattern in _CAPTION_PATTERNS:
                        match = pattern.match(value.strip())
                        if not match:
                            continue
                        year, name = int(match.group(1)), match.group(2).strip()
                        normalized = _norm(name)
                        if normalized:
                            years.setdefault(normalized, set()).add(year)
                            original.setdefault(normalized, name)
                        break
    except (OSError, zipfile.BadZipFile) as exc:
        raise LocalScanError(f"无法读取 EN.zip: {exc}") from exc
    return {key: (original[key], next(iter(values)))
            for key, values in years.items() if len(values) == 1}


def match_full_names(entries: dict[int, tuple[str, str]],
                     pool: dict[str, tuple[str, int]]
                     ) -> tuple[dict[int, tuple[str, int]], set[int]]:
    """以唯一最长 DisplayName 后缀 join 全名，并双向排除歧义。

    同一 DisplayName 对应多个 ID（包括 4263/4221 这类情况）或同一 ID 对应多个
    不同全名时全部标为 ambiguous，不让后出现的文案静默覆盖先出现者。
    """
    display = {car_id: _norm(pair[0]) for car_id, pair in entries.items()}
    display = {car_id: name for car_id, name in display.items() if name}
    candidates: dict[int, list[tuple[str, int]]] = {}
    ambiguous: set[int] = set()
    for normalized, (name, year) in pool.items():
        hits = [car_id for car_id, short in display.items()
                if normalized == short or normalized.endswith(" " + short)]
        if not hits:
            continue
        longest = max(len(display[car_id]) for car_id in hits)
        best = [car_id for car_id in hits if len(display[car_id]) == longest]
        if len(best) != 1:
            ambiguous.update(best)
            continue
        candidates.setdefault(best[0], []).append((name, year))
    resolved: dict[int, tuple[str, int]] = {}
    for car_id, values in candidates.items():
        distinct = {(_norm(name), year) for name, year in values}
        if car_id in ambiguous or len(distinct) != 1:
            ambiguous.add(car_id)
            continue
        resolved[car_id] = values[0]
    return resolved, ambiguous


def scan(game_root: Path, fingerprint: dict[str, int] | None = None) -> dict[str, Any]:
    """执行一次完整只读扫描并返回可 JSON 序列化的缓存对象。"""
    root = game_root.resolve()
    fp = game_fingerprint(root) if fingerprint is None else fingerprint
    en_zip = root / EN_ZIP
    if not en_zip.is_file():
        raise LocalScanError(f"缺少串表: {en_zip}")
    models, problems = scan_car_archives(root)
    entries = load_car_entries(en_zip)
    full_names, ambiguous = match_full_names(entries, load_full_names(en_zip))
    cars: dict[str, dict[str, Any]] = {}
    for car_id, model in sorted(models.items()):
        display, short = entries.get(car_id, ("", ""))
        if car_id in full_names:
            name, year = full_names[car_id]
            value, pending, source = f"{year} {name}", False, "caption"
        else:
            value = re.sub(r"\s+", " ", (short or display).strip())
            pending, source = True, "short" if value else "pending"
        cars[str(car_id)] = {"model": model, "name": value,
                             "pending": pending, "name_source": source}
    return {
        "schema": SCHEMA,
        "scanned_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "game_dir": str(root),
        "fingerprint": fp,
        "cars": cars,
        "pending": [int(key) for key, value in cars.items() if value["pending"]],
        "ambiguous": sorted(ambiguous),
        "problems": problems,
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(payload)
    os.replace(temporary, path)


def save_cache(data: dict[str, Any]) -> None:
    directory = carupdate.cache_dir()
    if directory is None:
        raise LocalScanError("缓存目录不可用")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        _atomic_write(directory / CACHE_NAME, raw)
    except (OSError, TypeError, ValueError) as exc:
        raise LocalScanError(f"写入本地车型缓存失败: {exc}") from exc


def load_cached() -> dict[str, Any] | None:
    """读取并做结构校验；损坏或旧 schema 的缓存一律忽略。"""
    directory = carupdate.cache_dir()
    if directory is None:
        return None
    try:
        obj = json.loads((directory / CACHE_NAME).read_text(encoding="utf-8"))
        if not isinstance(obj, dict) or obj.get("schema") != SCHEMA:
            return None
        if not isinstance(obj.get("scanned_at"), str) or not isinstance(obj.get("game_dir"), str):
            return None
        fingerprint = obj.get("fingerprint")
        if not isinstance(fingerprint, dict):
            return None
        for key in ("zip_count", "latest_zip_mtime_ns", "en_zip_mtime_ns"):
            value = fingerprint.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return None
        cars = obj.get("cars")
        if not isinstance(cars, dict):
            return None
        for key, entry in cars.items():
            if not isinstance(key, str) or not key.isdigit() or not isinstance(entry, dict):
                return None
            if (not isinstance(entry.get("model"), str)
                    or not isinstance(entry.get("name"), str)
                    or not isinstance(entry.get("pending"), bool)
                    or entry.get("name_source") not in ("caption", "short", "pending")):
                return None
        return obj
    except (OSError, ValueError, TypeError):
        return None


def scan_or_load(game_root: Path | None, force: bool = False
                 ) -> tuple[dict[str, Any], bool]:
    """指纹未变时复用缓存；否则完整扫描并原子写缓存。返回 (数据, 是否缓存命中)。"""
    if game_root is None:
        raise LocalScanError("未找到 FH6 游戏目录")
    root = game_root.resolve()
    fingerprint = game_fingerprint(root)
    cached = load_cached()
    if not force and cached is not None:
        try:
            same_root = os.path.normcase(cached["game_dir"]) == os.path.normcase(str(root))
        except (KeyError, TypeError):
            same_root = False
        if same_root and cached.get("fingerprint") == fingerprint:
            return cached, True
    data = scan(root, fingerprint)
    if game_fingerprint(root) != fingerprint:
        raise LocalScanError("游戏文件在扫描期间发生变化，请重新扫描")
    save_cache(data)
    return data, False


def merge_cars(base: dict[str, Any], local: dict[str, Any] | None) -> dict[str, Any]:
    """把本地名称仅补到 base 缺失的 ID；保留 base 的 ``_updated`` 与所有现值。"""
    if local is None or not isinstance(base.get("fh6"), dict):
        return base
    cars = local.get("cars")
    if not isinstance(cars, dict):
        return base
    table = dict(base["fh6"])
    changed = False
    for key, entry in cars.items():
        if key in table or not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            table[key] = name
            changed = True
    if not changed:
        return base
    merged = dict(base)
    merged["fh6"] = table
    return merged


def pending_ids(base: dict[str, Any], local: dict[str, Any] | None) -> list[int]:
    """相对内置/在线基表仍为新增且未获得唯一全名的 ID。"""
    if local is None or not isinstance(base.get("fh6"), dict):
        return []
    cars = local.get("cars")
    if not isinstance(cars, dict):
        return []
    table = base["fh6"]
    return sorted(int(key) for key, entry in cars.items()
                  if key not in table and isinstance(entry, dict) and entry.get("pending") is True)


def clear_cache() -> bool:
    """显式“恢复内置数据”时清除本地缓存，不触碰游戏文件。"""
    directory = carupdate.cache_dir()
    if directory is None:
        return False
    try:
        (directory / CACHE_NAME).unlink(missing_ok=True)
        (directory / (CACHE_NAME + ".tmp")).unlink(missing_ok=True)
    except OSError:
        return False
    return True
