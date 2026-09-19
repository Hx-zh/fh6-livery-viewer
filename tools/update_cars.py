# -*- coding: utf-8 -*-
"""tools/update_cars.py — cars.json 维护闭环工具（PLAN.md「改动7」落地）。

用法:
    python tools/update_cars.py              # 报告模式: 串表 ID 锚点 + (可选)GameDB 文案 → diff
    python tools/update_cars.py --write      # 应用模式: 追加新增/修正差异(不自动删除) + _updated 打戳
    python tools/update_cars.py --str PATH   # 手动指定 EN.zip 或 Data_Car.str(默认自动找游戏目录)
    python tools/update_cars.py --no-mem     # 跳过内存数据源(仅探测新增 ID)

数据源(2026-09-19 简化, 仅留 GameDB 系):
    - **GameDB 文案**(gamemem.read_car_years, 运行时解密, 界面语言须简中) = 唯一权威
      命名/年份源; 双向唯一 join 到 .str 车型 ID。
    - **磁盘 Data_Car.str** 仅作 ID 锚点/新增探测(GameDB 文案不带 ID, 结构性必需)。
    - cars.json 现存值 = 人工实证缓存(文案未覆盖条目维持原值)。
    已废弃: 品牌展开表(BR/FW/MM)与 ModelShort 后缀年份推导——两者均被实证会错
    (2163 后缀 '16 vs 游戏内 2015), 猜错不如不猜, 无文案即报「待人工」。

教训(2026-09-19): 串表区域里「' 2015 ' + ' Mercedes-AMG GT S'」看似年份+全名成对,
实乃中文文案「拍摄 2015 年份的 Mercedes-AMG GT S」的 ASCII 碎片(非 ASCII 字节被
忽略造成的假象)——勿再据此臆造新数据源; 拍照文案即 GameDB 全名+年份的唯一内存载体。

只读原则: EN.zip 只读打开不解压写入; 内存只读(ReadProcessMemory), 不写不 hook。
"""
from __future__ import annotations

import json
import re
import struct
import sys
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CARS_JSON = ROOT / "cars.json"


def norm(s: str) -> str:
    """归一化: NFKC + 弯引号→直引号 + casefold + 空白压缩(比对用)。"""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).casefold().strip()


# ---- 磁盘 Data_Car.str ----

def find_game_str() -> Path | None:
    """自动定位游戏 EN.zip: Steam libraryfolders.vdf 库目录 + 常见根 + Xbox 版路径。"""
    candidates = []
    vdf = Path(r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf")
    if vdf.is_file():
        candidates += re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace"))
    candidates += [r"C:\Program Files (x86)\Steam", r"H:\SteamLibrary", r"G:\SteamLibrary",
                   r"D:\SteamLibrary", r"E:\SteamLibrary", r"F:\SteamLibrary"]
    seen: set[str] = set()
    for c in candidates:
        for p in Path(c).glob("steamapps/common/ForzaHorizon*/media/Stripped/StringTables/EN.zip"):
            if str(p) not in seen:
                seen.add(str(p))
                if "Horizon6" in str(p):
                    return p
    for d in "CDEFGH":
        for p in Path(f"{d}:/XboxGames").glob("ForzaHorizon*/**/StringTables/EN.zip"):
            return p
    return None


def parse_str_table(data: bytes) -> tuple[list[str], list[str]]:
    """ForzaTech 串表: 头 0x84/0x88 分别为 VALUES/KEYS 表偏移。返回 (keys, vals)。"""
    def rd(o: int) -> list[str]:
        cnt = struct.unpack_from("<I", data, o + 8)[0]
        eo, bo = o + 12, o + 12 + 8 * cnt
        out = []
        for i in range(cnt):
            p = bo + struct.unpack_from("<I", data, eo + i * 8 + 4)[0]
            e = data.index(b"\x00", p)
            out.append(data[p:e].decode("utf-8", "replace"))
        return out

    return rd(struct.unpack_from("<I", data, 0x88)[0]), rd(struct.unpack_from("<I", data, 0x84)[0])


def car_entries(keys: list[str], vals: list[str]) -> dict[int, tuple[str, str]]:
    """code -> (DisplayName, ModelShort)。ModelShort = vals 同数组下标 + DisplayName 条数。"""
    ds = sum(1 for k in keys if k.startswith("IDS_DisplayName_"))
    cd: dict[int, tuple[str, str]] = {}
    for i, k in enumerate(keys):
        m = re.match(r"^IDS_DisplayName_(\d+)$", k)
        if m:
            cd[int(m.group(1))] = (vals[i], vals[i + ds])
    if ds == 0 or len(cd) != ds:
        raise SystemExit(f"串表解析异常: DisplayName 计数 {ds}, 实际条目 {len(cd)}")
    return cd


def load_str_entries(str_path: Path) -> dict[int, tuple[str, str]]:
    if str_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(str_path) as z:
            data = z.read("Data_Car.str")
    else:
        data = str_path.read_bytes()
    return car_entries(*parse_str_table(data))


# ---- 内存数据源(①, 游戏运行时可用; 只读) ----

def caption_years() -> list[tuple[int, str]] | None:
    """① 拍照文案 (年份, 完整车名) 去重表(gamemem.read_car_years 封装)。

    该文案是运行时解密 GameDB 的唯一内存载体(年份+商标全称全名);
    依赖游戏界面语言为简中, 不可用返回 None。
    """
    import gamemem
    pid = gamemem.find_game_pid()
    if not pid:
        return None
    with gamemem.GameMemoryReader(pid) as r:
        return r.read_car_years()


# ---- 主流程 ----

def split_name(v: str) -> tuple[int | None, str]:
    m = re.match(r"^(\d{4})\s+(.*)$", v)
    return (int(m.group(1)), m.group(2)) if m else (None, v)


def main(argv: list[str]) -> int:
    args = set(argv)
    str_arg = next((a.split("=", 1)[1] for a in argv if a.startswith("--str=")), None)
    no_mem, write = "--no-mem" in args, "--write" in args

    str_path = Path(str_arg) if str_arg else find_game_str()
    if not str_path:
        raise SystemExit("未找到游戏 EN.zip(用 --str=PATH 手动指定)")
    entries = load_str_entries(str_path)
    print(f"[串表] {str_path} → {len(entries)} 辆(仅作 ID 锚点/新增探测, 不作命名来源)")

    cars: dict[str, str] = json.loads(CARS_JSON.read_text(encoding="utf-8"))["fh6"]

    caps: list[tuple[int, str]] = []
    if not no_mem:
        caps = caption_years() or []
        if caps:
            print(f"[内存] 拍照文案 {len(caps)} 条(GameDB 全名+年份, 唯一权威命名/年份源)")
        else:
            print("[内存] 拍照文案不可用(游戏未运行/界面语言非简中), 本轮只能探测新增 ID, 不产出命名/年份")

    def build_matches(pool: dict[str, tuple[str, int]]) -> dict[int, tuple[str, int]]:
        """pool: norm(全名) -> (全名原文, 年份)。返回 code -> (全名原文, 年份)。

        双向唯一才匹配: ① 全名以该 code 的 DisplayName 结尾; ② 该全名不被任何
        其他 code 的 DisplayName 后缀匹配(消歧, 如 Civic Type R ×5 / Corvette Z06 ×2)。
        """
        ds_norms = {c: norm(d) for c, (d, _) in entries.items()}
        out: dict[int, tuple[str, int]] = {}
        for nn, (orig, y) in pool.items():
            hits = [c for c, nds in ds_norms.items() if nn.endswith(nds)]
            if len(hits) == 1:
                out[hits[0]] = (orig, y)
        return out

    cap_hit = build_matches({norm(n): (n, y) for y, n in caps}) if caps else {}

    def resolve(code: int, ds: str) -> tuple[str | None, int | None, str]:
        """解析 (全名, 年份, 证据)。唯一命名/年份源 = GameDB 文案(双向唯一匹配)。

        无文案匹配: 现存条目维持 cars.json 值(人工实证缓存), 新 ID 返回 (None,…)=待人工。
        品牌展开/后缀年份推导已废弃(2026-09-19 简化: 仅信 GameDB, 猜错不如不猜)。
        """
        if code in cap_hit:
            nn, y = cap_hit[code]
            return nn, y, "文案"
        old = cars.get(str(code))
        if old is not None:
            oy, oname = split_name(old)
            return oname, oy, "现存"
        return None, None, "待人工"

    added, pending, removed, changed, same = [], [], [], [], 0
    for code in sorted(entries):
        ds, _ms = entries[code]
        name, year, ev = resolve(code, ds)
        old = cars.get(str(code))
        if old is None:
            if name is None:
                pending.append(code)
            else:
                added.append((code, f"{year} {name}" if year else name, ev))
            continue
        full = f"{year} {name}" if year else (name or old)
        if norm(old) != norm(full):
            changed.append((code, old, full, ev))
        else:
            same += 1

    print(f"\n===== diff 报告(对照 cars.json {len(cars)} 条) =====")
    print(f"[不变] {same} 条")
    print(f"\n[新增] {len(added)} 条(新 ID 且文案已命中, 可自动落库):")
    for c, full, ev in added:
        print(f"  + {c}: {full}   <{ev}>")
    print(f"\n[新增·待人工] {len(pending)} 条(新 ID 无文案覆盖, 不猜名, 需人工查证):")
    for c in pending:
        print(f"  ? {c}: ds={entries[c][0]!r}")
    print(f"\n[修正] {len(changed)} 条(文案与现存不一致, 以文案为准):")
    for c, old, new, ev in changed:
        print(f"  ~ {c}: {old}\n      → {new}   <{ev}>")
    print(f"\n[删除候选] {len(removed)} 条(cars.json 有、串表无; 不自动删除, 人工确认):")
    for c, v in removed:
        print(f"  - {c}: {v}")

    if not write:
        print("\n(报告模式。确认无误后加 --write 应用新增/修正; 待人工/删除候选永不自动落库)")
        return 0
    if not added and not changed:
        print("\n[--write] 无新增/修正, 不写文件")
        return 0
    data = json.loads(CARS_JSON.read_text(encoding="utf-8"))
    fh6 = data["fh6"]
    for c, full, _ in added:
        fh6[str(c)] = full
    for c, _old, new, _ in changed:
        fh6[str(c)] = new
    data["fh6"] = dict(sorted(fh6.items()))  # 文件约定=键字典序(与历史一致, 差异最小)
    data["_updated"] = datetime.now().astimezone().isoformat(timespec="seconds")
    raw = CARS_JSON.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    backup = ROOT / "backups" / f"cars_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
    backup.write_bytes(raw)
    out = (json.dumps(data, ensure_ascii=False, indent=2).replace("\n", nl) + nl).encode("utf-8")
    CARS_JSON.write_bytes(out)
    print(f"\n[--write] 已应用 +{len(added)} ~{len(changed)}, 备份 {backup.name}, "
          f"_updated={data['_updated']}(记得当日 commit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
