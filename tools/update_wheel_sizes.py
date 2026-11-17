# -*- coding: utf-8 -*-
"""tools/update_wheel_sizes.py — 从明文 GameDB 补齐缺失车型的原厂轮组几何。

用法:
    python tools/update_wheel_sizes.py PATH/gamedb.sqlite
    python tools/update_wheel_sizes.py PATH/gamedb.sqlite --write

输入必须通过只读 SQLite 打开、PRAGMA integrity_check 和 Data_Car 锚点检查。
车型代码取 Data_Car.MediaName，轮胎尺寸取 Data_Car；车宽/离地间隙通过
List_UpgradeCarBody(Ordinal=Data_Car.Id, IsStock=1).CarBodyID 精确连接到
Data_CarBody.ID。该连接由游戏 exe 内嵌 SQL 证实；任何所需表/列缺失都会报错，
不按名称相似度猜车型，也不从其他车型复制数值。

默认只报告。--write 只追加现存文件中不存在的 model code（不区分大小写），
保留所有现值、_comment 和 _default，写入前备份到 backups/。非数值、非有限值、
非正值或明显超出合理范围的值会被拒绝并逐条报告。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gamedbmem  # noqa: E402  (ROOT 必须先入 sys.path)

WHEEL_SIZES_JSON = ROOT / "viewer3d" / "assets" / "cars" / "wheel_sizes.json"
MODEL_CODE_RE = re.compile(r"^[A-Za-z0-9_]+$")

REQUIRED_SCHEMA = {
    "Data_Car": {
        "Id", "MediaName",
        "FrontTireWidthMM", "FrontTireAspect", "FrontWheelDiameterIN",
        "RearTireWidthMM", "RearTireAspect", "RearWheelDiameterIN",
    },
    "List_UpgradeCarBody": {"Id", "Ordinal", "CarBodyID", "IsStock"},
    "Data_CarBody": {
        "ID", "ModelFrontTrackOuter", "ModelRearTrackOuter",
        "ModelFrontStockRideHeight", "ModelRearStockRideHeight",
    },
}

QUERY = """
SELECT
    c.Id AS CarId,
    c.MediaName AS MediaName,
    stock.Id AS StockCarBodyPartId,
    c.FrontTireWidthMM AS FrontTireWidthMM,
    c.FrontTireAspect AS FrontTireAspect,
    c.FrontWheelDiameterIN AS FrontWheelDiameterIN,
    c.RearTireWidthMM AS RearTireWidthMM,
    c.RearTireAspect AS RearTireAspect,
    c.RearWheelDiameterIN AS RearWheelDiameterIN,
    b.ModelFrontTrackOuter AS ModelFrontTrackOuter,
    b.ModelRearTrackOuter AS ModelRearTrackOuter,
    b.ModelFrontStockRideHeight AS ModelFrontStockRideHeight,
    b.ModelRearStockRideHeight AS ModelRearStockRideHeight
FROM Data_Car AS c
JOIN List_UpgradeCarBody AS stock
    ON stock.Ordinal = c.Id AND stock.IsStock = 1
JOIN Data_CarBody AS b ON b.ID = stock.CarBodyID
ORDER BY c.Id, stock.Id
"""


@dataclass
class WheelExtraction:
    entries: dict[str, dict[str, dict[str, int | float]]] = field(default_factory=dict)
    covered: set[str] = field(default_factory=set)
    issues: list[str] = field(default_factory=list)


def require_schema(tables: dict[str, tuple[str, ...]]) -> None:
    actual = {name.casefold(): {col.casefold() for col in cols} for name, cols in tables.items()}
    missing: list[str] = []
    for table, columns in REQUIRED_SCHEMA.items():
        present = actual.get(table.casefold())
        if present is None:
            missing.append(f"{table} (整表)")
            continue
        absent = sorted(
            (column for column in columns if column.casefold() not in present),
            key=str.casefold,
        )
        if absent:
            missing.append(f"{table}: {', '.join(absent)}")
    if missing:
        raise gamedbmem.GameDBError(
            "无法建立已验证的 Data_Car → 原厂 CarBody → Data_CarBody 映射；缺少 "
            + "; ".join(missing)
        )


def checked_number(
    value: object, label: str, minimum: float, maximum: float
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 不是数值: {value!r}")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{label} 超出合理范围 [{minimum}, {maximum}]: {value!r}")
    return int(value) if isinstance(value, int) or number.is_integer() else number


def axle_entry(
    row: sqlite3.Row, prefix: str, track_column: str, ride_column: str
) -> dict[str, int | float]:
    return {
        "width": checked_number(row[f"{prefix}TireWidthMM"], f"{prefix} tire width", 50, 1000),
        "aspect": checked_number(row[f"{prefix}TireAspect"], f"{prefix} aspect", 1, 200),
        "rim": checked_number(row[f"{prefix}WheelDiameterIN"], f"{prefix} rim", 3, 50),
        "track": checked_number(row[track_column], f"{prefix} track", 0.05, 5),
        "ride": checked_number(row[ride_column], f"{prefix} ride", 0.005, 3),
    }


def extract_missing(
    conn: sqlite3.Connection,
    tables: dict[str, tuple[str, ...]],
    existing_codes: set[str],
) -> WheelExtraction:
    require_schema(tables)
    result = WheelExtraction()
    folded_existing = {code.casefold() for code in existing_codes}
    new_names: dict[str, str] = {}
    invalid: set[str] = set()
    for row in conn.execute(QUERY):
        code_value = row["MediaName"]
        if not isinstance(code_value, str) or not MODEL_CODE_RE.fullmatch(code_value):
            result.issues.append(f"车 ID {row['CarId']}: 非法 MediaName {code_value!r}")
            continue
        code = code_value.strip()
        folded = code.casefold()
        if folded in folded_existing:
            result.covered.add(folded)
            continue
        if folded in invalid:
            continue
        try:
            entry = {
                "front": axle_entry(
                    row, "Front", "ModelFrontTrackOuter", "ModelFrontStockRideHeight"
                ),
                "rear": axle_entry(
                    row, "Rear", "ModelRearTrackOuter", "ModelRearStockRideHeight"
                ),
            }
        except ValueError as exc:
            invalid.add(folded)
            previous_code = new_names.get(folded)
            if previous_code is not None:
                result.entries.pop(previous_code, None)
            result.issues.append(f"{code} (车 ID {row['CarId']}): {exc}；整项拒绝")
            continue
        previous_code = new_names.get(folded)
        if previous_code is not None:
            old = result.entries[previous_code]
            if old != entry:
                invalid.add(folded)
                del result.entries[previous_code]
                result.issues.append(f"{code}: 多个原厂 CarBody 给出冲突值，整项拒绝")
                continue
            continue
        result.entries[code] = entry
        new_names[folded] = code
    return result


def dump_wheel_sizes(data: dict[str, object], newline: str) -> bytes:
    lines = ["{"]
    items = list(data.items())
    for index, (key, value) in enumerate(items):
        comma = "," if index + 1 < len(items) else ""
        rendered = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if isinstance(value, dict):
            rendered = "{ " + rendered[1:-1].strip() + " }"
        lines.append(f"  {json.dumps(key)}: {rendered}{comma}")
    lines.append("}")
    return (newline.join(lines) + newline).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("db", type=Path, help="已通过内存提取的明文 GameDB")
    parser.add_argument("--write", action="store_true", help="只追加缺失车型并备份")
    args = parser.parse_args(argv)

    summary = gamedbmem.validate_gamedb(args.db)
    print(
        f"[GameDB] {summary.path}；integrity_check=ok；"
        f"Data_Car/Data_CarBody 映射检查中"
    )
    data: dict[str, object] = json.loads(WHEEL_SIZES_JSON.read_text(encoding="utf-8"))
    existing_codes = {key for key in data if not key.startswith("_")}
    with closing(gamedbmem.connect_readonly(args.db)) as conn:
        extracted = extract_missing(conn, summary.tables, existing_codes)

    print(
        f"[对照] 现存 {len(existing_codes)} 车型；数据库命中现存 {len(extracted.covered)}；"
        f"可新增 {len(extracted.entries)}；拒绝/异常 {len(extracted.issues)}"
    )
    for code, entry in extracted.entries.items():
        print(f"  + {code}: {json.dumps(entry, ensure_ascii=False, allow_nan=False)}")
    for issue in extracted.issues:
        print("  ! " + issue)

    if not args.write:
        print("报告模式：未写文件；确认后加 --write，只追加以上缺失车型。")
        return 0
    if not extracted.entries:
        print("[--write] 没有可追加车型，不写文件。")
        return 0

    merged = dict(data)
    for code, entry in extracted.entries.items():
        if code in merged or code.casefold() in {key.casefold() for key in merged}:
            raise gamedbmem.GameDBError(f"写入前检测到车型冲突，拒绝覆盖: {code}")
        merged[code] = entry
    raw = WHEEL_SIZES_JSON.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    backup = ROOT / "backups" / f"wheel_sizes_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
    backup.write_bytes(raw)
    WHEEL_SIZES_JSON.write_bytes(dump_wheel_sizes(merged, newline))
    print(f"[--write] 已追加 {len(extracted.entries)} 车型；备份 {backup.name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (gamedbmem.GameDBError, sqlite3.Error, json.JSONDecodeError) as exc:
        raise SystemExit(f"wheel_sizes 更新失败: {exc}") from exc
