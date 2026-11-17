# -*- coding: utf-8 -*-
"""gamedbmem.py — 从运行中的 FH6 只读提取已解密的 SQLite GameDB 原型。

用法:
    python gamedbmem.py --output build/gamedb_memory.sqlite
    python gamedbmem.py --output build/gamedb_memory.sqlite --yes
    python gamedbmem.py --inspect build/gamedb_memory.sqlite

内存模式先要求用户确认风险，再枚举 forzahorizon6.exe 的已提交私有可读页，
寻找 SQLite 头并按头中的页大小/页数读取连续字节。候选只有同时通过只读
SQLite 打开、PRAGMA integrity_check 和 Data_Car 表检查才会保留为输出文件。
不会启动游戏，不写进程内存，不注入、不调试、不下 hook，也不覆盖已有输出。

--inspect 只读检查现成的明文数据库，并实际查询 Data_Car、Data_CarBody、
List_Wheels 及名称含 Upgrade 的表，输出列、行数和少量样例。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import sqlite3
import struct
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass, field, replace
from pathlib import Path

from gamemem import GameMemoryReader, find_game_pid

SQLITE_MAGIC = b"SQLite format 3\x00"
SQLITE_HEADER_SIZE = 100
SCAN_CHUNK_SIZE = 4 * 1024 * 1024
DUMP_CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_DB_SIZE = 512 * 1024 * 1024

MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
READABLE_PROTECTIONS = {
    0x02,  # PAGE_READONLY
    0x04,  # PAGE_READWRITE
    0x08,  # PAGE_WRITECOPY
    0x20,  # PAGE_EXECUTE_READ
    0x40,  # PAGE_EXECUTE_READWRITE
    0x80,  # PAGE_EXECUTE_WRITECOPY
}

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.VirtualQueryEx.argtypes = [wt.HANDLE, wt.LPCVOID, wt.LPVOID, ctypes.c_size_t]
_k32.VirtualQueryEx.restype = ctypes.c_size_t
_k32.ReadProcessMemory.argtypes = [
    wt.HANDLE, wt.LPCVOID, wt.LPVOID, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
]
_k32.ReadProcessMemory.restype = wt.BOOL


class GameDBError(RuntimeError):
    """GameDB 文件、结构或完整性检查失败。"""


class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", wt.LPVOID),
        ("AllocationBase", wt.LPVOID),
        ("AllocationProtect", wt.DWORD),
        ("_pad", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("_pad2", wt.DWORD),
    ]


@dataclass(frozen=True)
class SQLiteCandidate:
    address: int
    page_size: int
    page_count: int

    @property
    def size(self) -> int:
        return self.page_size * self.page_count


@dataclass
class ScanStats:
    regions: int = 0
    bytes_scanned: int = 0
    read_failures: int = 0
    candidates: int = 0
    dump_attempts: int = 0
    rejections: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TableQuery:
    name: str
    columns: tuple[str, ...]
    row_count: int
    rows: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class DatabaseSummary:
    path: Path
    page_size: int
    page_count: int
    tables: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ExtractionResult:
    stats: ScanStats
    candidate: SQLiteCandidate | None = None
    database: DatabaseSummary | None = None


def quote_identifier(name: str) -> str:
    """只引用已从 sqlite_master 读取的标识符。"""
    return '"' + name.replace('"', '""') + '"'


def connect_readonly(path: Path) -> sqlite3.Connection:
    """以 SQLite URI mode=ro 打开，并额外启用 query_only。"""
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def schema(conn: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return {
        row["name"]: tuple(
            col["name"] for col in conn.execute(
                f"PRAGMA table_xinfo({quote_identifier(row['name'])})"
            )
        )
        for row in rows
    }


def validate_gamedb(path: Path) -> DatabaseSummary:
    """验证明文 SQLite 完整性及 GameDB 锚点；不做任何写操作。"""
    if not path.is_file():
        raise GameDBError(f"数据库不存在: {path}")
    with path.open("rb") as fh:
        header = fh.read(SQLITE_HEADER_SIZE)
    if len(header) != SQLITE_HEADER_SIZE or not header.startswith(SQLITE_MAGIC):
        raise GameDBError(f"不是明文 SQLite 数据库: {path}")

    page_size = struct.unpack_from(">H", header, 16)[0]
    page_size = 65536 if page_size == 1 else page_size
    page_count = struct.unpack_from(">I", header, 28)[0]
    try:
        with closing(connect_readonly(path)) as conn:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            problems = [str(row[0]) for row in rows]
            if problems != ["ok"]:
                raise GameDBError(
                    "PRAGMA integrity_check 失败: " + "; ".join(problems[:10])
                )
            tables = schema(conn)
            if not any(name.casefold() == "data_car" for name in tables):
                raise GameDBError("SQLite 完整性正常，但缺少 GameDB 锚点表 Data_Car")
            if page_count == 0:
                page_count = path.stat().st_size // page_size
            return DatabaseSummary(path, page_size, page_count, tables)
    except sqlite3.Error as exc:
        raise GameDBError(f"只读 SQLite 验证失败: {exc}") from exc


def query_gamedb(
    conn: sqlite3.Connection, tables: dict[str, tuple[str, ...]], sample_rows: int = 1
) -> list[TableQuery]:
    """实际查询核心表和升级表；表名来自调用方已读取的 schema。"""
    wanted = {"data_car", "data_carbody", "list_wheels"}
    names = [
        name for name in tables
        if name.casefold() in wanted or "upgrade" in name.casefold()
    ]
    result: list[TableQuery] = []
    for name in names:
        quoted = quote_identifier(name)
        count = conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
        rows: tuple[dict[str, object], ...] = ()
        if sample_rows:
            rows = tuple(
                dict(row) for row in conn.execute(
                    f"SELECT * FROM {quoted} LIMIT ?", (sample_rows,)
                )
            )
        result.append(TableQuery(name, tables[name], count, rows))
    return result


def parse_sqlite_header(
    header: bytes, address: int = 0, max_size: int = DEFAULT_MAX_DB_SIZE
) -> SQLiteCandidate | None:
    """严格筛掉头字段不自洽的候选，避免按伪造长度大量读内存。"""
    if len(header) < SQLITE_HEADER_SIZE or not header.startswith(SQLITE_MAGIC):
        return None
    raw_page_size = struct.unpack_from(">H", header, 16)[0]
    page_size = 65536 if raw_page_size == 1 else raw_page_size
    if not (512 <= page_size <= 65536) or page_size & (page_size - 1):
        return None
    if header[18] not in (1, 2) or header[19] not in (1, 2):
        return None
    if header[21:24] != b"\x40\x20\x20" or header[20] >= page_size:
        return None
    page_count = struct.unpack_from(">I", header, 28)[0]
    if page_count == 0:
        return None
    if header[24:28] != header[92:96]:
        return None
    candidate = SQLiteCandidate(address, page_size, page_count)
    if candidate.size > max_size:
        return None
    return candidate


class GameDBMemoryReader(GameMemoryReader):
    """复用只读 OpenProcess 句柄，支持跨 VirtualQueryEx 区域的连续读取。"""

    def regions(self) -> list[tuple[int, int]]:
        addr = 0
        max_addr = 0x7FFFFFFFFFFF if sys.maxsize > 2**32 else 0x7FFFFFFF
        mbi = _MBI()
        out: list[tuple[int, int]] = []
        while addr < max_addr:
            result = _k32.VirtualQueryEx(
                self.handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)
            )
            if not result:
                break
            base = ctypes.cast(mbi.BaseAddress, ctypes.c_void_p).value or 0
            size = mbi.RegionSize
            if (
                mbi.State == MEM_COMMIT
                and mbi.Type == MEM_PRIVATE
                and mbi.Protect in READABLE_PROTECTIONS
                and size > 0
            ):
                out.append((base, size))
            next_addr = base + size
            if next_addr <= addr:
                break
            addr = next_addr
        return out

    def read_at(self, address: int, size: int) -> bytes:
        buf = (ctypes.c_char * size)()
        read = ctypes.c_size_t()
        ok = _k32.ReadProcessMemory(
            self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read)
        )
        if not ok and read.value == 0:
            return b""
        return buf.raw[:read.value]

    def read_exact(self, address: int, size: int) -> bytes | None:
        data = self.read_at(address, size)
        return data if len(data) == size else None


def find_header_candidates(
    reader: GameDBMemoryReader, max_size: int = DEFAULT_MAX_DB_SIZE
) -> tuple[list[SQLiteCandidate], ScanStats]:
    stats = ScanStats()
    candidates: list[SQLiteCandidate] = []
    seen: set[int] = set()
    for base, region_size in reader.regions():
        stats.regions += 1
        offset = 0
        tail = b""
        while offset < region_size:
            request = min(SCAN_CHUNK_SIZE, region_size - offset)
            data = reader.read_at(base + offset, request)
            if not data:
                stats.read_failures += 1
                break
            stats.bytes_scanned += len(data)
            buf = tail + data
            buf_address = base + offset - len(tail)
            pos = buf.find(SQLITE_MAGIC)
            while pos >= 0:
                address = buf_address + pos
                if address not in seen:
                    candidate = parse_sqlite_header(
                        buf[pos:pos + SQLITE_HEADER_SIZE], address, max_size
                    )
                    if candidate is not None:
                        seen.add(address)
                        candidates.append(candidate)
                pos = buf.find(SQLITE_MAGIC, pos + 1)
            if len(data) != request:
                stats.read_failures += 1
                break
            tail = buf[-(SQLITE_HEADER_SIZE - 1):]
            offset += len(data)
    stats.candidates = len(candidates)
    return candidates, stats


def _dump_candidate(
    reader: GameDBMemoryReader, candidate: SQLiteCandidate, target: Path
) -> None:
    written = 0
    with target.open("wb") as fh:
        while written < candidate.size:
            size = min(DUMP_CHUNK_SIZE, candidate.size - written)
            data = reader.read_exact(candidate.address + written, size)
            if data is None:
                raise GameDBError(
                    f"连续读取在 +0x{written:x} 处中断（需要 {size} 字节）"
                )
            fh.write(data)
            written += size


def extract_gamedb(
    pid: int, output: Path, max_size: int = DEFAULT_MAX_DB_SIZE
) -> ExtractionResult:
    """扫描并验证候选；没有找到完整 GameDB 时返回无 database 的结果。"""
    if output.exists():
        raise GameDBError(f"拒绝覆盖已有输出: {output}")
    with GameDBMemoryReader(pid) as reader:
        candidates, stats = find_header_candidates(reader, max_size)
        for candidate in candidates:
            stats.dump_attempts += 1
            output.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=".gamedb-", suffix=".sqlite.tmp", dir=output.parent
            )
            os.close(fd)
            temp = Path(temp_name)
            try:
                _dump_candidate(reader, candidate, temp)
                summary = validate_gamedb(temp)
            except (GameDBError, OSError, sqlite3.Error) as exc:
                stats.rejections.append(f"0x{candidate.address:x}: {exc}")
                temp.unlink(missing_ok=True)
                continue
            try:
                os.link(temp, output)
                return ExtractionResult(
                    stats, candidate, replace(summary, path=output)
                )
            finally:
                temp.unlink(missing_ok=True)
    return ExtractionResult(stats)


def print_database(summary: DatabaseSummary, sample_rows: int) -> None:
    print(
        f"已验证 {summary.path}: page_size={summary.page_size}, "
        f"pages={summary.page_count}, tables={len(summary.tables)}"
    )
    with closing(connect_readonly(summary.path)) as conn:
        for table in query_gamedb(conn, summary.tables, sample_rows):
            print(f"[{table.name}] {table.row_count} 行; 列: {', '.join(table.columns)}")
            for row in table.rows:
                print("  " + repr(row))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--output", type=Path, default=Path("build/gamedb_memory.sqlite"))
    parser.add_argument("--inspect", type=Path, help="只读检查已有明文 GameDB，不扫描内存")
    parser.add_argument("--pid", type=int, help="指定游戏 PID（默认按进程名查找）")
    parser.add_argument("--yes", action="store_true", help="确认已了解只读内存扫描风险")
    parser.add_argument("--max-size-mib", type=int, default=512)
    parser.add_argument("--sample-rows", type=int, default=1)
    args = parser.parse_args(argv)
    if args.sample_rows < 0 or args.max_size_mib <= 0:
        parser.error("--sample-rows 必须 >= 0，--max-size-mib 必须 > 0")

    if args.inspect:
        summary = validate_gamedb(args.inspect)
        print_database(summary, args.sample_rows)
        return 0

    pid = args.pid or find_game_pid()
    if not pid:
        print("未找到正在运行的 forzahorizon6.exe；未扫描内存，未创建数据库。")
        return 2
    if not args.yes:
        print(
            "即将对游戏进程执行 OpenProcess + ReadProcessMemory 只读扫描。\n"
            "不注入、不调试、不写内存；仍建议离线进行，并自行确认相关风险。"
        )
        if input("输入 YES 继续: ") != "YES":
            print("用户未确认；未扫描内存。")
            return 2

    print(f"开始只读扫描 PID {pid}；不会启动游戏或修改进程。")
    result = extract_gamedb(pid, args.output, args.max_size_mib * 1024 * 1024)
    stats = result.stats
    print(
        f"扫描 {stats.regions} 个区域 / {stats.bytes_scanned} 字节；"
        f"读取失败 {stats.read_failures} 处；有效头候选 {stats.candidates} 个；"
        f"转储尝试 {stats.dump_attempts} 次。"
    )
    if result.database is None:
        print("未找到通过完整性及 Data_Car 校验的连续明文 GameDB；未保留候选文件。")
        for reason in stats.rejections[:20]:
            print("  拒绝 " + reason)
        if len(stats.rejections) > 20:
            print(f"  … 另有 {len(stats.rejections) - 20} 条拒绝原因")
        return 1
    assert result.candidate is not None
    print(f"采用内存地址 0x{result.candidate.address:x} 的候选。")
    print_database(result.database, args.sample_rows)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GameDBError, OSError, sqlite3.Error) as exc:
        raise SystemExit(f"GameDB 提取失败: {exc}") from exc
