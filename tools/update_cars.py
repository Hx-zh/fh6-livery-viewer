# -*- coding: utf-8 -*-
"""tools/update_cars.py — cars.json 维护闭环工具（PLAN.md「改动7」落地）。

用法:
    python tools/update_cars.py              # 报告模式: 解析串表 → (可选)内存文案年份 → diff
    python tools/update_cars.py --write      # 应用模式: 追加新增/修正差异(不自动删除) + _updated 打戳
    python tools/update_cars.py --str PATH   # 手动指定 EN.zip 或 Data_Car.str(默认自动找游戏目录)
    python tools/update_cars.py --no-mem     # 跳过内存数据源(纯磁盘)

数据源权威分级(见 tools/cars_update_workflow.md):
    ① 内存拍照文案年份(gamemem.read_car_years, 运行时解密 GameDB, 界面语言须简中)
    ② 磁盘 Data_Car.str(ID/短名/后缀年份)      ③ 官网/社区表: 仅旁证, 本工具不接入

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

# ---- 品牌展开表: 移植自参考实现 FH6存档涂装解析器v1.3.2/apply_all.js(已验证 660/671 展开一致)。
# 已知偏差(2026-09-19 内存 UI 实证, 权威口径以 UI 全名为准, 本表仅作 fallback 候选):
#   FW['M-B']='Mercedes-Benz' —— AMG 车型游戏内实为 Mercedes-AMG(2242/2471/2654/3064)
#   FW['AMG']='Mercedes-AMG'  —— 3176 Hammer 游戏内实为 Mercedes-Benz AMG Hammer Coupe
#   缺 Vaux→Vauxhall(278)、BR 缺 Ginetta(3429)/Dodge 前缀(3958)/Renault 前缀(3980)
#   3189 Hennessey / 3539·3540·3665 SIERRA Cars / 3880 Toyota 前缀不在 DisplayName 中
BR = ['Lamborghini', 'Koenigsegg', 'Mercedes-AMG', 'Mercedes-Benz', 'AMG Transport Dynamics', 'Aston Martin', 'Alfa Romeo', 'Land Rover', 'De Tomaso', 'Gordon Murray Automotive', 'Formula Drift', 'Ferrari', 'Porsche', 'McLaren', 'Maserati', 'Pagani', 'Bentley', 'Hennessey', 'Rimac', 'RIVIAN', 'Lucid', 'Saleen', 'Shelby', 'Abarth', 'Zenvo', 'Apollo', 'Mitsubishi', 'Nissan', 'Toyota', 'Mazda', 'Honda', 'Ford', 'Dodge', 'Chevrolet', 'Cadillac', 'Buick', 'GMC', 'Pontiac', 'Plymouth', 'Datsun', 'Audi', 'BMW', 'Volkswagen', 'Hyundai', 'Kia', 'Lexus', 'Jaguar', 'Lotus', 'MINI', 'Volvo', 'Acura', 'Lincoln', 'Jeep', 'RAM', 'Renault', 'Peugeot', 'Opel', 'SUBARU', 'Subaru', 'Holden', 'HSV', 'TVR', 'Noble', 'Ariel', 'KTM', 'BAC', 'Wuling', 'Penhall', 'Polaris', 'Alumicraft', 'Reliant', 'Ultima', 'Radical', 'DeLorean', 'MG', 'Austin-Healey', 'Autozam', 'Schuppan', 'Jimco', 'Meyers', 'GR']
FW = {'Lambo': 'Lamborghini', 'NISMO': 'Nissan', 'Caddy': 'Cadillac', 'SRT': 'Dodge', 'DD': 'DeBerti', 'GMA': 'Gordon Murray Automotive', 'LR': 'Land Rover', 'L.': 'Lamborghini', 'P.': 'Porsche', 'McL.': 'McLaren', 'Mit.': 'Mitsubishi', 'Nis.': 'Nissan', 'N.': 'Nissan', 'Toy.': 'Toyota', 'T.': 'Toyota', 'Mas.': 'Maserati', 'AR': 'Alfa Romeo', 'AMG': 'Mercedes-AMG', 'MB': 'Mercedes-Benz', 'AM': 'Aston Martin', 'Alfa': 'Alfa Romeo', 'Chevy': 'Chevrolet', 'VW': 'Volkswagen', 'M-B': 'Mercedes-Benz', 'M-AMG': 'Mercedes-AMG'}
MM = {2470: 'Aston Martin', 1063: 'Dodge', 1175: 'Pagani', 1200: 'Audi', 1398: 'Lamborghini', 1481: 'Austin-Healey', 1513: 'Maserati', 1532: 'Hennessey', 1533: 'Holden', 1562: 'Dodge', 1564: 'Chevrolet', 1586: 'Lincoln', 1601: 'Lamborghini', 2006: 'Chevrolet', 2034: 'Ferrari', 2128: 'Cadillac', 2177: 'Chevrolet', 2262: 'Cadillac', 2263: 'Dodge', 2297: 'Porsche', 2421: 'Cadillac', 2469: 'Toyota', 2494: 'Land Rover', 2526: 'Koenigsegg', 2552: 'Alumicraft', 2574: 'AMG Transport Dynamics', 2649: 'Ford', 2652: 'Mitsubishi', 2659: 'Nissan', 2712: 'Mitsubishi', 2713: 'Playground', 2714: 'Playground', 2755: 'Porsche', 2792: 'Ford', 2794: 'Porsche', 2801: 'Nissan', 2872: 'Hyundai', 2902: 'Playground', 2903: 'Subaru', 2910: 'Koenigsegg', 2987: 'Peel', 3072: 'Porsche', 3082: 'Maserati', 3087: 'McLaren', 3088: 'Chevrolet', 3118: 'Chevrolet', 3120: 'Lamborghini', 3129: 'Renault', 3134: 'Renault', 3156: 'McLaren', 3189: 'Ford', 3198: 'De Tomaso', 3225: 'Ferrari', 3227: 'Ferrari', 3249: 'Formula Drift', 3289: 'Lamborghini', 3318: 'Audi', 3363: 'Nissan', 3364: 'Aston Martin', 3367: 'Ferrari', 3369: 'Chevrolet', 3371: 'Lamborghini', 3414: 'Land Rover', 3441: 'Toyota', 3482: 'McLaren', 3486: 'Jeep', 3543: 'Pagani', 3595: 'Ferrari', 3599: 'Gordon Murray Automotive', 3600: 'Hennessey', 3611: 'Maserati', 3616: 'Mercedes-AMG', 3625: 'Rimac', 3631: 'Aston Martin', 3667: 'Porsche', 3668: 'McLaren', 3672: 'Lamborghini', 3686: 'Polaris', 3692: 'Ford', 3700: 'McLaren', 3722: 'GMC', 3726: 'Acura', 3736: 'Ford', 3750: 'Mitsubishi', 3753: 'Lamborghini', 3759: 'Lamborghini', 3760: 'Porsche', 3766: 'Chevrolet', 3771: 'Chevrolet', 3781: 'Porsche', 3785: 'Toyota', 3798: 'Mazda', 3827: 'Hyundai', 3829: 'Hyundai', 3840: 'Lamborghini', 3850: 'Dodge', 3855: 'Nissan', 3858: 'Nissan', 3859: 'Honda', 3860: 'Nissan', 3886: 'Mitsubishi', 3891: 'Lamborghini', 3914: 'Toyota', 3918: 'Nissan', 3921: 'Nissan', 3950: 'Ferrari', 3953: 'Porsche', 3955: 'RAM', 3959: 'Dodge', 4002: 'Lamborghini', 4038: 'Toyota', 4055: 'Toyota', 4057: 'Nissan', 4081: 'Koenigsegg', 4085: 'Mitsubishi', 4090: 'Mitsubishi', 4094: 'Nissan', 4114: 'Nissan', 4119: 'Nissan', 4124: 'Mercedes-Benz', 4125: 'Honda', 4126: 'Honda', 4127: 'Mitsubishi', 4128: 'Subaru', 4129: 'Nissan', 4145: 'Mazda', 4147: 'Alfa Romeo', 4160: 'Nissan', 4163: 'Wuling', 4167: 'Nissan', 4168: 'Ford', 4169: 'Mercedes-Benz', 4197: 'Mazda', 4198: 'Dodge', 4199: 'Toyota', 4205: 'Nissan', 4210: 'Lotus', 4214: 'Toyota', 4216: 'Honda', 4221: 'Toyota', 4223: 'Nissan', 4232: 'Porsche', 4255: 'Toyota', 4259: 'Toyota', 4260: 'Nissan', 4267: 'Mitsubishi', 4315: 'Peel', 4332: 'Toyota', 4333: 'Toyota', 4342: 'Toyota', 1297: 'Mitsubishi', 3937: 'Honda', 3954: 'Chevrolet', 4238: 'Nissan', 260: 'Porsche', 261: 'Porsche', 262: 'Porsche', 265: 'Porsche', 281: 'Dodge', 299: 'Chevrolet', 312: 'Chevrolet', 314: 'Chevrolet', 315: 'Chevrolet', 323: 'Lancia', 324: 'Lamborghini', 327: 'Mitsubishi', 343: 'Nissan', 344: 'Nissan', 345: 'Nissan', 374: 'Mitsubishi', 378: 'Mitsubishi', 440: 'Nissan', 458: 'Lancia', 639: 'Dodge', 1009: 'Mitsubishi', 1069: 'Chevrolet', 1093: 'Chevrolet', 1204: 'Renault', 1295: 'Lancia', 1381: 'Mitsubishi', 1661: 'Lancia', 2866: 'Exomotive', 2871: 'Can-Am', 2935: 'Funco', 3184: 'Ford', 3325: 'Aston Martin', 3404: 'Ford', 3523: 'Formula Drift', 3524: 'Formula Drift', 3539: 'SIERRA', 3540: 'SIERRA', 3665: 'SIERRA', 3774: 'Lamborghini', 3775: 'Lamborghini', 3910: 'Porsche', 4179: 'Nissan', 4254: 'Honda', 4261: 'Porsche', 2996: 'Formula Drift', 2997: 'Formula Drift', 3000: 'Formula Drift', 3003: 'Formula Drift', 3037: 'Formula Drift', 3232: 'Formula Drift', 3400: 'Formula Drift', 3411: 'Formula Drift', 3551: 'Formula Drift', 3744: 'Formula Drift', 1335: 'Mazda', 1478: 'Audi', 2517: 'Ford', 2636: 'Toyota', 2663: 'RJ Anderson', 2793: 'Ferrari', 2937: 'Ford', 3007: 'Volkswagen', 3031: 'Porsche', 3128: 'Ford', 3214: 'Porsche', 3282: 'Porsche', 3549: 'Alumicraft', 3554: 'Mitsubishi', 3603: 'Casey Currie Motorsports', 3604: 'Jimco', 3605: 'Jimco', 3662: 'RJ Anderson', 3670: 'Ford', 3693: 'Alumicraft', 4211: 'Honda', 4212: 'Nissan', 4213: 'Nissan', 4231: 'Honda', 4277: 'Honda'}

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


# ---- 名称/年份推导(③ 磁盘层) ----

def ms_year(ms: str, ds: str) -> int | None:
    """ModelShort 年份: 'XX 后缀(≤26→20XX 否则 19XX); 无引号取尾部 2 位且 DisplayName 未含该 token。"""
    ym = re.search(r"'(\d{2})$", ms) or None
    if not ym:
        m2 = re.search(r"\s(\d{2})$", ms)
        if m2 and not re.search(r"\b" + m2.group(1) + r"\b", ds):
            ym = m2
    if not ym:
        return None
    yy = int(ym.group(1))
    return 2000 + yy if yy <= 26 else 1900 + yy


def expand_name(code: int, ds: str, ms: str) -> str:
    """品牌展开(apply_all.js 同规则): BR 前缀 → FW 缩写 → MM 人工表 → 裸名。"""
    brand = ""
    for b in BR:
        if re.match("^" + re.sub(r"[- ]", "[- ]?", b), ms, re.I):
            brand = b
            break
    if not brand:
        f = re.split(r"[\s\d]", ms)[0].rstrip(".")
        brand = FW.get(f) or MM.get(code, "")
    return brand + " " + ds if brand and not ds.lower().startswith(brand.lower()) else ds


# ---- 内存数据源(①②, 游戏运行时可用; 只读) ----

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
    n_ms_year = sum(1 for ds, ms in entries.values() if ms_year(ms, ds))
    print(f"[串表] {str_path} → {len(entries)} 辆(ModelShort 带年份 {n_ms_year} 条)")

    cars: dict[str, str] = json.loads(CARS_JSON.read_text(encoding="utf-8"))["fh6"]

    caps: list[tuple[int, str]] = []
    if not no_mem:
        caps = caption_years() or []
        if caps:
            print(f"[内存] 拍照文案年份 {len(caps)} 条(GameDB 全名+年份)")
        else:
            print("[内存] 拍照文案不可用(游戏未运行/界面语言非简中), 年份侧降级为后缀推导")

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

    def resolve(code: int, ds: str, ms: str) -> tuple[str, int | None, str]:
        """解析 (全名, 年份, 证据)。优先级 ①文案 > cars.json 现存 > ②后缀/展开。

        现存条目的年份优先保留 json 值(v1.7.2 起人工实证), 仅缺年份时退回后缀推导
        (后缀年份实证会错, 如 2163: 后缀 '16, 游戏内/文案 2015)。
        """
        if code in cap_hit:
            nn, y = cap_hit[code]
            return nn, y, "文案"
        old = cars.get(str(code))
        if old is not None:
            oy, oname = split_name(old)
            y = ms_year(ms, ds)
            return oname, (oy or y), "现存/后缀" if (y and not oy) else "现存"
        return expand_name(code, ds, ms), ms_year(ms, ds), "展开低置信"

    added, removed, changed, same = [], [], [], 0
    for code in sorted(entries):
        ds, ms = entries[code]
        name, year, ev = resolve(code, ds, ms)
        full = f"{year} {name}" if year else name
        old = cars.get(str(code))
        if old is None:
            added.append((code, full, ev))
        elif norm(old) != norm(full):
            changed.append((code, old, full, ev))
        else:
            same += 1
    removed = [(int(c), v) for c, v in cars.items() if int(c) not in entries]

    print(f"\n===== diff 报告(对照 cars.json {len(cars)} 条) =====")
    print(f"[不变] {same} 条")
    print(f"\n[新增] {len(added)} 条(串表有、cars.json 无):")
    for c, full, ev in added:
        print(f"  + {c}: {full}   <{ev}>")
    print(f"\n[修正] {len(changed)} 条(名称/年份变化):")
    for c, old, new, ev in changed:
        print(f"  ~ {c}: {old}\n      → {new}   <{ev}>")
    print(f"\n[删除候选] {len(removed)} 条(cars.json 有、串表无; 不自动删除, 人工确认):")
    for c, v in removed:
        print(f"  - {c}: {v}")

    if not write:
        print("\n(报告模式。确认无误后加 --write 应用新增/修正; --write 不处理删除候选)")
        return 0
    # --write 安全闸: 低置信新增(串表残留/无法 join 内存数据)不自动落库, 留人工确认
    held = [a for a in added if a[2] == "展开低置信"]
    added = [a for a in added if a[2] != "展开低置信"]
    if held:
        print(f"\n[--write 拦截] {len(held)} 条低置信新增未落库(疑串表残留或无内存数据, 需人工确认):")
        for c, full, _ in held:
            print(f"  ? {c}: {full}")
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
