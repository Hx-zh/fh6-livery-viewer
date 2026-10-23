# -*- coding: utf-8 -*-
"""tools/update_part_names.py — 查看器套件名多语言数据生成(游戏串表 → assets)。

查看器 PARTS 页的选项名原本只有上游导出的英文表(`viewer3d/assets/cars/
part_names.json`, 682 车型 / 7706 条)。游戏本身自带 25 种语言的界面串表,
套件名/厂家名/类别名都在里面, 所以其它语言不需要人工翻译: 用 EN 表做对照
逐条 join, 直接生成 `part_names.<lang>.json`。

用法:
    python tools/update_part_names.py               # 报告模式: 各语言命中率
    python tools/update_part_names.py --write       # 写入 viewer3d/assets/cars/
    python tools/update_part_names.py --game PATH   # 手动指定游戏根目录/串表目录

数据源(只读, 全部来自游戏安装目录):
    <game>/media/Stripped/StringTables/EN.zip   + CHS/CHT/JP/KO.zip
      - Upgrades.str              具体套件名(原厂/街车版/跑车版/赛车版 + 类别…)
      - List_PartManufacturer.str 厂家名(Stock 原厂 / Toyota 丰田 / …)
      - UpgradeTypes.str          类别名(Body Kit / Front Bumper / …)

映射规则(与 part_names.json 的构词一致):
    "Toyota - Remove Front Bumper" → "丰田 - 移除前保险杠"
    "Forza Horizon 6 Widebody Kit" → "极限竞速：地平线 6 宽体车身套件"(无 " - " 分隔)
    游戏里没有的档位(如 Race Hood / Sport Side Skirts)保持英文并计入未命中, 不猜。

只读原则: 串表 zip 只读打开, 不解压落盘; 只有 --write 才写 viewer3d/assets。
"""
from __future__ import annotations

import json
import struct
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ASSETS = ROOT / "viewer3d" / "assets" / "cars"
BASE_JSON = ASSETS / "part_names.json"

# 查看器语言码 -> 游戏语言包名
LANG_ZIPS = {"zh-cn": "CHS", "zh-tw": "CHT", "ja": "JP", "ko": "KO"}

# 套件类别 ID -> UpgradeTypes.str 的 key。ID 与
# viewer3d/fls/src/core/model_geometry.h 的 car_part_types 保持一致。
PART_TYPE_KEYS = {
    2: "IDS_Name_38",    # CarBody     -> Body Kit
    9: "IDS_Name_25",    # RearWing    -> Rear Wing
    34: "IDS_Name_24",   # FrontBumper -> Front Bumper
    35: "IDS_Name_26",   # RearBumper  -> Rear Bumper
    36: "IDS_Name_28",   # Hood        -> Hood
    37: "IDS_Name_27",   # SideSkirts  -> Side Skirts
}

# 命中率下限: 低于此值拒绝写入, 防止串表换版/解析错位时把坏数据发出去
MIN_COVERAGE = 0.90


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


def read_table(zip_path: Path, name: str) -> dict[str, str]:
    with zipfile.ZipFile(zip_path) as archive:
        keys, vals = parse_str_table(archive.read(name))
    return dict(zip(keys, vals))


def text_map(en: dict[str, str], localized: dict[str, str]) -> tuple[dict[str, str], int]:
    """英文串 -> 目标语言串。同一英文对应多个不同译文时保留首个并计数(消歧告警)。"""
    out: dict[str, str] = {}
    ambiguous = 0
    for key, value in en.items():
        src = value.strip()
        if not src:
            continue
        dst = localized.get(key, "").strip()
        if not dst:
            continue
        if src in out and out[src] != dst:
            ambiguous += 1
            continue
        out[src] = dst
    return out, ambiguous


def translate(value: str, manufacturers: dict[str, str], upgrades: dict[str, str],
              part_types: dict[str, str], upgrade_suffixes: list[str]) -> str | None:
    if " - " in value:
        maker, option = (part.strip() for part in value.split(" - ", 1))
        if maker in manufacturers and option in upgrades:
            return f"{manufacturers[maker]} - {upgrades[option]}"
    text = value.strip()
    for table in (part_types, upgrades, manufacturers):
        if text in table:
            return table[text]
    # 无 " - " 分隔的组合: "<厂家> <套件名>"(如 Forza Horizon 6 Widebody Kit)
    for option in upgrade_suffixes:
        if text.endswith(" " + option):
            maker = text[: -len(option) - 1].strip()
            if maker in manufacturers:
                return f"{manufacturers[maker]} {upgrades[option]}"
    return None


def string_tables_dir(game: Path) -> Path | None:
    for candidate in (game / "media" / "Stripped" / "StringTables",
                      game / "Content" / "media" / "Stripped" / "StringTables"):
        if (candidate / "EN.zip").is_file():
            return candidate
    if (game / "EN.zip").is_file():
        return game
    return None


def find_string_tables() -> Path | None:
    import flsbridge  # 只读探测, 与 app 用同一套 Steam/Xbox 候选

    root = flsbridge.detect_game_root()
    return string_tables_dir(root) if root is not None else None


def dump_models(path: Path, meta: dict[str, object],
                models: dict[str, dict[str, str]]) -> None:
    """与现有 part_names.json 同款排版: 每条车型一行, 2 空格缩进, CRLF。"""
    items = [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}"
             for k, v in meta.items()]
    items += [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}"
              for k, v in models.items()]
    text = "{\n" + ",\n".join(items) + "\n}\n"
    with path.open("w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(text)


def main(argv: list[str]) -> int:
    game_arg = next((a.split("=", 1)[1] for a in argv if a.startswith("--game=")), None)
    write = "--write" in set(argv)

    tables_dir = Path(game_arg) if game_arg else find_string_tables()
    if tables_dir is None or string_tables_dir(tables_dir) is None:
        raise SystemExit("未找到游戏串表目录(用 --game=PATH 手动指定游戏根目录)")
    tables_dir = string_tables_dir(tables_dir)
    if tables_dir is None:
        raise SystemExit("--game 指向的目录里没有 media/Stripped/StringTables")
    print(f"[串表] {tables_dir}")

    base: dict[str, dict[str, str]] = json.loads(BASE_JSON.read_text(encoding="utf-8"))
    values = [v for entries in base.values() for v in entries.values()]
    print(f"[基准] {BASE_JSON.name}: {len(base)} 车型 / {len(values)} 条")

    en_zip = tables_dir / "EN.zip"
    en_upgrades = read_table(en_zip, "Upgrades.str")
    en_manufacturers = read_table(en_zip, "List_PartManufacturer.str")
    en_types = read_table(en_zip, "UpgradeTypes.str")

    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    failures = 0
    for code, zip_name in LANG_ZIPS.items():
        zip_path = tables_dir / f"{zip_name}.zip"
        if not zip_path.is_file():
            print(f"[{code}] 跳过: 缺少 {zip_name}.zip")
            failures += 1
            continue
        upgrades, amb_u = text_map(en_upgrades, read_table(zip_path, "Upgrades.str"))
        manufacturers, amb_m = text_map(
            en_manufacturers, read_table(zip_path, "List_PartManufacturer.str"))
        types, amb_t = text_map(en_types, read_table(zip_path, "UpgradeTypes.str"))
        if amb_u or amb_m or amb_t:
            print(f"[{code}] 消歧告警: Upgrades {amb_u} / 厂家 {amb_m} / 类别 {amb_t}")

        suffixes = sorted(upgrades, key=len, reverse=True)
        models: dict[str, dict[str, str]] = {}
        misses: dict[str, int] = {}
        hits = 0
        for model, entries in base.items():
            translated: dict[str, str] = {}
            for key, value in entries.items():
                text = translate(value, manufacturers, upgrades, types, suffixes)
                if text is None:
                    misses[value] = misses.get(value, 0) + 1
                    translated[key] = value          # 游戏里没有 → 保留英文, 不猜
                else:
                    hits += 1
                    translated[key] = text
            models[model] = translated

        coverage = hits / len(values) if values else 0.0
        print(f"[{code}] {hits}/{len(values)} 命中 ({coverage:.2%}), "
              f"未命中 {sum(misses.values())} 条 / {len(misses)} 种")
        for value, count in sorted(misses.items(), key=lambda kv: -kv[1]):
            print(f"        {count}× {value}")

        # 类别名按 IDS key 直取: text_map 的键是英文串, 这里要的是 partType ID -> 类别名
        localized_types = read_table(zip_path, "UpgradeTypes.str")
        part_types = {str(pid): localized_types[key].strip()
                      for pid, key in PART_TYPE_KEYS.items()
                      if localized_types.get(key, "").strip()}
        meta = {
            "_lang": code,
            "_source": f"Forza Horizon 6 {zip_name}.zip: Upgrades.str, "
                       f"List_PartManufacturer.str, UpgradeTypes.str",
            "_updated": stamp,
            "_partTypes": part_types,
        }
        target = ASSETS / f"part_names.{code}.json"
        if write:
            if coverage < MIN_COVERAGE:
                print(f"[{code}] 命中率低于 {MIN_COVERAGE:.0%}, 拒绝写入")
                failures += 1
                continue
            dump_models(target, meta, models)
            print(f"[{code}] 已写入 {target.relative_to(ROOT)}")
        else:
            print(f"[{code}] 报告模式: 未写入 {target.relative_to(ROOT)} (加 --write 落地)")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
