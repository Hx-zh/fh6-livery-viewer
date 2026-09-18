# -*- coding: utf-8 -*-
"""tools/dev_build.py — 提交后自动本地编译测试版(git post-commit 钩子驱动)。

用法:
    python tools/dev_build.py --install-hook   # 安装 .git/hooks/post-commit(一次性)
    python tools/dev_build.py                  # 手动立即构建(同步等待, 调试用)
    python tools/dev_build.py --hook           # 钩子入口: 秒回, 实际构建已后台分离

产物: dist/FH6LiveryViewer_zh-CN.exe(基础 exe 的简中变体拷贝, 供日常测试)。

规则:
    - 单飞锁 build/.dev_build.lock(O_CREAT|O_EXCL): 已有构建在跑则本轮跳过并记
      日志(下一次提交会再触发, 也可手动补跑); 锁超过 30 分钟视为残留可抢占;
    - 只跑 check_i18n 快门禁(秒级); pyright 留给 CI/发布门禁, 保持提交体验轻快;
    - 从当前工作区构建(允许未提交改动——测试版的意义); 日志追加 build/dev_build.log。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import release_build as rb  # noqa: E402  复用 PYINSTALLER 解析/构建参数

LOCK = ROOT / "build" / ".dev_build.lock"
LOG = ROOT / "build" / "dev_build.log"
TARGET = ROOT / "dist" / "FH6LiveryViewer_zh-CN.exe"
STALE_S = 30 * 60

HOOK = """#!/bin/sh
# 提交后自动本地编译测试版(后台分离, 不阻塞 git)——见 tools/dev_build.py
python tools/dev_build.py --hook >/dev/null 2>&1
exit 0
"""


def log(msg: str) -> None:
    try:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def acquire() -> bool:
    """单飞锁; 已被占用(且不陈旧)返回 False。"""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                age = time.time() - LOCK.stat().st_mtime
            except OSError:
                continue                    # 锁刚消失, 重试一次
            if age > STALE_S:
                log(f"抢占残留锁(age {age:.0f}s)")
                try:
                    LOCK.unlink()
                except OSError:
                    pass
                continue
            return False
    return False


def release_lock() -> None:
    try:
        LOCK.unlink()
    except OSError:
        pass


def build_once(commit: str) -> bool:
    t0 = time.time()
    r = subprocess.run([sys.executable, "check_i18n.py"], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0 or "COVERAGE OK" not in (r.stdout or "") + (r.stderr or ""):
        log(f"✗ {commit} check_i18n 未通过, 跳过构建")
        return False
    py = rb.pyinstaller_python()
    b = subprocess.run([py, "-m", "PyInstaller", str(ROOT / "FH6LiveryViewer.spec"),
                        "--noconfirm", "--distpath", str(ROOT / "dist"),
                        "--workpath", str(ROOT / "build")],
                       cwd=ROOT, capture_output=True, text=True, errors="replace")
    exe = ROOT / "dist" / "FH6LiveryViewer.exe"
    if b.returncode != 0 or not exe.is_file():
        tail = ((b.stdout or "") + "\n" + (b.stderr or "")).strip()[-400:]
        hint = ("  (提示: 输出 exe 被占用——多半是上次测试的窗口没关,"
                " 结束 FH6LiveryViewer.exe 进程后重试)"
                if "拒绝访问" in tail or "PermissionError" in tail else "")
        log(f"✗ {commit} PyInstaller 失败: {tail}{hint}")
        return False
    shutil.copyfile(exe, TARGET)
    log(f"✓ {commit} -> {TARGET.name} {TARGET.stat().st_size:,}B ({time.time() - t0:.0f}s)")
    return True


def run_hook() -> None:
    """钩子入口: 拿锁 → 分离后台 worker → 立即返回(不阻塞 git)。"""
    if sys.platform != "win32":
        print("dev_build 钩子仅支持 Windows")
        return
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip() or "?"
    if not acquire():
        log(f"- {commit} 跳过(已有构建进行中)")
        return
    detached = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", commit],
                     cwd=str(ROOT), creationflags=detached, close_fds=True,
                     stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install_hook() -> None:
    p = ROOT / ".git" / "hooks" / "post-commit"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(HOOK, encoding="utf-8", newline="\n")
    p.chmod(0o755)
    print(f"已安装钩子: {p}")
    print("此后每次 git commit 将后台编译 dist/FH6LiveryViewer_zh-CN.exe"
          "(日志: build/dev_build.log)")


def main() -> int:
    if "--install-hook" in sys.argv:
        install_hook()
        return 0
    if "--hook" in sys.argv:
        run_hook()
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == "--worker":
        commit = sys.argv[2]
        try:
            build_once(commit)
        finally:
            release_lock()
        return 0
    # 默认: 手动同步构建(拿不到锁则提示)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip() or "?"
    if not acquire():
        print("已有构建进行中(或锁残留), 本次跳过; 稍后重试或删 build/.dev_build.lock")
        return 1
    try:
        ok = build_once(commit)
    finally:
        release_lock()
    print(("构建完成: " if ok else "构建失败: ") + str(TARGET) + f"  (日志 {LOG})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
