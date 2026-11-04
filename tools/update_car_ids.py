# -*- coding: utf-8 -*-
"""tools/update_car_ids.py — 同步 3D 查看器的 car_ids.json（独立 ID/模型路径）。

用法:
    python tools/update_car_ids.py                  # 报告缺失/冲突, 不写文件
    python tools/update_car_ids.py --check          # 自动化门禁: 覆盖不全/有冲突时退出码 1
    python tools/update_car_ids.py --write          # 只补缺失 ID, 不改现存条目
    python tools/update_car_ids.py --allow-short-names --write
                                                    # 名字未定时用 Data_Car 短名先落库
    python tools/update_car_ids.py --emit-map[=PATH]
                                                    # 导出纯 ID→模型代码表(默认 build/car_id_map.json)
    python tools/update_car_ids.py --game PATH      # 手动指定游戏根目录
    python tools/update_car_ids.py --registry PATH  # 手动指定 car_ids.json

ID 锚点: <游戏>/media/Cars/<CODE>.zip 内的 <CODE>.carbin 头 "Ordinal" 字段。
该值与存档 Livery_<ID>_<时间戳> 的车 ID 一致; 全量实测与
Scene/animations/Mojo/clip/carclips_<ID>.clipd 文件名逐车相同, 两路互证。
这一段只依赖游戏文件, 不依赖 cars.json 的名称/年份, 可单独作为 ID 更新路径。
名称: cars.json fh6 现值优先; 缺失时用游戏串表 "Own the 2025 …" 文案
(与 tools/update_cars.py 同一匹配规则); 仍无唯一匹配则报待人工。
`--allow-short-names` 仅在名字待人工时用 Data_Car 的 ModelShort 兜底, 让查看器先能加载模型。
只读: zip 只读打开, 不解压落盘; 现存条目永不覆盖。
"""
from __future__ import annotations

import json
import re
import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.update_cars import (  # noqa: E402  (ROOT 必须先入 sys.path)
    CARS_JSON,
    load_disk_captions,
    load_str_entries,
    match_captions,
)

CAR_IDS_JSON = ROOT / "viewer3d" / "assets" / "cars" / "car_ids.json"


def find_game_root(arg: str | None) -> Path | None:
    if arg:
        return Path(arg)
    try:
        import flsbridge
        root = flsbridge.detect_game_root()
    except Exception:
        root = None
    if root:
        return Path(root)
    from tools.update_cars import find_game_str
    str_path = find_game_str()
    return str_path.parents[3] if str_path else None


def carbin_identity(data: bytes) -> tuple[int, str]:
    """读 .carbin Scene 头: version, [BuildGuid], [BuildStrict], Ordinal, MediaName。"""
    o = 0

    def need(n: int) -> None:
        if o + n > len(data):
            raise ValueError("unexpected end of carbin header")

    def u8() -> int:
        nonlocal o
        need(1)
        v = data[o]
        o += 1
        return v

    def u16() -> int:
        nonlocal o
        need(2)
        v = struct.unpack_from("<H", data, o)[0]
        o += 2
        return v

    def u32() -> int:
        nonlocal o
        need(4)
        v = struct.unpack_from("<I", data, o)[0]
        o += 4
        return v

    def i32() -> int:
        nonlocal o
        need(4)
        v = struct.unpack_from("<i", data, o)[0]
        o += 4
        return v

    version = u16()
    if version >= 3:
        need(16)
        o += 16
    if version >= 5:
        u8()
    ordinal = u32()
    length = i32()
    if length < 0 or length > (1 << 20):
        raise ValueError(f"bad mediaName length {length}")
    need(length)
    media = data[o:o + length].decode("latin1")
    return ordinal, media


def scan_game(game_root: Path) -> tuple[dict[int, str], list[tuple[str, str]]]:
    cars_dir = game_root / "media" / "Cars"
    if not cars_dir.is_dir():
        raise SystemExit(f"不是有效的 FH6 游戏目录: {cars_dir} 不存在")
    game: dict[int, str] = {}
    problems: list[tuple[str, str]] = []
    for path in sorted(cars_dir.glob("*.zip")):
        code = path.stem
        try:
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if "/" not in n and n.lower().endswith(".carbin")]
                if len(names) != 1:
                    names = [n for n in z.namelist() if n.lower().endswith(".carbin")]
                if len(names) != 1:
                    raise ValueError(f"{len(names)} 个 .carbin, 无法确定主文件")
                ordinal, media = carbin_identity(z.read(names[0]))
        except Exception as exc:  # noqa: BLE001  单个坏包不阻断全量报告
            problems.append((code, str(exc)))
            continue
        if media.lower() != code.lower():
            problems.append((code, f"mediaName={media!r} 与文件名不符"))
        if ordinal in game:
            problems.append((str(ordinal), f"ID 重复: {game[ordinal]} / {code}"))
            continue
        game[ordinal] = code
    return game, problems


def viewer_name(full: str) -> str | None:
    """'2025 Porsche Macan GTS' → 'Porsche Macan GTS 25' (上游 car_ids 命名风格)。"""
    m = re.match(r"^(\d{4})\s+(.+)$", full.strip())
    if not m:
        return None
    return f"{m.group(2)} {int(m.group(1)) % 100:02d}"


def short_name(ds: str, ms: str) -> str:
    """名字未定时的兜底: Data_Car 的 ModelShort 优先, 空则 DisplayName。"""
    return re.sub(r"\s+", " ", (ms or ds).strip())


def emit_id_map(path: Path, game: dict[int, str]) -> None:
    """导出纯 ID→模型代码表; path 为 '-' 时写 stdout。"""
    text = json.dumps({str(i): game[i] for i in sorted(game)},
                      ensure_ascii=False, indent=2) + "\n"
    if str(path) == "-":
        print(text, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"[ID 表] {path} → {len(game)} 条")


def dump_registry(registry: dict[str, object], newline: str) -> bytes:
    # 保持现有文件顺序: 上游习惯把新条目追加在末尾, 重排会制造无意义 diff。
    items = list(registry.items())
    lines = ["{"]
    for i, (key, value) in enumerate(items):
        comma = "," if i + 1 < len(items) else ""
        lines.append(f'"{key}": {json.dumps(value, ensure_ascii=False)}{comma}')
    lines.append("}")
    return (newline.join(lines) + newline).encode("utf-8")


def main(argv: list[str]) -> int:
    args = set(argv)
    game_arg = next((a.split("=", 1)[1] for a in argv if a.startswith("--game=")), None)
    registry_arg = next((a.split("=", 1)[1] for a in argv if a.startswith("--registry=")), None)
    map_arg = next((a.split("=", 1)[1] for a in argv if a.startswith("--emit-map=")), None)
    write = "--write" in args
    check = "--check" in args
    allow_short = "--allow-short-names" in args
    emit_map = "--emit-map" in args or map_arg is not None
    if check and write:
        raise SystemExit("--check 与 --write 不能同时使用")

    game_root = find_game_root(game_arg)
    if not game_root:
        raise SystemExit("未找到 FH6 游戏目录(用 --game=PATH 手动指定)")
    registry_path = Path(registry_arg) if registry_arg else CAR_IDS_JSON

    game, problems = scan_game(game_root)
    if emit_map:
        emit_id_map(Path(map_arg) if map_arg else ROOT / "build" / "car_id_map.json", game)
    registry: dict[str, object] = json.loads(registry_path.read_text(encoding="utf-8"))
    cars: dict[str, str] = json.loads(CARS_JSON.read_text(encoding="utf-8"))["fh6"]

    str_path = game_root / "media" / "Stripped" / "StringTables" / "EN.zip"
    entries = load_str_entries(str_path) if str_path.is_file() else {}
    disk_hit = match_captions(entries, load_disk_captions(str_path)) if entries else {}

    missing: list[tuple[int, str, str, str]] = []
    pending: list[tuple[int, str, str, str]] = []
    covered = 0
    for car_id in sorted(game):
        key = str(car_id)
        code = game[car_id]
        if key in registry:
            covered += 1
            entry = registry[key]
            model = entry.get("model") if isinstance(entry, dict) else None
            if model and model.lower() != code.lower():
                problems.append((key, f"现存 model={model!r} 与游戏 {code!r} 不符"))
            version = entry.get("version") if isinstance(entry, dict) else None
            if version and "FH6" not in version.upper():
                problems.append((key, f"现存 version={version!r} 未含 FH6"))
            continue
        full = cars.get(key)
        source = "cars.json"
        if full is None and car_id in disk_hit:
            name, year = disk_hit[car_id]
            full = f"{year} {name}"
            source = "串表文案"
        name = viewer_name(full) if full is not None else None
        if name is None and full is not None:
            name = full.strip()
        if name is None and allow_short:
            ds, ms = entries.get(car_id, ("", ""))
            name = short_name(ds, ms)
            source = "Data_Car 短名"
        if not name:
            ds, ms = entries.get(car_id, ("", ""))
            pending.append((car_id, code, ds, ms))
            continue
        missing.append((car_id, code, name, source))

    print(f"[游戏] {game_root} → media/Cars {len(game)} 辆")
    print(f"[注册表] {registry_path} → {len(registry)} 条, 已覆盖 {covered} 辆")
    print(f"\n[缺失] {len(missing)} 条(可补):")
    for car_id, code, name, source in missing:
        print(f"  + {car_id} {code}: {name}   <{source}>")
    print(f"\n[待人工] {len(pending)} 条(无唯一全名, 不猜):")
    for car_id, code, ds, ms in pending:
        print(f"  ? {car_id} {code}: ds={ds!r} ms={ms!r}")
    print(f"\n[冲突] {len(problems)} 条:")
    for key, msg in problems[:20]:
        print(f"  ! {key}: {msg}")
    if len(problems) > 20:
        print(f"  … 另有 {len(problems) - 20} 条")

    if check:
        ok = not missing and not pending and not problems
        print(f"\n[check] {'OK' if ok else 'FAIL'}: "
              f"缺失 {len(missing)} / 待人工 {len(pending)} / 冲突 {len(problems)}"
              f"（游戏 {len(game)} 辆, 已覆盖 {covered} 辆）")
        return 0 if ok else 1
    if not write:
        print("\n(报告模式。确认后加 --write 只补缺失条目, 现存条目不改)")
        print("(纯 ID 路径可加 --allow-short-names; 自动化门禁用 --check)")
        return 0
    if not missing:
        print("\n[--write] 无缺失条目, 不写文件")
        return 0
    for car_id, code, name, _source in missing:
        registry[str(car_id)] = {"model": code, "name": name, "version": "FH6"}
    raw = registry_path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    registry_path.write_bytes(dump_registry(registry, newline))
    print(f"\n[--write] 已补 {len(missing)} 条, 现存 {covered} 条未改")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
