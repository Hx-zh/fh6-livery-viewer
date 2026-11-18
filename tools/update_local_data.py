# -*- coding: utf-8 -*-
"""tools/update_local_data.py — 本机 GameDB → 全部车辆 JSON → 在线更新。

用法（仓库根目录）：
    python tools/update_local_data.py                    # 只解密、校验、报告，不改 JSON
    python tools/update_local_data.py --write            # 写入 JSON，不提交、不推送
    python tools/update_local_data.py --push             # 写入、提交并执行 data-only 发布
    python tools/update_local_data.py --gamedb build/fh6_gamedb_open_source.sqlite

默认自动定位 FH6，并调用 build/private-fh6-gamedb/decrypt_fh6_gamedb.py。
该私有解密器及 key 不进入 Git；也可用 --gamedb 提供已通过完整性校验的明文库。
--game PATH 可指定游戏根目录。--push 隐含 --write，并推送 origin/main，同时沿用
release_build.py --data-only 的 Gitee 镜像与双端 raw 校验；不会打 tag 或发布 exe。

新车型全名只接受磁盘完整文案，且文案年份必须与 GameDB 一致；现存名称和年份均保留。
4263 沿用人工审定的同实体残留结论，永不自动入库。无可靠名称、schema 不符、carbin
不匹配或生成器报告异常时拒绝写入/推送。所有生成器只追加新资产，现存资产不覆盖、不删除。
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gamedbmem  # noqa: E402
from tools import update_car_ids, update_cars, update_data_manifest  # noqa: E402
from tools import update_part_names, update_wheel_sizes  # noqa: E402

PRIVATE_DECRYPTOR = ROOT / "build" / "private-fh6-gamedb" / "decrypt_fh6_gamedb.py"
WORK_DB = ROOT / "build" / "local_data_update" / "gamedb.sqlite"
ASSETS = ROOT / "viewer3d" / "assets" / "cars"
MANIFEST = ROOT / "viewer3d" / "assets" / "data_manifest.json"
DATA_PATHS = (
    ROOT / "cars.json",
    ASSETS / "car_ids.json",
    ASSETS / "part_names.json",
    ASSETS / "part_names.zh-cn.json",
    ASSETS / "part_names.zh-tw.json",
    ASSETS / "part_names.ja.json",
    ASSETS / "part_names.ko.json",
    ASSETS / "wheel_sizes.json",
)
CURATED_EXCLUDED_CAR_IDS = {4263}


class LocalUpdateError(RuntimeError):
    pass


@dataclass
class CarsPlan:
    data: dict
    added: list[tuple[int, str]]
    changed: list[tuple[int, str, str]]
    pending: list[str]
    removed: list[int]


@dataclass
class UpdatePlan:
    game: Path
    db: Path
    cars: CarsPlan
    wheel: update_wheel_sizes.WheelExtraction
    parts: update_part_names.PartExtraction


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def run_process(command: list[str]) -> None:
    print("+ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode:
        raise LocalUpdateError(f"命令退出码 {result.returncode}: {command[0]}")


def git(command: list[str], capture: bool = True) -> str:
    result = subprocess.run(
        ["git", *command], cwd=ROOT, capture_output=capture, check=False
    )
    if result.returncode:
        detail = ""
        if capture:
            detail = (result.stdout + result.stderr).decode("utf-8", "replace").strip()
        raise LocalUpdateError(f"git {command[0]} 失败: {detail[:500]}")
    return result.stdout.decode("utf-8", "replace").strip() if capture else ""


def require_clean() -> None:
    dirty = git(["status", "--porcelain", "--untracked-files=no"])
    if dirty:
        raise LocalUpdateError("有未提交的跟踪文件改动，先处理后再运行:\n" + dirty)


def decrypt_if_needed(args: argparse.Namespace, game: Path) -> Path:
    if args.gamedb is not None:
        return args.gamedb.resolve()
    if not PRIVATE_DECRYPTOR.is_file():
        raise LocalUpdateError(
            f"缺少本机私有解密器 {PRIVATE_DECRYPTOR}；请恢复私有研究目录，"
            "或改用 --gamedb 指定明文 SQLite"
        )
    encrypted = game / "media" / "Stripped" / "gamedbRC.slt"
    if not encrypted.is_file():
        raise LocalUpdateError(f"GameDB 不存在: {encrypted}")
    WORK_DB.parent.mkdir(parents=True, exist_ok=True)
    run_process([
        sys.executable,
        str(PRIVATE_DECRYPTOR),
        "--input",
        str(encrypted),
        "--output",
        str(WORK_DB),
    ])
    return WORK_DB


def plan_cars(
    conn: sqlite3.Connection,
    game: Path,
    carbin: dict[int, str],
) -> tuple[CarsPlan, dict[int, tuple[str, str]], dict[int, tuple[str, int]]]:
    columns = {row[1].casefold() for row in conn.execute("PRAGMA table_info(Data_Car)")}
    required = {"id", "year", "medianame"}
    if not required <= columns:
        raise LocalUpdateError(f"Data_Car 缺少必需列: {sorted(required - columns)}")
    rows = conn.execute("SELECT Id, Year, MediaName FROM Data_Car ORDER BY Id").fetchall()
    seen_ids: set[int] = set()
    seen_media: set[str] = set()
    for row in rows:
        car_id, year, media = row
        if isinstance(car_id, bool) or not isinstance(car_id, int) or car_id <= 0:
            raise LocalUpdateError(f"非法 Data_Car.Id: {car_id!r}")
        if isinstance(year, bool) or not isinstance(year, int) or year <= 0:
            raise LocalUpdateError(f"{car_id} 非法 GameDB Year: {year!r}")
        if not isinstance(media, str) or not media.strip():
            raise LocalUpdateError(f"{car_id} 非法 MediaName: {media!r}")
        if car_id in seen_ids or media.casefold() in seen_media:
            raise LocalUpdateError(f"Data_Car 主键/车型代码重复: {car_id} {media}")
        seen_ids.add(car_id)
        seen_media.add(media.casefold())
    db_ids = {row[0] for row in rows}
    if db_ids != set(carbin):
        missing = sorted(db_ids - set(carbin))
        extra = sorted(set(carbin) - db_ids)
        raise LocalUpdateError(
            f"GameDB/carbin ID 集合不一致: GameDB 独有 {missing[:20]}；carbin 独有 {extra[:20]}"
        )
    for car_id, _year, media in rows:
        if carbin[car_id].casefold() != media.casefold():
            raise LocalUpdateError(
                f"{car_id} MediaName={media!r} 与 carbin={carbin[car_id]!r} 不一致"
            )

    en_zip = game / "media" / "Stripped" / "StringTables" / "EN.zip"
    entries = update_cars.load_str_entries(en_zip)
    disk_hit = update_cars.match_captions(entries, update_cars.load_disk_captions(en_zip))
    data = json.loads((ROOT / "cars.json").read_text(encoding="utf-8"))
    existing = data.get("fh6")
    if not isinstance(existing, dict):
        raise LocalUpdateError("cars.json 缺少 fh6 对象")
    added: list[tuple[int, str]] = []
    changed: list[tuple[int, str, str]] = []
    pending: list[str] = []
    for car_id, year, media in rows:
        if car_id in CURATED_EXCLUDED_CAR_IDS:
            continue
        old = existing.get(str(car_id))
        if old is not None:
            if not isinstance(old, str) or not old.strip():
                raise LocalUpdateError(f"cars.json 现存名称非法: {car_id} {old!r}")
            continue
        if car_id not in disk_hit:
            pending.append(f"{car_id} {media}: 无唯一磁盘完整文案")
            continue
        name, caption_year = disk_hit[car_id]
        if caption_year != year:
            pending.append(
                f"{car_id} {media}: GameDB Year={year} 与文案年份={caption_year} 冲突"
            )
            continue
        added.append((car_id, f"{year} {name}"))
    removed = sorted(set(existing) - {str(car_id) for car_id in db_ids})
    return CarsPlan(data, added, changed, pending, [int(value) for value in removed]), entries, disk_hit


def build_plan(args: argparse.Namespace) -> UpdatePlan:
    game = update_car_ids.find_game_root(args.game)
    if game is None:
        raise LocalUpdateError("未找到 FH6 游戏目录，请用 --game PATH 指定")
    game = game.resolve()
    db = decrypt_if_needed(args, game)
    summary = gamedbmem.validate_gamedb(db)
    carbin, problems = update_car_ids.scan_game(game)
    if problems:
        raise LocalUpdateError("carbin 扫描异常: " + "; ".join(f"{k}: {v}" for k, v in problems[:20]))
    with closing(gamedbmem.connect_readonly(db)) as conn:
        cars, _entries, _disk_hit = plan_cars(conn, game, carbin)
        existing_wheel = {
            key for key in json.loads(update_wheel_sizes.WHEEL_SIZES_JSON.read_text(encoding="utf-8"))
            if not key.startswith("_")
        }
        wheel = update_wheel_sizes.extract_missing(conn, summary.tables, existing_wheel)
        tables_dir = game / "media" / "Stripped" / "StringTables"
        en_upgrades = update_part_names.read_table(tables_dir / "EN.zip", "Upgrades.str")
        en_manufacturers = update_part_names.read_table(
            tables_dir / "EN.zip", "List_PartManufacturer.str"
        )
        base = json.loads(update_part_names.BASE_JSON.read_text(encoding="utf-8"))
        parts = update_part_names.generate_part_models(
            conn,
            summary.tables,
            game / "media" / "Cars",
            base,
            en_upgrades,
            en_manufacturers,
        )
    print(
        f"[快照] GameDB {len(carbin)} 辆；carbin Ordinal/MediaName 全量匹配；"
        f"cars +{len(cars.added)} ~{len(cars.changed)} ?{len(cars.pending)}；"
        f"wheel +{len(wheel.entries)}；part 新车型 +{len(parts.models)}"
    )
    return UpdatePlan(game, db, cars, wheel, parts)


def print_plan(plan: UpdatePlan) -> None:
    for car_id, full in plan.cars.added:
        print(f"  + cars {car_id}: {full}")
    for car_id, old, new in plan.cars.changed:
        print(f"  ~ cars {car_id}: {old} → {new}")
    for issue in plan.cars.pending:
        print("  ? " + issue)
    if plan.cars.removed:
        print("  - 删除候选（永不自动删除）: " + ", ".join(map(str, plan.cars.removed)))
    for code, entry in plan.wheel.entries.items():
        print(f"  + wheel {code}: {json.dumps(entry, ensure_ascii=False)}")
    for issue in plan.wheel.issues:
        print("  ! wheel " + issue)
    for model, entries in plan.parts.models.items():
        print(f"  + part {model}: {len(entries)} 条")
    for model, missing in plan.parts.manifest_missing.items():
        print(f"  ! part {model}: Manifest 缺名（不阻断）: {', '.join(missing)}")
    for issue in plan.parts.issues:
        print("  ! part " + issue)


def validate_plan(plan: UpdatePlan) -> None:
    problems = [*plan.cars.pending, *plan.wheel.issues, *plan.parts.issues]
    if problems:
        raise LocalUpdateError("预检未通过，未写入/推送:\n  " + "\n  ".join(problems))


def write_cars(plan: CarsPlan) -> None:
    if not plan.added and not plan.changed:
        return
    data = copy.deepcopy(plan.data)
    fh6 = data["fh6"]
    for car_id, full in plan.added:
        fh6[str(car_id)] = full
    for car_id, _old, new in plan.changed:
        fh6[str(car_id)] = new
    data["fh6"] = dict(sorted(fh6.items()))
    data["_updated"] = datetime.now().astimezone().isoformat(timespec="seconds")
    path = ROOT / "cars.json"
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    backup = ROOT / "backups" / f"cars_backup_local_{datetime.now():%Y%m%d_%H%M%S}.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(raw)
    path.write_bytes(
        (json.dumps(data, ensure_ascii=False, indent=2).replace("\n", newline) + newline).encode("utf-8")
    )
    print(f"[写入] cars.json +{len(plan.added)} ~{len(plan.changed)}；_updated={data['_updated']}")


def restore(snapshot: dict[Path, bytes | None]) -> None:
    for path, raw in snapshot.items():
        if raw is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(raw)


def apply_update(plan: UpdatePlan) -> list[Path]:
    validate_plan(plan)
    snapshot = {path: path.read_bytes() if path.exists() else None for path in DATA_PATHS}
    try:
        write_cars(plan.cars)
        update_car_ids.main(["--write"])
        if plan.wheel.entries:
            update_wheel_sizes.main([str(plan.db), "--write"])
        if plan.parts.models:
            if update_part_names.main([
                f"--game={plan.game}",
                f"--gamedb={plan.db}",
                "--write",
            ]):
                raise LocalUpdateError("part_names 多语言生成门禁失败")
        if update_car_ids.main(["--check"]):
            raise LocalUpdateError("car_ids.json 完整性门禁失败")
    except SystemExit as exc:
        restore(snapshot)
        raise LocalUpdateError(f"生成器中断: {exc}") from exc
    except Exception:
        restore(snapshot)
        raise
    changed = [
        path for path in DATA_PATHS
        if (path.read_bytes() if path.exists() else None) != snapshot[path]
    ]
    if not changed:
        print("[写入] 所有 JSON 已是最新，无文件变化")
    else:
        print("[写入] 变化文件: " + ", ".join(relative(path) for path in changed))
    return changed


def publish(changed: list[Path]) -> None:
    if not changed:
        print("[推送] 无数据变化，不创建提交、不推送")
        return
    run_process(["git", "fetch", "--all", "--tags"])
    behind = git(["rev-list", "--count", "HEAD..origin/main"])
    if behind != "0":
        raise LocalUpdateError(f"origin/main 已领先 {behind} 个提交；先同步上游，未提交本次数据")
    ahead = git(["log", "--oneline", "origin/main..HEAD"])
    if ahead:
        print("[推送] 本次还会包含本地已有提交:\n" + ahead)
    git(["diff", "--check", "--", *[relative(path) for path in changed]])
    git(["add", "--", *[relative(path) for path in changed]], capture=False)
    git(["commit", "-m", "chore(data): update FH6 vehicle assets"], capture=False)
    update_data_manifest.main(["--write"])
    git(["add", "--", relative(MANIFEST)], capture=False)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", relative(MANIFEST)],
        cwd=ROOT,
        check=False,
    ).returncode
    if staged == 1:
        git(["commit", "-m", "chore(data): refresh data manifest"], capture=False)
    elif staged != 0:
        raise LocalUpdateError("git diff --cached 检查失败")
    run_process([sys.executable, "release_build.py", "--data-only"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--game", help="FH6 游戏根目录")
    parser.add_argument("--gamedb", type=Path, help="使用既有明文 GameDB，跳过私有解密器")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="写入 JSON，但不提交或推送")
    mode.add_argument("--push", action="store_true", help="写入、提交并执行 data-only 双端发布")
    args = parser.parse_args(argv)
    try:
        require_clean()
        plan = build_plan(args)
        print_plan(plan)
        if not args.write and not args.push:
            problems = len(plan.cars.pending) + len(plan.wheel.issues) + len(plan.parts.issues)
            print("[报告] 未改 JSON" + (f"；预检问题 {problems} 个" if problems else "；预检通过"))
            return 2 if problems else 0
        original = {
            path: path.read_bytes() if path.exists() else None for path in DATA_PATHS
        } if args.push else {}
        head_before = git(["rev-parse", "HEAD"]) if args.push else ""
        changed = apply_update(plan)
        if args.push:
            try:
                publish(changed)
            except Exception:
                if git(["rev-parse", "HEAD"]) == head_before:
                    restore(original)
                raise
        return 0
    except (LocalUpdateError, gamedbmem.GameDBError, sqlite3.Error,
            OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
