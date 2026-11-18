# -*- coding: utf-8 -*-
"""tools/update_part_names.py — 查看器套件名多语言数据生成(GameDB/串表 → assets)。

查看器 PARTS 页的选项名原本只有上游导出的英文表(`viewer3d/assets/cars/
part_names.json`)。游戏自带 25 种语言的界面串表，所以其它语言不需要人工翻译:
用 EN 表做对照逐条 join，生成 `part_names.<lang>.json`。

用法:
    python tools/update_part_names.py               # 报告模式: 各语言命中率
    python tools/update_part_names.py --write       # 写入 viewer3d/assets/cars/
    python tools/update_part_names.py --game PATH   # 手动指定游戏根目录/串表目录
    python tools/update_part_names.py --game PATH --gamedb PATH/gamedb.sqlite
                                                    # 同时报告可补齐的缺失车型

--gamedb 只处理 part_names.json 里不存在的 model code。Data_Car.MediaName 对应
media/Cars/<CODE>.zip 内的 Manifest.xml；有 Ordinal 的升级表直接连接 Data_Car.Id，
四种无 Ordinal 的车身件则经 CarBodyID → List_UpgradeCarBody.CarBodyID +
Data_CarBody.ID → Ordinal → Data_Car.Id 精确连接，并拒绝重复/断链。
名称优先按 Upgrades.id → IDS_Name_<id>、ManufacturerID →
IDS_PartManufacturer_<id> 映射到 EN 串表；旧库明文/哈希值仅作兼容回退。
非原厂父套件保留厂家名；无通用 Upgrades 行时只复用现存唯一 PartId 名称，否则报错。
Manifest 提供 PartEnum/PartId 对照；缺名引用单独告警、不阻断，不凭 Manifest 猜名称。
List_Wheels 会被检查/计数，但当前查看器六类不含 WheelStyle，不伪造轮毂名称映射。

串表来源(只读):
    <game>/media/Stripped/StringTables/EN.zip + CHS/CHT/JP/KO.zip
      - Upgrades.str / List_PartManufacturer.str / UpgradeTypes.str

默认只报告。--write 保留所有现存车型和条目，只追加完整解析出的新车型条目；
英文基准新增会先备份，其它语言沿用原有未命中保留英文规则和 90% 覆盖门禁。
"""
from __future__ import annotations

import json
import re
import sqlite3
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gamedbmem  # noqa: E402  (ROOT 必须先入 sys.path)

ASSETS = ROOT / "viewer3d" / "assets" / "cars"
BASE_JSON = ASSETS / "part_names.json"

LANG_ZIPS = {"zh-cn": "CHS", "zh-tw": "CHT", "ja": "JP", "ko": "KO"}

# 与 viewer3d/fls/src/core/model_geometry.h 的 car_part_types 一致。
PART_TYPE_KEYS = {
    2: "IDS_Name_38",    # CarBody     -> Body Kit
    9: "IDS_Name_25",    # RearWing    -> Rear Wing
    34: "IDS_Name_24",   # FrontBumper -> Front Bumper
    35: "IDS_Name_26",   # RearBumper  -> Rear Bumper
    36: "IDS_Name_28",   # Hood        -> Hood
    37: "IDS_Name_27",   # SideSkirts  -> Side Skirts
}

PART_TABLES = {
    2: ("List_UpgradeCarBody", "CarBody"),
    9: ("List_UpgradeRearWing", "RearWing"),
    34: ("List_UpgradeCarBodyFrontBumper", "FrontBumper"),
    35: ("List_UpgradeCarBodyRearBumper", "RearBumper"),
    36: ("List_UpgradeCarBodyHood", "Hood"),
    37: ("List_UpgradeCarBodySideSkirt", "SideSkirts"),
}

CORE_SCHEMA = {
    "Data_Car": {"Id", "MediaName"},
    "Data_UpgradePart": {"Id", "PartName"},
    "UpgradeTypes": {"Id", "PartName"},
    "Upgrades": {"Id", "TypeId", "Level", "Name"},
    "List_PartManufacturer": {"Id", "PartManufacturer"},
}
PART_COLUMNS = {"Id", "Level", "IsStock", "ManufacturerID"}
MODEL_CODE_RE = re.compile(r"^[A-Za-z0-9_]+$")
MIN_COVERAGE = 0.90


@dataclass
class PartExtraction:
    models: dict[str, dict[str, str]] = field(default_factory=dict)
    manifest_missing: dict[str, list[str]] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    covered: set[str] = field(default_factory=set)
    manifest_links: int = 0
    db_rows: int = 0
    wheel_rows: int | None = None


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
    import flsbridge

    root = flsbridge.detect_game_root()
    return string_tables_dir(root) if root is not None else None


def find_cars_dir(game: Path, tables_dir: Path) -> Path | None:
    candidates = [game / "media" / "Cars", game / "Content" / "media" / "Cars"]
    if game.name.casefold() == "cars":
        candidates.append(game)
    if len(tables_dir.parents) >= 3:
        candidates.append(tables_dir.parents[2] / "media" / "Cars")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def require_columns(
    tables: dict[str, tuple[str, ...]], table: str, columns: set[str]
) -> list[str]:
    actual = next((cols for name, cols in tables.items() if name.casefold() == table.casefold()), None)
    if actual is None:
        return [f"{table} (整表)"]
    present = {col.casefold() for col in actual}
    return [
        f"{table}.{column}" for column in sorted(columns)
        if column.casefold() not in present
    ]


def manifest_part_ids(car_zip: Path) -> set[tuple[int, int]]:
    with zipfile.ZipFile(car_zip) as archive:
        root = ET.fromstring(archive.read("Manifest.xml"))
    enum_to_type = {enum: part_type for part_type, (_table, enum) in PART_TABLES.items()}
    return {
        (enum_to_type[element.attrib["PartEnum"]], int(element.attrib["PartId"]))
        for element in root.iter("UpgradeablePart")
        if element.attrib.get("PartEnum") in enum_to_type
    }


def db_text(value: object, table: dict[str, str], english_values: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text in table:
        resolved = table[text].strip()
        return resolved or None
    return text if text in english_values else None


def generate_part_models(
    conn: sqlite3.Connection,
    tables: dict[str, tuple[str, ...]],
    cars_dir: Path,
    existing: dict[str, dict[str, str]],
    en_upgrades: dict[str, str],
    en_manufacturers: dict[str, str],
) -> PartExtraction:
    result = PartExtraction()
    missing_core = [
        item for table, columns in CORE_SCHEMA.items()
        for item in require_columns(tables, table, columns)
    ]
    if missing_core:
        raise gamedbmem.GameDBError(
            "无法建立升级件名称映射；缺少 " + ", ".join(missing_core)
        )

    if any(name.casefold() == "list_wheels" for name in tables):
        result.wheel_rows = conn.execute("SELECT COUNT(*) FROM List_Wheels").fetchone()[0]
    else:
        result.issues.append("缺少 List_Wheels；不生成 WheelStyle（当前六类车身件不受影响）")

    existing_folded = {model.casefold() for model in existing}
    known_part_names: dict[tuple[int, int], set[str]] = {}
    for entries in existing.values():
        for key, value in entries.items():
            part_type_text, separator, part_id_text = key.partition(":")
            if separator and part_type_text.isdigit() and part_id_text.isdigit() and value:
                known_part_names.setdefault(
                    (int(part_type_text), int(part_id_text)), set()
                ).add(value)
    available: dict[int, tuple[str, str, str]] = {}
    for part_type, (table, enum) in PART_TABLES.items():
        missing = require_columns(tables, table, PART_COLUMNS)
        if missing:
            result.issues.append(f"跳过 {enum}: 缺少 " + ", ".join(missing))
            continue
        if not require_columns(tables, table, {"Ordinal"}):
            available[part_type] = (table, enum, "ordinal")
            continue
        parent_missing = (
            require_columns(tables, table, {"CarBodyID"})
            + require_columns(tables, "List_UpgradeCarBody", {"Id", "Ordinal", "CarBodyID"})
            + require_columns(tables, "Data_CarBody", {"ID"})
        )
        if parent_missing:
            result.issues.append(
                f"跳过 {enum}: 缺少 Ordinal/CarBodyID 连接所需的 " + ", ".join(parent_missing)
            )
            continue
        duplicates = conn.execute(
            "SELECT COUNT(*) FROM (SELECT CarBodyID FROM List_UpgradeCarBody "
            "GROUP BY CarBodyID HAVING COUNT(*) <> 1)"
        ).fetchone()[0]
        if duplicates:
            raise gamedbmem.GameDBError("List_UpgradeCarBody.CarBodyID 不唯一，拒绝猜测车身件车型")
        orphans = conn.execute(f"""
SELECT COUNT(*)
FROM {gamedbmem.quote_identifier(table)} AS p
LEFT JOIN List_UpgradeCarBody AS parent ON parent.CarBodyID = p.CarBodyID
LEFT JOIN Data_CarBody AS body ON body.ID = p.CarBodyID
LEFT JOIN Data_Car AS c ON c.Id = parent.Ordinal
WHERE parent.Id IS NULL OR body.ID IS NULL OR c.Id IS NULL
""").fetchone()[0]
        if orphans:
            result.issues.append(f"跳过 {enum}: CarBodyID 有 {orphans} 行无法精确连接到车型")
            continue
        available[part_type] = (table, enum, "carbody")
    if not available:
        result.issues.append("六个可视升级表均不可用；未生成任何车型")
        return result

    manifests: dict[str, set[tuple[int, int]] | None] = {}
    new_names: dict[str, str] = {}
    linked: set[tuple[str, int, int]] = set()
    blocked: set[str] = set()
    conflicts: set[tuple[str, str]] = set()
    upgrade_values = set(en_upgrades.values())
    manufacturer_values = set(en_manufacturers.values())

    for part_type, (table, enum, join_kind) in available.items():
        if join_kind == "ordinal":
            join = f"""FROM Data_Car AS c
JOIN {gamedbmem.quote_identifier(table)} AS p ON p.Ordinal = c.Id"""
            parent_stock = "p.IsStock"
        else:
            join = f"""FROM {gamedbmem.quote_identifier(table)} AS p
JOIN List_UpgradeCarBody AS parent ON parent.CarBodyID = p.CarBodyID
JOIN Data_CarBody AS body ON body.ID = p.CarBodyID
JOIN Data_Car AS c ON c.Id = parent.Ordinal"""
            parent_stock = "parent.IsStock"
        query = f"""
SELECT c.Id AS CarId, c.MediaName AS MediaName,
       p.Id AS PartId, p.IsStock AS IsStock, {parent_stock} AS ParentIsStock,
       p.ManufacturerID AS ManufacturerID, u.id AS UpgradeId,
       u.Name AS UpgradeName, pm.PartManufacturer AS ManufacturerName
{join}
LEFT JOIN Data_UpgradePart AS dp ON dp.Id = {part_type}
LEFT JOIN UpgradeTypes AS ut ON ut.PartName = dp.PartName
LEFT JOIN Upgrades AS u ON u.TypeId = ut.Id AND u.Level = p.Level
LEFT JOIN List_PartManufacturer AS pm ON pm.Id = p.ManufacturerID
ORDER BY c.Id, p.Id, u.Name
"""
        for row in conn.execute(query):
            result.db_rows += 1
            code_value = row["MediaName"]
            if not isinstance(code_value, str):
                result.issues.append(f"车 ID {row['CarId']}: MediaName 非字符串")
                continue
            code = code_value.strip()
            if not MODEL_CODE_RE.fullmatch(code):
                result.issues.append(f"车 ID {row['CarId']}: 非法 MediaName {code_value!r}")
                continue
            folded = code.casefold()
            if folded in existing_folded:
                result.covered.add(folded)
                continue
            code = new_names.get(folded, code)
            if folded in blocked:
                continue
            if folded not in manifests:
                try:
                    manifests[folded] = manifest_part_ids(cars_dir / f"{code}.zip")
                except (OSError, KeyError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
                    manifests[folded] = None
                    blocked.add(folded)
                    result.models.pop(code, None)
                    result.issues.append(f"{code}: Manifest.xml 不可验证，整车型跳过: {exc}")
                    continue
            manifest = manifests[folded]
            if manifest is None:
                continue

            part_id = row["PartId"]
            if isinstance(part_id, bool) or not isinstance(part_id, int) or part_id < 0:
                result.issues.append(f"{code}/{enum}: 非法 PartId {part_id!r}")
                continue
            key = f"{part_type}:{part_id}"
            if (folded, key) in conflicts:
                continue
            upgrade_id = row["UpgradeId"]
            upgrade_name = ""
            if isinstance(upgrade_id, int) and not isinstance(upgrade_id, bool):
                upgrade_name = en_upgrades.get(f"IDS_Name_{upgrade_id}", "").strip()
            if not upgrade_name:
                upgrade_name = db_text(row["UpgradeName"], en_upgrades, upgrade_values) or ""
            known_name = ""
            if not upgrade_name:
                candidates = known_part_names.get((part_type, part_id), set())
                if len(candidates) == 1:
                    known_name = next(iter(candidates))
                else:
                    result.issues.append(
                        f"{code}/{key}: Upgrades.id/Name 无法映射到 EN Upgrades.str，"
                        f"现存唯一 PartId 名称也不可用: {row['UpgradeId']!r} / {row['UpgradeName']!r}"
                    )
                    continue

            is_stock = row["IsStock"]
            if is_stock not in (0, 1):
                result.issues.append(f"{code}/{key}: 非法 IsStock {is_stock!r}")
                continue
            manufacturer_id = row["ManufacturerID"]
            maker = ""
            if (not known_name and (not is_stock or row["ParentIsStock"] == 0)
                    and manufacturer_id not in (None, 0)):
                maker_value = ""
                if isinstance(manufacturer_id, int) and not isinstance(manufacturer_id, bool):
                    maker_value = en_manufacturers.get(
                        f"IDS_PartManufacturer_{manufacturer_id}", ""
                    ).strip()
                if not maker_value:
                    maker_value = db_text(
                        row["ManufacturerName"], en_manufacturers, manufacturer_values
                    ) or ""
                if not maker_value:
                    result.issues.append(
                        f"{code}/{key}: ManufacturerID={manufacturer_id!r} 无法映射到厂家串表"
                    )
                    continue
                if maker_value != en_manufacturers.get("IDS_PartManufacturer_1", "").strip():
                    maker = maker_value
            if known_name:
                name = known_name
            elif not maker:
                name = upgrade_name
            elif part_type == 2:
                name = f"{maker} {upgrade_name}"
            else:
                name = f"{maker} - {upgrade_name}"

            new_names.setdefault(folded, code)
            old = result.models.get(code, {}).get(key)
            if old is not None and old != name:
                conflicts.add((folded, key))
                del result.models[code][key]
                result.issues.append(f"{code}/{key}: 多个名称冲突，整项拒绝: {old!r} / {name!r}")
                continue
            link = (folded, part_type, part_id)
            if (part_type, part_id) in manifest and link not in linked:
                linked.add(link)
                result.manifest_links += 1
            result.models.setdefault(code, {})[key] = name

    for model in list(result.models):
        if not result.models[model]:
            del result.models[model]
    for folded, manifest in manifests.items():
        if manifest is None:
            continue
        code = new_names.get(folded)
        if code is None:
            continue
        present = {
            (int(key.split(":", 1)[0]), int(key.split(":", 1)[1]))
            for key in result.models.get(code, {})
        }
        missing = sorted(manifest - present)
        if missing:
            result.manifest_missing[code] = [
                f"{part_type}:{part_id}" for part_type, part_id in missing
            ]
    return result


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
    db_arg = next((a.split("=", 1)[1] for a in argv if a.startswith("--gamedb=")), None)
    write = "--write" in set(argv)

    game_input = Path(game_arg) if game_arg else None
    tables_dir = game_input if game_input is not None else find_string_tables()
    if tables_dir is None or string_tables_dir(tables_dir) is None:
        raise SystemExit("未找到游戏串表目录(用 --game=PATH 手动指定游戏根目录)")
    tables_dir = string_tables_dir(tables_dir)
    if tables_dir is None:
        raise SystemExit("--game 指向的目录里没有 media/Stripped/StringTables")
    print(f"[串表] {tables_dir}")

    base: dict[str, dict[str, str]] = json.loads(BASE_JSON.read_text(encoding="utf-8"))
    original_count = len(base)
    original_values = sum(len(entries) for entries in base.values())
    print(f"[基准] {BASE_JSON.name}: {original_count} 车型 / {original_values} 条")

    en_zip = tables_dir / "EN.zip"
    en_upgrades = read_table(en_zip, "Upgrades.str")
    en_manufacturers = read_table(en_zip, "List_PartManufacturer.str")
    en_types = read_table(en_zip, "UpgradeTypes.str")

    extracted = PartExtraction()
    if db_arg:
        db_path = Path(db_arg)
        summary = gamedbmem.validate_gamedb(db_path)
        cars_dir = find_cars_dir(game_input or tables_dir, tables_dir)
        if cars_dir is None:
            raise gamedbmem.GameDBError("找不到 media/Cars，无法逐车读取 Manifest.xml")
        with closing(gamedbmem.connect_readonly(db_path)) as conn:
            extracted = generate_part_models(
                conn, summary.tables, cars_dir, base, en_upgrades, en_manufacturers
            )
        print(
            f"[GameDB] integrity_check=ok；升级行 {extracted.db_rows}；"
            f"已覆盖现存车型 {len(extracted.covered)}；Manifest ID 对照 {extracted.manifest_links} 条；"
            f"List_Wheels {extracted.wheel_rows if extracted.wheel_rows is not None else '不可用'}"
        )
        for model, entries in extracted.models.items():
            print(f"  + {model}: {len(entries)} 条")
            for key, value in entries.items():
                print(f"      {key}: {value}")
        for model, missing in extracted.manifest_missing.items():
            print(f"  ! {model}: Manifest 缺名（不阻断）: {', '.join(missing)}")
        for issue in extracted.issues:
            print("  ! " + issue)
        base.update(extracted.models)

    values = [v for entries in base.values() for v in entries.values()]
    if extracted.models:
        print(f"[合并] {original_count} → {len(base)} 车型；只新增车型，不修改现存条目")
        if write:
            backup = ROOT / "backups" / f"part_names_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
            backup.write_bytes(BASE_JSON.read_bytes())
            dump_models(BASE_JSON, {}, base)
            print(f"[英文] 已追加 {len(extracted.models)} 车型；备份 {backup.name}")

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
                    translated[key] = value
                else:
                    hits += 1
                    translated[key] = text
            models[model] = translated

        coverage = hits / len(values) if values else 0.0
        print(f"[{code}] {hits}/{len(values)} 命中 ({coverage:.2%}), "
              f"未命中 {sum(misses.values())} 条 / {len(misses)} 种")
        for value, count in sorted(misses.items(), key=lambda kv: -kv[1]):
            print(f"        {count}× {value}")

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
