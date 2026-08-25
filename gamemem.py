# -*- coding: utf-8 -*-
"""
gamemem.py — FH6 运行时内存只读扫描器 (检测「涂装是否喷在车上」/ 读取车辆串表)

原理 (全部实测自本机 FH6):
- 游戏把车库表 (Career_Garage) 序列化数据常驻内存。每条车库记录里,
  涂装文件名 (LiveryFileName) 形如 "Livery_<车型ID>_<14位时间戳>",
  其后紧跟 "Tuning_<车型ID>_<时间戳>" (有调校的车) 或一个 GUID 串
  (VersionedLiveryId, 无调校的车)。该签名与「存档条目列表」「存档键名表」
  均不冲突 (对真值 133/133 精确命中, 条目列表 0 误报)。
- 游戏同时把 Data_Car VALUES 串表加载进内存，可用 read_car_strings()
  只读取出全部 DisplayName / ModelShort（各 660 条），用于校对 cars.json。
- 我们只读 (OpenProcess + ReadProcessMemory), 不附加调试器/不写内存/不下 hook。

抗 ASLR: 不硬编码地址, 每次全量扫描定位记录区 (8 线程并行读, 约 3-4 秒,
瓶颈是 ReadProcessMemory 拷贝带宽); 之后缓存命中的区域地址,
本会话内刷新只重扫这些区域 (毫秒级), 定期/手动再全量扫描。
"""
from __future__ import annotations

import concurrent.futures
import ctypes
import ctypes.wintypes as wt
import re
import struct

_k32 = ctypes.WinDLL('kernel32', use_last_error=True)

PROCESS_VM_READ = 0x10
PROCESS_QUERY_INFORMATION = 0x400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_READWRITE = 0x4
PAGE_EXECUTE_READWRITE = 0x40
PAGE_WRITECOPY = 0x8
PAGE_EXECUTE_WRITECOPY = 0x80

GAME_EXE = "forzahorizon6.exe"

# 车库记录签名: Livery_<车>_<ts> 紧跟 (Tuning_<车>_<ts> | GUID)
APPLIED_RE = re.compile(
    rb'(Livery_[0-9]{3,6}_[0-9]{14})'
    rb'(?:Tuning_[0-9]{3,6}_[0-9]{14}'
    rb'|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})')

# 车辆字符串表签名: 游戏内加载的 Data_Car VALUES 串表本体。
# 顺序 = Data_Car.str 的键序 (IDS_DisplayName_* 按 ID 字符串字典序),
# 前 660 条为 DisplayName, 后 660 条为 ModelShort。
CAR_STRING_SIG = b"FXX\x00CCGT\x00Lancer Evolution X GSR\x00M3\x00"
CAR_STRING_COUNT = 1320


class _MBI(ctypes.Structure):
    _fields_ = [('BaseAddress', wt.LPVOID), ('AllocationBase', wt.LPVOID),
                ('AllocationProtect', wt.DWORD), ('__pad', wt.DWORD),
                ('RegionSize', ctypes.c_size_t), ('State', wt.DWORD),
                ('Protect', wt.DWORD), ('Type', wt.DWORD), ('__pad2', wt.DWORD)]


def find_game_pid(exe: str = GAME_EXE) -> int | None:
    """按进程名找 PID (toolhelp32 快照)。"""
    TH32CS_SNAPPROCESS = 0x2
    h = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if h in (-1, 0):
        return None

    class PE32(ctypes.Structure):
        _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ProcessID', wt.DWORD),
                    ('th32DefaultHeapID', ctypes.c_void_p), ('th32ModuleID', wt.DWORD),
                    ('cntThreads', wt.DWORD), ('th32ParentProcessID', wt.DWORD),
                    ('pcPriClassBase', ctypes.c_long), ('dwFlags', wt.DWORD),
                    ('szExeFile', ctypes.c_char * 260)]

    pe = PE32()
    pe.dwSize = ctypes.sizeof(pe)
    try:
        if not _k32.Process32First(h, ctypes.byref(pe)):
            return None
        while True:
            if pe.szExeFile.decode(errors='ignore').lower() == exe.lower():
                return pe.th32ProcessID
            if not _k32.Process32Next(h, ctypes.byref(pe)):
                return None
    finally:
        _k32.CloseHandle(h)


class GameMemoryReader:
    """对运行中的游戏做只读扫描。用 close() 或 with 释放句柄。"""

    def __init__(self, pid: int):
        self.pid = pid
        self.handle = None
        last_err = 0
        # 优先用完整查询权限; 某些运行环境只允许 QUERY_LIMITED_INFORMATION,
        # 使用受限权限也能满足 VirtualQueryEx + ReadProcessMemory 的只读需求。
        for access in (PROCESS_VM_READ | PROCESS_QUERY_INFORMATION,
                       PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION):
            self.handle = _k32.OpenProcess(access, False, pid)
            if self.handle:
                break
            last_err = ctypes.get_last_error()
        if not self.handle:
            raise OSError(
                f'OpenProcess 失败 err={last_err}；若游戏以管理员身份运行，'
                f'请也以管理员身份运行本工具，或关闭会拦截进程访问的安全软件。')
        self._hit_regions: list[tuple[int, int]] = []   # 缓存命中区域 (base, size)

    def close(self):
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---- 内存枚举/读取 ----

    def _regions(self):
        addr = 0
        mbi = _MBI()
        out = []
        while addr < 0x7FFFFFFFFFFF:
            r = _k32.VirtualQueryEx(self.handle, ctypes.c_void_p(addr),
                                    ctypes.byref(mbi), ctypes.sizeof(mbi))
            if not r:
                break
            base = ctypes.cast(mbi.BaseAddress, ctypes.c_void_p).value or 0
            if (mbi.State == MEM_COMMIT and mbi.Type == MEM_PRIVATE
                    and mbi.Protect in (PAGE_READWRITE, PAGE_EXECUTE_READWRITE,
                                        PAGE_WRITECOPY, PAGE_EXECUTE_WRITECOPY)
                    and 0 < mbi.RegionSize <= 64 * 1024 * 1024):
                out.append((base, mbi.RegionSize))
            addr = base + mbi.RegionSize
        return out

    def _read(self, addr: int, size: int) -> bytes | None:
        buf = (ctypes.c_char * size)()
        n = ctypes.c_size_t()
        if not _k32.ReadProcessMemory(self.handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)):
            return None
        return buf.raw[:n.value]

    # ---- 扫描 ----

    @staticmethod
    def _find_hits(mem: bytes, out: set[str]):
        """在区域缓冲里找车库记录签名: find 粗筛 + 正则精验 (与 finditer 语义等价)。"""
        pos = mem.find(b"Livery_")
        while pos >= 0:
            m = APPLIED_RE.match(mem, pos)
            if m:
                out.add(m.group(1).decode())
            pos = mem.find(b"Livery_", pos + 1)

    def _scan_region(self, base: int, size: int, out: set[str]):
        mem = self._read(base, size)
        if not mem:
            return False
        self._find_hits(mem, out)
        return True

    def _scan_one(self, region: tuple[int, int]):
        """单个区域任务: 读内存 + 找命中, 返回 (base, size, 命中集合)。"""
        base, size = region
        hits: set[str] = set()
        self._scan_region(base, size, hits)
        return base, size, hits

    def scan_applied_liveries(self) -> set[str]:
        """全量扫描堆区 (8 线程并行读区域), 返回车上涂装名集合 (并缓存命中区域供快速重扫)。"""
        out: set[str] = set()
        self._hit_regions = []
        # ReadProcessMemory 释放 GIL, 同一句柄多线程并发只读安全; map 保序合并
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for base, size, hits in ex.map(self._scan_one, self._regions()):
                if hits:
                    out |= hits
                    self._hit_regions.append((base, size))
        return out

    def rescan_applied_liveries(self) -> set[str]:
        """快速重扫: 只读缓存的命中区域。无缓存或结果异常少时回退全量扫描。"""
        if not self._hit_regions:
            return self.scan_applied_liveries()
        out: set[str] = set()
        for base, size in self._hit_regions:
            self._scan_region(base, size, out)
        if not out:   # 区域已失效 (如重开游戏) -> 全量
            return self.scan_applied_liveries()
        return out

    # ---- 车辆字符串表 ----

    def read_car_strings(self) -> tuple[list[str], list[str]] | None:
        """读取游戏内存中已加载的 Data_Car VALUES 串表。

        返回 (DisplayName[660], ModelShort[660]); 串表顺序即 Data_Car.str
        的键序 (IDS_DisplayName_* 按 ID 字符串字典序)。找不到返回 None。
        """
        for base, size in self._regions():
            mem = self._read(base, size)
            if not mem:
                continue
            pos = mem.find(CAR_STRING_SIG)
            if pos < 0:
                continue
            strings: list[str] = []
            i = pos
            while len(strings) < CAR_STRING_COUNT and i < len(mem):
                j = mem.find(b"\x00", i)
                if j < 0:
                    break
                strings.append(mem[i:j].decode("utf-8", errors="replace"))
                i = j + 1
            if len(strings) == CAR_STRING_COUNT:
                return strings[:660], strings[660:]
        return None


def read_applied_liveries(pid: int | None = None) -> set[str] | None:
    """一次性: 找游戏并全量扫描, 返回车上涂装名集合。游戏不在运行返回 None。"""
    pid = pid or find_game_pid()
    if not pid:
        return None
    with GameMemoryReader(pid) as r:
        return r.scan_applied_liveries()


def read_car_strings(pid: int | None = None) -> tuple[list[str], list[str]] | None:
    """一次性: 读取游戏内存中 Data_Car 的 DisplayName/ModelShort 串表。"""
    pid = pid or find_game_pid()
    if not pid:
        return None
    with GameMemoryReader(pid) as r:
        return r.read_car_strings()


if __name__ == '__main__':
    import time
    pid = find_game_pid()
    print('游戏 PID:', pid)
    if pid:
        t0 = time.time()
        names = read_applied_liveries(pid)
        assert names is not None          # 已拿到 pid, 不会走 None 分支
        print(f'车上涂装 {len(names)} 条 ({time.time()-t0:.1f}s)')
        for n in sorted(names)[:20]:
            print(' ', n)
