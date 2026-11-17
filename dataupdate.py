# -*- coding: utf-8 -*-
"""dataupdate.py — 按仓库 data_manifest.json 原子更新 JSON 数据缓存（只读 GET）。

仓库路径固定为 ``viewer3d/assets/data_manifest.json``，schema 1。仓库 URL 不由本模块
另存一份，而是从 :data:`carupdate.SOURCE_URLS` 的 cars.json 地址派生，因此 Gitee /
GitHub / jsDelivr 的维护仍只有一个来源。

典型用法（后台线程）::
    result = dataupdate.update_cache(CARS_UA, prefer_gitee=True)
    result.updated       # 本次实际替换的 cache-relative target
    result.cars          # manifest 含 cars.json 时，对应 cars_online.json 的已校验 dict

所有文件先下载并验证尺寸、SHA-256、JSON（json.gz 先解压），再写固定 .tmp。全部准备
好后才逐个 os.replace，最后提交 ``data_manifest.json`` 缓存；任一阶段失败均不提交
新 manifest，已替换文件会回滚。缓存只位于 ``%LOCALAPPDATA%\\FH6LiveryViewer``。
"""
from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import carupdate

MANIFEST_REPO_PATH = "viewer3d/assets/data_manifest.json"
MANIFEST_CACHE = "data_manifest.json"
MANIFEST_SCHEMA = 1
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class DataUpdateError(RuntimeError):
    """manifest、下载、校验或缓存提交失败。"""


class ManifestFetchError(DataUpdateError):
    """所有源均未能提供合法 manifest；调用方可回退旧 cars.json 通道。"""


class CarsLocalNewerError(DataUpdateError):
    """远端 cars.json 比当前内置/在线基表旧，保留现有缓存与内存表。"""

    def __init__(self, data: dict[str, Any]):
        super().__init__("本地车型表较新")
        self.data = data


@dataclass(frozen=True)
class FileEntry:
    path: str
    target: str
    bytes: int
    sha256: str
    kind: str


@dataclass(frozen=True)
class UpdateResult:
    source: str
    manifest: dict[str, Any]
    updated: tuple[str, ...]
    cars: dict[str, Any] | None


def source_order(prefer_gitee: bool) -> tuple[str, ...]:
    return (("gitee", "github", "jsdelivr") if prefer_gitee
            else ("github", "jsdelivr", "gitee"))


def repo_url(source: str, repo_path: str) -> str:
    """从 carupdate 的 cars.json URL 派生同仓库文件 URL。"""
    base = carupdate.SOURCE_URLS[source]
    if not base.endswith("/cars.json"):
        raise DataUpdateError(f"数据源不是 cars.json URL: {source}")
    return base[:-len("cars.json")] + urllib.parse.quote(repo_path, safe="/")


def _repo_relative(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise DataUpdateError(f"{field} 不是合法相对路径")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(
            part in ("", ".", "..") for part in path.parts):
        raise DataUpdateError(f"{field} 不是合法相对路径: {value!r}")
    return value


def _expected_target(repo_path: str) -> str:
    if repo_path == "cars.json":
        return carupdate.CARS_CACHE
    for prefix, target_prefix in (
            ("viewer3d/assets/cars/", "viewer_assets/cars/"),
            ("viewer3d/assets/vector/", "viewer_assets/vector/")):
        if repo_path.startswith(prefix) and len(repo_path) > len(prefix):
            return target_prefix + repo_path[len(prefix):]
    raise DataUpdateError(f"不支持的仓库数据路径: {repo_path}")


def manifest_entries(manifest: object) -> tuple[FileEntry, ...]:
    """严格校验 schema 1，并限制 target 只能落在约定的缓存区域。"""
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise DataUpdateError("data manifest schema 不符")
    if isinstance(manifest.get("schema"), bool):
        raise DataUpdateError("data manifest schema 不符")
    updated = manifest.get("updated")
    if not isinstance(updated, str) or not updated.strip():
        raise DataUpdateError("data manifest 缺少 updated")
    try:
        datetime.fromisoformat(updated.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataUpdateError("data manifest updated 不是 ISO 8601") from exc
    files = manifest.get("files")
    if not isinstance(files, list):
        raise DataUpdateError("data manifest 缺少 files[]")
    entries: list[FileEntry] = []
    seen_paths: set[str] = set()
    seen_targets: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise DataUpdateError("data manifest files[] 条目不是对象")
        path = _repo_relative(item.get("path"), "path")
        target = _repo_relative(item.get("target"), "target")
        expected = _expected_target(path)
        if target != expected:
            raise DataUpdateError(f"target 不符: {path} 必须为 {expected}")
        size = item.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise DataUpdateError(f"文件大小非法: {path}")
        digest = item.get("sha256")
        if not isinstance(digest, str) or not _HASH_RE.fullmatch(digest):
            raise DataUpdateError(f"SHA-256 非法: {path}")
        kind = item.get("kind")
        if kind not in ("json", "json.gz"):
            raise DataUpdateError(f"kind 非法: {path}")
        if path in seen_paths or target in seen_targets:
            raise DataUpdateError(f"重复的数据条目: {path}")
        seen_paths.add(path)
        seen_targets.add(target)
        entries.append(FileEntry(path, target, size, digest.lower(), kind))
    return tuple(entries)


def _read_url(url: str, user_agent: str, limit: int) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent or carupdate.UA_DEFAULT})
    with urllib.request.urlopen(request, timeout=carupdate.FETCH_TIMEOUT_S) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"下载内容超过声明大小 ({limit}B)")
    return raw


def fetch_manifest(user_agent: str = "", prefer_gitee: bool = True
                   ) -> tuple[str, dict[str, Any]]:
    """按既有数据源顺序获取并校验 manifest。"""
    errors: list[str] = []
    for source in source_order(prefer_gitee):
        try:
            raw = _read_url(repo_url(source, MANIFEST_REPO_PATH), user_agent,
                            MAX_MANIFEST_BYTES)
            obj = json.loads(raw.decode("utf-8"))
            manifest_entries(obj)
            if not isinstance(obj, dict):
                raise DataUpdateError("data manifest 不是对象")
            return source, obj
        except (OSError, ValueError, http.client.HTTPException, DataUpdateError) as exc:
            errors.append(f"{source}: {exc}")
    raise ManifestFetchError("; ".join(errors)[:500] or "全部数据源不可用")


def load_cached_manifest() -> dict[str, Any] | None:
    directory = carupdate.cache_dir()
    if directory is None:
        return None
    try:
        obj = json.loads((directory / MANIFEST_CACHE).read_text(encoding="utf-8"))
        manifest_entries(obj)
        return obj if isinstance(obj, dict) else None
    except (OSError, ValueError, DataUpdateError):
        return None


def target_path(directory: Path, entry: FileEntry) -> Path:
    return directory.joinpath(*entry.target.split("/"))


def _decode_and_validate(entry: FileEntry, raw: bytes, min_count: int) -> Any:
    if len(raw) != entry.bytes:
        raise ValueError(f"大小不符: {entry.path} ({len(raw)} != {entry.bytes})")
    if hashlib.sha256(raw).hexdigest() != entry.sha256:
        raise ValueError(f"SHA-256 不符: {entry.path}")
    decoded = gzip.decompress(raw) if entry.kind == "json.gz" else raw
    obj = json.loads(decoded.decode("utf-8"))
    if entry.path == "cars.json":
        return carupdate.validate(obj, min_count)
    return obj


def _check_cars_version(entry: FileEntry, obj: Any,
                        current: dict[str, Any] | None) -> None:
    if entry.path != "cars.json" or current is None or not isinstance(obj, dict):
        return
    local_dt = carupdate.data_updated_dt(current)
    remote_dt = carupdate.data_updated_dt(obj)
    if local_dt is not None and (remote_dt is None or remote_dt < local_dt):
        raise CarsLocalNewerError(obj)


def _fetch_file(entry: FileEntry, user_agent: str, prefer_gitee: bool,
                min_count: int) -> tuple[bytes, Any]:
    errors: list[str] = []
    for source in source_order(prefer_gitee):
        try:
            raw = _read_url(repo_url(source, entry.path), user_agent, entry.bytes)
            obj = _decode_and_validate(entry, raw, min_count)
            return raw, obj
        except (OSError, ValueError, EOFError, http.client.HTTPException,
                carupdate.CarUpdateError) as exc:
            errors.append(f"{source}: {exc}")
    raise DataUpdateError("; ".join(errors)[:500] or f"无法下载 {entry.path}")


def _write_temporary(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(payload)


def _atomic_write(path: Path, payload: bytes) -> None:
    _write_temporary(path, payload)
    os.replace(path.with_name(path.name + ".tmp"), path)


def _remove_temporary(path: Path) -> None:
    try:
        path.with_name(path.name + ".tmp").unlink(missing_ok=True)
    except OSError:
        pass


def update_cache(user_agent: str = "", prefer_gitee: bool = True,
                 min_count: int = carupdate.MIN_FH6,
                 cars_current: dict[str, Any] | None = None) -> UpdateResult:
    """获取 manifest，只替换本机内容与远端哈希不符的文件。

    cars.json 仍写入 carupdate.CARS_CACHE，因此 ``carupdate.load_cached()``、
    ``cache_fetched_at()`` 和启动采用规则保持兼容。manifest 未列出 cars.json 时，
    返回的 ``cars`` 为 None，不触碰既有 cars_online.json。传入 cars_current 可沿用
    旧通道的“本地 _updated 较新则不覆盖”规则，拒绝时抛 CarsLocalNewerError。
    """
    directory = carupdate.cache_dir()
    if directory is None:
        raise DataUpdateError("缓存目录不可用")
    source, manifest = fetch_manifest(user_agent, prefer_gitee)
    entries = manifest_entries(manifest)
    manifest_raw = json.dumps(manifest, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")

    changed: list[FileEntry] = []
    downloaded: dict[str, bytes] = {}
    objects: dict[str, Any] = {}
    previous: dict[str, bytes | None] = {}
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            target = target_path(directory, entry)
            try:
                old = target.read_bytes()
            except FileNotFoundError:
                old = None
            if old is not None:
                try:
                    obj = _decode_and_validate(entry, old, min_count)
                    _check_cars_version(entry, obj, cars_current)
                    objects[entry.target] = obj
                    continue
                except (OSError, ValueError, EOFError, http.client.HTTPException,
                        carupdate.CarUpdateError):
                    pass
            previous[entry.target] = old
            raw, obj = _fetch_file(entry, user_agent, prefer_gitee, min_count)
            _check_cars_version(entry, obj, cars_current)
            downloaded[entry.target] = raw
            objects[entry.target] = obj
            changed.append(entry)

        # 所有下载/解析成功后才动现有缓存；此阶段失败只会留下待下次覆盖的 .tmp。
        for entry in changed:
            _write_temporary(target_path(directory, entry), downloaded[entry.target])
    except (OSError, ValueError, EOFError, http.client.HTTPException,
            DataUpdateError, carupdate.CarUpdateError) as exc:
        for entry in changed:
            _remove_temporary(target_path(directory, entry))
        if isinstance(exc, DataUpdateError):
            raise
        raise DataUpdateError(str(exc)) from exc

    replaced: list[FileEntry] = []
    try:
        for entry in changed:
            target = target_path(directory, entry)
            os.replace(target.with_name(target.name + ".tmp"), target)
            replaced.append(entry)
        # manifest 是整批数据的提交点；其写入失败时回滚本批已替换的文件。
        _atomic_write(directory / MANIFEST_CACHE, manifest_raw)
    except OSError as exc:
        rollback_errors: list[str] = []
        for entry in reversed(replaced):
            target = target_path(directory, entry)
            old = previous.get(entry.target)
            try:
                if old is None:
                    target.unlink(missing_ok=True)
                else:
                    _atomic_write(target, old)
            except OSError as rollback_exc:
                rollback_errors.append(f"{entry.target}: {rollback_exc}")
        for entry in changed:
            _remove_temporary(target_path(directory, entry))
        detail = str(exc)
        if rollback_errors:
            detail += "; 回滚失败: " + "; ".join(rollback_errors)
        raise DataUpdateError(detail) from exc

    cars = objects.get("cars_online.json") if any(
        entry.path == "cars.json" for entry in entries) else None
    return UpdateResult(source=source, manifest=manifest,
                        updated=tuple(entry.target for entry in changed),
                        cars=cars if isinstance(cars, dict) else None)
