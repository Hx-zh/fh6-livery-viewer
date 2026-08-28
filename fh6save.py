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

import concurrent.futures
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
    ts: datetime | None       # 文件名里的时间戳(≈作者创作时间)
    mtime: float | None = None   # 分片文件 mtime 最大值(≈玩家下载落盘时间); 字段级重复规则用
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

def _fill_item(it: SaveItem):
    """scan_folder 尾部处理的单条目版: mtime 采集(各分片 mtime 最大值
    ≈ 玩家下载落盘时间, 字段级重复规则用) + header 解析。解析失败只标 header_ok=False。"""
    mt = 0.0
    for p in it.files.values():
        try:
            mt = max(mt, p.stat().st_mtime)
        except OSError:
            pass
    it.mtime = mt or None
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


def _collect_entries(game: str, steam_user: str, folder: Path, entries: list,
                     items: dict[str, SaveItem], only_bases: set | None = None):
    """scan_folder/scan_folder_incremental 共用的目录项聚合: 按共同前缀把分片
    归入 SaveItem(Steam 平铺文件 <前缀>.<分片> 和 pgs 目录型条目 <前缀>/ 内含分片)。
    only_bases 给定时只处理这些 base(增量扫描用)。"""
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
            if only_bases is not None and f.name not in only_bases:
                continue
            it = _get_item(itype, car_id, ts, f.name, True)
            try:
                for inner in f.iterdir():
                    if inner.is_file():
                        it.files[inner.name] = inner
            except OSError:
                pass
        elif f.is_file():
            base = f.name[:-(len(part) + 1)] if part else f.name
            if only_bases is not None and base not in only_bases:
                continue
            it = _get_item(itype, car_id, ts, base, False)
            it.files[part or ""] = f


def _sort_items(items: dict[str, SaveItem]) -> list[SaveItem]:
    return sorted(items.values(), key=lambda x: (x.ts or datetime.min), reverse=True)


def scan_folder(game: str, steam_user: str, folder: Path) -> list[SaveItem]:
    """扫描一个目录, 按共同前缀聚合成条目并解析 header。
    同时支持 Steam 平铺文件(<前缀>.<分片>)和 pgs 目录型条目(<前缀>/ 内含分片)。"""
    items: dict[str, SaveItem] = {}
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    _collect_entries(game, steam_user, folder, entries, items)
    for it in items.values():
        _fill_item(it)
    return _sort_items(items)


def save_signature(folder: Path) -> dict[str, tuple] | None:
    """存档目录轻量签名(自动刷新轮询用): {条目 base: (最大 mtime_ns, 总 size)}。
    只收 ITEM_RE 匹配的条目(与 scan_folder 口径一致); 目录型条目聚合内部文件,
    平铺文件型条目把各分片聚合到同一 base。os.scandir 的 DirEntry.stat 在
    Windows 上走目录项缓存, 千余条目仅几十毫秒。
    目录本身不可读返回 None(区别于「可读但空」), 调用方应跳过本轮。"""
    sig: dict[str, tuple] = {}
    try:
        with os.scandir(folder) as it:
            entries = list(it)
    except OSError:
        return None
    for e in entries:
        m = ITEM_RE.match(e.name)
        if not m:
            continue
        part = m.group(4)
        try:
            if e.is_dir():
                base, mt, size = e.name, 0, 0
                try:
                    with os.scandir(e.path) as inner:
                        for ie in inner:
                            if not ie.is_file():
                                continue
                            st = ie.stat()
                            mt = max(mt, st.st_mtime_ns)
                            size += st.st_size
                except OSError:
                    continue
            elif e.is_file():
                base = e.name[:-(len(part) + 1)] if part else e.name
                st = e.stat()
                mt, size = st.st_mtime_ns, st.st_size
            else:
                continue
        except OSError:
            continue
        old = sig.get(base)
        sig[base] = (mt, size) if old is None else (max(old[0], mt), old[1] + size)
    return sig


def scan_folder_incremental(game: str, steam_user: str, folder: Path,
                            old_items: list[SaveItem],
                            changed_bases: set) -> list[SaveItem]:
    """scan_folder 的增量版(自动刷新用): changed_bases(新增+有变化)之外的条目
    直接复用旧 SaveItem 对象——对象身份不变, app 侧的缩略图缓存/选中状态随之存活;
    changed 条目重新聚合并解析 header; 磁盘上已消失的条目自动剔除(删除场景,
    无需调用方传入)。返回排序契约与 scan_folder 一致。"""
    try:
        entries = list(folder.iterdir())
    except OSError:
        entries = []
    present: set = set()
    for f in entries:
        m = ITEM_RE.match(f.name)
        if not m:
            continue
        part = m.group(4)
        present.add(f.name if f.is_dir()
                    else (f.name[:-(len(part) + 1)] if part else f.name))
    items = {it.base: it for it in old_items
             if it.base in present and it.base not in changed_bases}
    fresh: dict[str, SaveItem] = {}
    _collect_entries(game, steam_user, folder, entries, fresh,
                     only_bases=changed_bases)
    for it in fresh.values():
        _fill_item(it)
    items.update(fresh)
    return _sort_items(items)


def game_layout(items: list[SaveItem]) -> tuple[int, dict[str, tuple[int, int]]]:
    """计算每个涂装在游戏内「我的涂装」网格中的布局。

    与游戏内排列一致(参照 FH6存档涂装解析器 的实测结论):
    仅 Livery 条目, 按车型 ID 升序分组, 同车型内按条目名(时间戳)升序,
    每列 2 个(行号 = 列内序号), 行列均 1 起。
    返回 (总列数, base -> (行, 列)); 总列数 = ceil(Livery 数 / 2)。
    """
    liveries = sorted((it for it in items if it.itype == "Livery"),
                      key=lambda it: (it.car_id, it.base))
    layout = {it.base: (i % 2 + 1, i // 2 + 1) for i, it in enumerate(liveries)}
    return (len(liveries) + 1) // 2, layout


def game_position_map(items: list[SaveItem]) -> dict[str, str]:
    """每个涂装在「我的涂装」列表中的位置字符串 "N行M列"(布局规则见 game_layout)。"""
    _total, layout = game_layout(items)
    return {base: f"{x}行{y}列" for base, (x, y) in layout.items()}


def locate_keys(x: int, y: int, total: int) -> list[tuple[str, int]]:
    """从 1行1列 到 (x行y列) 的最短方向键序列, 返回 [(方向, 次数)](先水平后垂直)。

    水平取 min(y-1, total-y+1): y-1 更短(或相等)则向右, 否则向左(利用首尾环绕);
    垂直固定向下 x-1 次。已在原位(1行1列)时返回空列表。
    """
    keys: list[tuple[str, int]] = []
    if y - 1 <= total - y + 1:
        if y > 1:
            keys.append(("→", y - 1))
    else:
        keys.append(("←", total - y + 1))
    if x > 1:
        keys.append(("↓", x - 1))
    return keys


# ---------------------------------------------------------------- 重复涂装检测

# header 里游戏自动填入的默认字符串(哨兵值)
SENTINELS = ("Forza BaseLivery", "Forza Livery", "Forza SoulBoundLivery")

# 默认图片距离阈值(「图片: 距离≤N」条件的出厂值); 其余条件出厂默认不参与
DUP_T1 = 6


@dataclass(frozen=True)
class DupRule:
    """一条重复判定条件(v1.4.0 规则引擎; 无预制模式, 条件由用户在
    app 的「重复检测参数」里自由组合, 全部底层参数直接暴露)。

    car:    "same"=车型相同 | "diff"=车型不同
    author: "same"=同作者 | "diff"=双方实名且不同 | "any"=不限
            (作者为 Forza 哨兵名视为匿名, 匿名条目不参与 same/diff 判定)
    img:    None=不比对 | ("dist", 最大汉明距离) | "missing"=任一方无预览图文件
            (感知哈希汉明距离 0~64, 越小要求越像; 解码失败不视作无图)
    name:   None=不比对 | ("min", 相似度下限) | ("max", 相似度上限)
            (difflib 比率 0~1; 双方名称任一为空时相似度按 0 处理)
    created/downloaded: None=不参与 | "same" | "diff"
            (created=文件名时间戳≈作者创作时间; downloaded=条目 mtime≈下载落盘时间;
             任一方缺该时间则条件不成立)
    key 仅作为命中标签进入组规则列表; label 为界面展示短语。
    字段默认全部中立(不比对/不参与); 出厂默认条件见 DEFAULT_DUP_RULE。"""
    key: str = "重复"
    label: str = ""
    car: str = "same"
    author: str = "any"
    img: object = None
    name: object = None
    created: str | None = None
    downloaded: str | None = None


# 出厂默认条件: 同车型 + 图片距离≤6(即原「同车复刻」语义), 其余条件不参与
DEFAULT_DUP_RULE = DupRule(img=("dist", DUP_T1))


def _clean_texts(it: SaveItem) -> tuple[str, str, str]:
    """标题只清 Base/SoulBound 哨兵(默认标题 "Forza Livery" 原样保留);
    作者清全部哨兵; 描述为哨兵或与标题/作者相同时清空。"""
    title = "" if it.name in ("Forza BaseLivery", "Forza SoulBoundLivery") else it.name
    author = "" if it.creator in SENTINELS else it.creator
    desc = "" if (it.desc in SENTINELS or it.desc == it.name
                  or it.desc == it.creator) else it.desc
    return title, desc, author


def _dhash(path: Path) -> int | None:
    """64 位差异感知哈希: 灰度缩放到 9×8, 相邻像素比大小。解码失败/无 PIL 返回 None。"""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(path).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return None
    px = img.tobytes()            # L 模式 9×8 → 恰好 72 字节
    h = 0
    for row in range(8):
        base = row * 9
        for col in range(8):
            h = (h << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return h


def extract_dup_features(items: list[SaveItem]) -> dict[str, dict]:
    """为每个 Livery 条目预计算重复检测特征(清洗文本 + 缩略图感知哈希)。
    图片解码较慢, 适合在后台线程里预计算一次, 之后重复分组即可直接复用。
    条目较多时用线程池并行(PIL 解码释放 GIL, 实测提速约 3.5 倍),
    map 保序收集, 结果与串行完全一致; 条目太少时退回串行, 避免池开销。"""

    def _one(it: SaveItem) -> tuple[str, dict]:
        title, desc, author = _clean_texts(it)
        img_hash = None
        has_img = it.thumb_big is not None
        # 只取大图(bigThumb.png/webp), 大图缺失回退 thumb.webp; 不同规格的图不混用
        for k in ("bigThumb.webp", "bigThumb.png", "thumb.webp"):
            p = it.files.get(k)
            if p:
                img_hash = _dhash(p)
                break
        # noimg = 条目确实没有任何预览图文件(区别于「有文件但解码失败」,
        # 后者不参与「无图同名」类规则)
        return it.base, {"title": title, "desc": desc, "author": author,
                         "hash": img_hash, "noimg": not has_img}

    liveries = [it for it in items if it.itype == "Livery"]
    workers = min(8, os.cpu_count() or 4)
    if len(liveries) < 8 or workers < 2:
        pairs = map(_one, liveries)             # 太少/单核: 串行
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            pairs = list(ex.map(_one, liveries))
    return dict(pairs)


def detect_duplicates(items: list[SaveItem],
                      features: dict[str, dict] | None = None,
                      rules: list[DupRule] | None = None
                      ) -> tuple[dict[str, int], dict[int, int], dict[int, list[str]]]:
    """重复涂装推断(规则引擎, v1.4.0; 无预制模式)。

    两两配对, 命中任一给定条件的对即并查集合并(传递性: A~B、B~C 则三者同组)。
    条件对象 DupRule 的全部底层参数由用户在 app「重复检测参数」里组合:
    车型/作者 相同与否 + 图片汉明距离/名称相似度阈值 + 创建/下载时间 相同与否。

    features 为 extract_dup_features() 的预计算结果, 缺省时现场计算;
    rules 缺省用 [DEFAULT_DUP_RULE](同车型 + 图片距离≤6)。
    返回 (base -> 重复组号(1 起, 0 = 无重复),
          车型 ID -> 唯一涂装数(重复组算 1 种),
          组号 -> 命中的条件 key 列表(sorted))。
    仅统计 Livery 条目; 组号按本工具扫描序(时间戳降序)首次出现分配。"""
    import difflib

    liveries = [it for it in items if it.itype == "Livery"]
    if features is None:
        features = extract_dup_features(liveries)
    if rules is None:
        rules = [DEFAULT_DUP_RULE]

    index = {it.base: i for i, it in enumerate(liveries)}
    parent = list(range(len(liveries)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int):
        parent[find(x)] = find(y)

    def time_cmp(a_val, b_val, want: str) -> bool:
        """created/downloaded 条件: 双方都有该时间才可比; same=相等, diff=不等。"""
        if a_val is None or b_val is None:
            return False
        return (a_val == b_val) if want == "same" else (a_val != b_val)

    n = len(liveries)
    matched: list[tuple[int, int, list[str]]] = []
    for i in range(n):
        a = liveries[i]
        fa = features[a.base]
        for j in range(i + 1, n):
            b = liveries[j]
            fb = features[b.base]
            same_car = a.car_id == b.car_id
            same_author = bool(fa["author"]) and fa["author"] == fb["author"]
            if not same_car and not same_author:
                # 现有预设要么要求同车、要么要求同作者(无 car=diff+author=any 形态),
                # 可安全剪枝; 引入新形态规则时需回看此处
                continue
            hd = None
            if fa["hash"] is not None and fb["hash"] is not None:
                hd = (fa["hash"] ^ fb["hash"]).bit_count()

            sim: float | None = None

            def name_sim() -> float:
                nonlocal sim
                if sim is None:
                    ta, tb = fa["title"], fb["title"]
                    # 双方名称为空时不视为相似(避免无名条目互配)
                    sim = (difflib.SequenceMatcher(None, ta, tb).ratio()
                           if ta and tb else 0.0)
                return sim

            hit: list[str] = []
            for r in rules:
                if r.car == "same" and not same_car:
                    continue
                if r.car == "diff" and same_car:
                    continue
                if r.author == "same" and not same_author:
                    continue
                if r.author == "diff" and same_author:
                    continue          # 「不同作者」要求双方作者都实名(匿名互配无意义)
                if r.img is not None:
                    if r.img == "missing":
                        if not (fa.get("noimg") or fb.get("noimg")):
                            continue
                    else:
                        if hd is None or hd > r.img[1]:
                            continue
                if r.name is not None:
                    mode, v = r.name
                    s = name_sim()
                    if mode == "min" and s < v:
                        continue
                    if mode == "max" and s >= v:
                        continue
                if r.created is not None and not time_cmp(a.ts, b.ts, r.created):
                    continue
                if r.downloaded is not None and not time_cmp(a.mtime, b.mtime,
                                                             r.downloaded):
                    continue
                hit.append(r.key)
            if hit:
                union(i, j)
                matched.append((i, j, hit))

    # ---- 汇总: 连通分量 ≥2 的为重复组, 按首次出现顺序编号(1 起)
    sizes: dict[int, int] = {}
    for it in liveries:
        root = find(index[it.base])
        sizes[root] = sizes.get(root, 0) + 1
    root_gid: dict[int, int] = {}
    group_of: dict[str, int] = {}
    for it in liveries:
        root = find(index[it.base])
        if sizes[root] < 2:
            group_of[it.base] = 0
            continue
        gid = root_gid.setdefault(root, len(root_gid) + 1)
        group_of[it.base] = gid

    # 每组命中过的规则标签
    _gid_rules: dict[int, set] = {}
    for i, _j, rules_hit in matched:
        gid = group_of[liveries[i].base]
        if gid:
            _gid_rules.setdefault(gid, set()).update(rules_hit)
    group_rules = {gid: sorted(rs) for gid, rs in _gid_rules.items()}

    # 每车型唯一涂装数(重复组算 1 种)
    car_unique: dict[int, set] = {}
    for it in liveries:
        key = f"G{group_of[it.base]}" if group_of[it.base] else f"U{index[it.base]}"
        car_unique.setdefault(it.car_id, set()).add(key)

    return group_of, {cid: len(s) for cid, s in car_unique.items()}, group_rules


# ---------------------------------------------------------------- 车厂提取

# 已知车厂(基于 FH6存档涂装解析器 apply_all.js 的 BR 表, 按本车名表实测补充)
_BRAND_LIST = (
    "Lamborghini|Koenigsegg|Mercedes-AMG|Mercedes-Benz|AMG Transport Dynamics|"
    "Aston Martin|Alfa Romeo|Land Rover|De Tomaso|Gordon Murray Automotive|"
    "Formula Drift|Ferrari|Porsche|McLaren|Maserati|Pagani|Bentley|Hennessey|"
    "Rimac|Rivian|Lucid|Saleen|Shelby|Abarth|Zenvo|Apollo|Mitsubishi|Nissan|"
    "Toyota|Mazda|Honda|Ford|Dodge|Chevrolet|Cadillac|Buick|GMC|Pontiac|Plymouth|"
    "Datsun|Audi|BMW|Volkswagen|Hyundai|Kia|Lexus|Jaguar|Lotus|MINI|Volvo|Acura|"
    "Lincoln|Jeep|RAM|Renault|Peugeot|Opel|Subaru|Holden|HSV|TVR|Noble|Ariel|KTM|"
    "BAC|Wuling|Penhall|Polaris|Alumicraft|Reliant|Ultima|Radical|DeLorean|MG|"
    "Austin-Healey|Autozam|Schuppan|Jimco|Meyers|GR|"
    # 补充: 车名表里出现但 BR 表没有的品牌
    "Can-Am|Deberti|Fiat|Funco|Lancia|Peel|Viper|Exomotive"
).split("|")

# 车厂匹配模式: 长的优先, 允许连字符/空格差异, 右侧不得紧跟字母(防误匹配)
_BRAND_PATTERNS = [
    (b, re.compile(re.escape(b).replace(r"\ ", r"[- ]?").replace(r"\-", r"[- ]?")
                   + r"(?![A-Za-z])", re.I))
    for b in sorted(set(_BRAND_LIST), key=len, reverse=True)
]

# 首词 → 规范车厂名(多词品牌、别名、上游数据笔误)
BRAND_ALIASES = {
    "Gordon": "Gordon Murray Automotive",
    "Casey": "Casey Currie Motorsports",
    "RJ": "RJ Anderson",
    "Sierra": "Sierra Cars",
    "Mazdaspeed": "Mazda",
    "Nisan": "Nissan",            # 车名表笔误
    "Merceds-AMG": "Mercedes-AMG",  # 车名表笔误
    "SIERRA": "Sierra Cars",
    "Playground": "Traffic",      # 交通车(Box Truck/Bus/Flatbed)归为一组
}


def car_brand(name: str) -> str:
    """从车型显示名(如 "1969 Toyota 2000 GT")提取车厂名。无法判断时返回首词。"""
    n = re.sub(r"^\d{4}\s+", "", name).strip()
    if not n:
        return ""
    if n.endswith("(Traffic)"):
        return "Traffic"          # 场景交通车归为一组
    for brand, pat in _BRAND_PATTERNS:
        if pat.match(n):
            return brand
    first = n.split()[0]
    return BRAND_ALIASES.get(first, first)


# ---------------------------------------------------------------- 车辆名表

class CarTable:
    """车型 ID → 名称 对照表(只读; cars.json 随程序分发)。"""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict = {}
        self.load()

    def load(self):
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def name(self, game: str, car_id: int) -> str:
        return self._data.get(game, {}).get(str(car_id), "")

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
        src_key = str(s.get("source") or "?")
        src = {"steam": "Steam", "pgs": "商店版/pgs"}.get(src_key, src_key)
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
