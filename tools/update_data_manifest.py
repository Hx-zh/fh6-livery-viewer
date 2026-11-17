# -*- coding: utf-8 -*-
"""tools/update_data_manifest.py — 生成/校验全部 JSON 数据的在线更新清单。

用法:
    python tools/update_data_manifest.py            # 报告 HEAD 数据与清单是否一致
    python tools/update_data_manifest.py --check    # 不一致/缺清单时退出码 1
    python tools/update_data_manifest.py --write    # 写入 viewer3d/assets/data_manifest.json

清单从 **HEAD blob** 计算 hash/字节数，不用工作区文件；这样 GitHub/Gitee raw 的
字节不会因 Windows autocrlf 被误判。数据文件须先提交，再生成并提交清单。
清单不变时保留原来的 updated/commit，避免无关提交制造数据更新。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "viewer3d" / "assets" / "data_manifest.json"

# (仓库内路径, 缓存内目标路径, 数据类型)
DATA_FILES = (
    ("cars.json", "cars_online.json", "json"),
    ("viewer3d/assets/cars/car_ids.json", "viewer_assets/cars/car_ids.json", "json"),
    ("viewer3d/assets/cars/part_names.json", "viewer_assets/cars/part_names.json", "json"),
    ("viewer3d/assets/cars/part_names.zh-cn.json", "viewer_assets/cars/part_names.zh-cn.json", "json"),
    ("viewer3d/assets/cars/part_names.zh-tw.json", "viewer_assets/cars/part_names.zh-tw.json", "json"),
    ("viewer3d/assets/cars/part_names.ja.json", "viewer_assets/cars/part_names.ja.json", "json"),
    ("viewer3d/assets/cars/part_names.ko.json", "viewer_assets/cars/part_names.ko.json", "json"),
    ("viewer3d/assets/cars/wheel_sizes.json", "viewer_assets/cars/wheel_sizes.json", "json"),
    ("viewer3d/assets/vector/shape_names.json", "viewer_assets/vector/shape_names.json", "json"),
    ("viewer3d/assets/vector/shape_geometry.json.gz", "viewer_assets/vector/shape_geometry.json.gz", "json.gz"),
)


def head_bytes(path: str) -> bytes:
    r = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=ROOT,
                       capture_output=True, check=True)
    return r.stdout


def file_entry(path: str, target: str, kind: str) -> dict[str, object]:
    raw = head_bytes(path)
    if kind == "json":
        json.loads(raw.decode("utf-8"))
    else:
        json.loads(gzip.decompress(raw).decode("utf-8"))
    return {"path": path, "target": target, "kind": kind,
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def build_manifest(previous: dict | None = None) -> dict:
    files = [file_entry(*item) for item in DATA_FILES]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()
    previous_files = previous.get("files") if isinstance(previous, dict) else None
    unchanged = isinstance(previous_files, list) and previous_files == files
    if unchanged and previous is not None:
        stamp = previous.get("updated", "")
        commit = previous.get("commit", head)
    else:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        commit = head
    return {"schema": 1, "updated": stamp, "commit": commit, "files": files}


def load_previous() -> dict | None:
    try:
        obj = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def same_data(a: dict | None, b: dict) -> bool:
    return isinstance(a, dict) and a.get("schema") == 1 and a.get("files") == b["files"]


def main(argv: list[str]) -> int:
    write = "--write" in argv
    check = "--check" in argv
    previous = load_previous()
    current = build_manifest(previous)
    previous_files = previous.get("files", []) if previous else []
    old_by_path = {f.get("path"): f for f in previous_files if isinstance(f, dict)}
    changed = [f["path"] for f in current["files"] if old_by_path.get(f["path"]) != f]
    if previous is None:
        changed = [f["path"] for f in current["files"]]
    print(f"[清单] {len(current['files'])} 个数据文件; 变化 {len(changed)} 个")
    for path in changed:
        print(f"  ~ {path}")
    if not same_data(previous, current):
        print("[清单] 数据内容与现有清单不一致")
    if check:
        return 0 if same_data(previous, current) else 1
    if not write:
        print("(报告模式。确认后加 --write 落地; 数据文件须先提交)")
        return 0 if same_data(previous, current) else 2
    if same_data(previous, current):
        print("[--write] 数据清单已是最新, 不写文件")
        return 0
    MANIFEST.write_bytes((json.dumps(current, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"[--write] 已写入 {MANIFEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
