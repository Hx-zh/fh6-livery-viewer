# -*- coding: utf-8 -*-
"""
fh6save.py — 极限竞速:地平线 Steam 版存档涂装/纹饰/调校解析库

格式依据(实测 + 社区逆向):
- Steam 存档位于 <Steam>/userdata/<steam用户ID>/<appid>/remote/ 下,
  FH4 为平铺文件, FH5/FH6 多为 remote/<数字文件夹>/ 再平铺。
- 每个"条目"由同名的几个分片文件组成:
    <类型>_<车型ID>_<时间戳yyyyMMddhhmmss>.header      元数据(名称/作者/日期/层数)
    <类型>_<车型ID>_<时间戳>.C_livery / .C_group / .Data   数据体(zlib 压缩)
    <类型>_<车型ID>_<时间戳>.bigThumb.png / .smallThumb.png / .Thumb.png  预览图
- header 二进制布局(与 Arstz/FH6_livery_unlocker 一致, 已用本机 FH4/FH5 文件验证):
    u32 version | u32 名称长度 | UTF-16LE 名称 | u32 描述长度(0=未分享) | UTF-16LE 描述
    | u16 年 | u8 月 | ... | [20:24] 作者标签1 | [24:26] 作者标签2 | u32 作者名长度 | UTF-16LE 作者名 ...
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- 游戏档案

GAMES = {
    "fh6": {"appid": 2483190, "title": "极限竞速:地平线 6"},
    "fh5": {"appid": 1551360, "title": "极限竞速:地平线 5"},
    "fh4": {"appid": 1293830, "title": "极限竞速:地平线 4"},
}

# 文件名类型前缀 → 中文名
ITEM_TYPES = {
    "Livery": "涂装",
    "BaseLivery": "基础涂装",
    "SoulBoundLivery": "绑定涂装",
    "LayerGroup": "纹饰组",
    "VinylGroup": "纹饰组",
    "Tuning": "调校",
    "GarageThumbnail": "车库缩略图",
    "Photo": "照片",
}

# <类型>_<车型ID>[_m0]_<14位时间戳>[.<分片>]
# Steam 版为平铺文件(带分片后缀), 商店版(pgs)为同名文件夹(无后缀)
ITEM_RE = re.compile(r"^([A-Za-z]+)_(\d{3,6})(?:_m\d+)?_(\d{14})(?:\.(.+))?$")


# ---------------------------------------------------------------- 数据结构

@dataclass
class SaveItem:
    game: str                 # "fh6" / "fh5" / "fh4"
    steam_user: str           # userdata 用户 ID 或 pgs 目录名
    folder: Path              # 条目所在目录
    base: str                 # 共同前缀, 如 Livery_2182_20230820011901
    itype: str                # 原始类型前缀, 如 Livery
    car_id: int               # 车型 ID (来自文件名)
    ts: datetime | None       # 文件名里的时间戳
    is_dir: bool = False      # True = pgs 目录型条目, False = Steam 平铺文件
    files: dict = field(default_factory=dict)   # 分片名 -> 完整路径
    # 以下由 header 解析填充
    name: str = ""
    desc: str = ""
    creator: str = ""
    published: bool = False
    layer_count: int = 0       # 仅 FH6(header v7)已分享条目可靠
    header_car_id: int = 0     # header 内嵌的车型 ID (与文件名交叉校验用)
    header_ok: bool = False

    @property
    def type_cn(self) -> str:
        return ITEM_TYPES.get(self.itype, self.itype)

    @property
    def total_size(self) -> int:
        total = 0
        for p in self.files.values():
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
        return total

    @property
    def thumb_small(self) -> Path | None:
        for k in ("smallThumb.png", "smallThumb.webp", "Thumb.png", "Thumb.webp", "thumb"):
            if k in self.files:
                return self.files[k]
        return None

    @property
    def thumb_big(self) -> Path | None:
        for k in ("bigThumb.png", "bigThumb.webp", "Thumb.png", "Thumb.webp",
                  "image", "thumb"):
            if k in self.files:
                return self.files[k]
        return None

    @property
    def has_data(self) -> bool:
        return any(k in self.files for k in ("C_livery", "C_group", "Data"))


# ---------------------------------------------------------------- header 解析

def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def parse_header(data: bytes) -> dict:
    """解析 .header 文件。解析失败抛 ValueError。"""
    if len(data) < 12:
        raise ValueError("header 太短")
    nlen = _u32(data, 4)
    if nlen == 0 or nlen > 256 or 8 + nlen * 2 > len(data):
        raise ValueError(f"名称长度异常: {nlen}")
    name = data[8:8 + nlen * 2].decode("utf-16-le", errors="replace")
    pos = 8 + nlen * 2

    next_val = _u32(data, pos)
    published = False
    desc = ""
    if next_val == 0:
        pos += 4
    else:
        dlen = next_val
        if dlen > 4096 or pos + 4 + dlen * 2 > len(data):
            raise ValueError(f"描述长度异常: {dlen}")
        desc = data[pos + 4:pos + 4 + dlen * 2].decode("utf-16-le", errors="replace")
        pos += 4 + dlen * 2
        published = True

    s2 = data[pos:]
    if len(s2) < 32:
        raise ValueError("s2 段太短")
    year = _u16(s2, 0)
    month = s2[2]
    clen = _u32(s2, 28)
    creator = ""
    if 0 < clen <= 64 and 32 + clen * 2 <= len(s2):
        creator = s2[32:32 + clen * 2].decode("utf-16-le", errors="replace")

    # 作者名之后的 s3 段。布局实测(用本机 FH4/FH5/FH6 文件验证):
    #   FH6 (v7) 已分享: u32 flag=1 | 8B 保留 | u32 标志 | 16B GUID | 01 02 标记
    #             | s3[37:41] 图层数 | s3[41:45] 车型ID | 16B GUID
    #   FH6 (v7) 草稿/基础涂装: 01 02 开头, s3[13:17] 车型ID
    #   FH4/FH5 (v4/v6): 同区域字段值 = 文件名里的车型 ID (三种变体偏移见下)
    version = _u32(data, 0)
    layer_count = 0
    header_car_id = 0
    cend = pos + 32 + clen * 2
    while cend < len(data) and data[cend] == 0:
        cend += 1
    s3 = data[cend:]
    try:
        if version >= 7:
            if len(s3) >= 45 and _u32(s3, 0) == 1:
                layer_count = _u32(s3, 37)
                header_car_id = _u32(s3, 41)
            elif len(s3) >= 17 and s3[:2] == b"\x01\x02":
                header_car_id = _u32(s3, 13)
        else:
            if len(s3) >= 41 and _u32(s3, 0) == 1:             # published_standard
                header_car_id = _u32(s3, 37)
            elif len(s3) >= 16 and _u16(s3, 0) != 0x0201:      # hybrid_published
                hdr = 16
                if len(s3) >= hdr + 13 and s3[hdr:hdr + 2] == b"\x01\x02":
                    header_car_id = _u32(s3, hdr + 9)
            elif len(s3) >= 13:                                # draft_standard
                header_car_id = _u32(s3, 9)
    except struct.error:
        pass
    if header_car_id > 100000:   # 防御异常值
        header_car_id = 0
    if layer_count > 200000:
        layer_count = 0

    return {
        "name": name, "desc": desc, "published": published,
        "year": year, "month": month, "creator": creator,
        "layer_count": layer_count, "header_car_id": header_car_id,
        "version": version, "nlen": nlen,
    }


# ---------------------------------------------------------------- 存档定位

def find_steam_path() -> Path | None:
    """先查注册表, 再试常见安装路径。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            p = Path(winreg.QueryValueEx(k, "SteamPath")[0])
            if (p / "userdata").is_dir():
                return p
    except OSError:
        pass
    for c in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
              r"D:\Steam", r"E:\Steam", r"D:\SteamLibrary", r"E:\SteamLibrary"):
        p = Path(c)
        if (p / "userdata").is_dir():
            return p
    return None


# 微软商店/Xbox 版(pgs)存档根目录候选
PGS_ROOTS = [rf"{d}:\XboxGames\GameSave\pgs" for d in "CDEFGH"]


def find_pgs_saves() -> list[dict]:
    """扫描 pgs 布局(ContainersRoot)的 FH6 存档。"""
    found = []
    seen = set()
    for root_str in PGS_ROOTS:
        root = Path(root_str)
        if not root.is_dir():
            continue
        for u in sorted(root.glob("u_*")):
            if not u.is_dir():
                continue
            subs = [s for s in u.iterdir() if s.is_dir()]
            current = u / "current"
            if current.exists():
                # current junction 指向活跃快照; 旧快照(100→101 滚动)不再列出
                subs = [current]
            for sub in subs:
                cr = sub / "ContainersRoot"
                if not cr.is_dir():
                    continue
                # junction/软链归一后去重
                key = os.path.realpath(cr).lower()
                if key in seen:
                    continue
                seen.add(key)
                if any(ITEM_RE.match(x.name) for x in cr.iterdir()):
                    found.append({"game": "fh6", "steam_user": u.name,
                                  "dir": Path(os.path.realpath(cr)), "source": "pgs"})
    return found


def find_saves(steam: Path | None = None) -> list[dict]:
    """扫描所有本机存在的地平线存档, 返回 [{game, steam_user, dir, source}]。"""
    steam = steam or find_steam_path()
    found = []
    if steam:
        userdata = steam / "userdata"
        if userdata.is_dir():
            for user_dir in sorted(userdata.iterdir()):
                if not user_dir.is_dir() or not user_dir.name.isdigit():
                    continue
                for gkey, g in GAMES.items():
                    remote = user_dir / str(g["appid"]) / "remote"
                    if not remote.is_dir():
                        continue
                    # FH4 平铺在 remote/, FH5/FH6 多在 remote/<数字>/ 下; 两层都找
                    candidates = [remote] + [d for d in remote.iterdir() if d.is_dir()]
                    for c in candidates:
                        if any(ITEM_RE.match(f.name) for f in c.iterdir() if f.is_file()):
                            found.append({"game": gkey, "steam_user": user_dir.name,
                                          "dir": c, "source": "steam"})
                            break
                    else:
                        # 目录存在但还没有条目文件(如刚同步) —— 也列出来
                        found.append({"game": gkey, "steam_user": user_dir.name,
                                      "dir": remote, "source": "steam"})
    found.extend(find_pgs_saves())
    return found


# ---------------------------------------------------------------- 扫描

def scan_folder(game: str, steam_user: str, folder: Path) -> list[SaveItem]:
    """扫描一个目录, 按共同前缀聚合成条目并解析 header。
    同时支持 Steam 平铺文件(<前缀>.<分片>)和 pgs 目录型条目(<前缀>/ 内含分片)。"""
    items: dict[str, SaveItem] = {}
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []

    def _get_item(itype: str, car_id: str, ts: str, base: str, is_dir: bool) -> SaveItem:
        if base not in items:
            try:
                # 文件名时间戳是 UTC, 转成本地时间显示
                dt = (datetime.strptime(ts, "%Y%m%d%H%M%S")
                      .replace(tzinfo=timezone.utc).astimezone())
            except ValueError:
                dt = None
            items[base] = SaveItem(game=game, steam_user=steam_user, folder=folder,
                                   base=base, itype=itype, car_id=int(car_id),
                                   ts=dt, is_dir=is_dir)
        return items[base]

    for f in entries:
        m = ITEM_RE.match(f.name)
        if not m:
            continue
        itype, car_id, ts, part = m.group(1), m.group(2), m.group(3), m.group(4)
        if f.is_dir():
            # pgs 目录型条目: 分片是文件夹内的文件
            it = _get_item(itype, car_id, ts, f.name, True)
            try:
                for inner in f.iterdir():
                    if inner.is_file():
                        it.files[inner.name] = inner
            except OSError:
                pass
        elif f.is_file():
            base = f.name[:-(len(part) + 1)] if part else f.name
            it = _get_item(itype, car_id, ts, base, False)
            it.files[part or ""] = f

    for it in items.values():
        hdr = it.files.get("header")
        if hdr:
            try:
                meta = parse_header(hdr.read_bytes())
                it.name = meta["name"]
                it.desc = meta["desc"]
                it.creator = meta["creator"]
                it.published = meta["published"]
                it.layer_count = meta["layer_count"]
                it.header_car_id = meta["header_car_id"]
                it.header_ok = True
            except (ValueError, OSError, struct.error):
                it.header_ok = False
    return sorted(items.values(), key=lambda x: (x.ts or datetime.min), reverse=True)


# ---------------------------------------------------------------- 车辆名表

class CarTable:
    """车型 ID → 名称 对照表。文件不存在时自动创建空表。"""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict = {}
        self.load()

    def load(self):
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def save(self):
        self.path.write_text(json.dumps(self._data, ensure_ascii=False,
                                        indent=2, sort_keys=True), encoding="utf-8")

    def name(self, game: str, car_id: int) -> str:
        return self._data.get(game, {}).get(str(car_id), "")

    def set_name(self, game: str, car_id: int, name: str):
        self._data.setdefault(game, {})[str(car_id)] = name
        self.save()

    def known_count(self, game: str) -> int:
        return len(self._data.get(game, {}))


# ---------------------------------------------------------------- 操作(只读)

class SaveOps:
    """查看器唯一保留的写盘操作: 把存档目录备份成 zip(不改动存档本身)。"""

    def __init__(self, backup_root: Path):
        self.backup_root = backup_root

    def _stamp(self) -> str:
        return time.strftime("%Y%m%d_%H%M%S")

    def backup_all(self, folder: Path) -> Path:
        """把整个存档目录打成 zip。"""
        self.backup_root.mkdir(parents=True, exist_ok=True)
        out = self.backup_root / f"save_backup_{self._stamp()}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in folder.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(folder))
        return out


# ---------------------------------------------------------------- CLI 自检

if __name__ == "__main__":
    steam = find_steam_path()
    print(f"Steam 路径: {steam}")
    for s in find_saves(steam):
        g = GAMES[s["game"]]
        items = scan_folder(s["game"], s["steam_user"], s["dir"])
        src = {"steam": "Steam", "pgs": "商店版/pgs"}.get(s.get("source"), "?")
        print(f"\n[{g['title']} | {src}] 用户 {s['steam_user']}  目录 {s['dir']}  条目 {len(items)}")
        for it in items[:10]:
            car_mark = f"车ID {it.car_id:<5}"
            if it.header_car_id and it.header_car_id != it.car_id:
                car_mark += f"(header:{it.header_car_id}?!)"
            layers = f"层数:{it.layer_count:<6}" if it.layer_count else ""
            print(f"  {it.type_cn:<6} {car_mark} {it.name or '(未解析)':<24} "
                  f"作者:{it.creator or '?':<12} {layers}"
                  f"{'已分享' if it.published else '未分享'} {it.base}")
        if len(items) > 10:
            print(f"  ... 共 {len(items)} 条")
