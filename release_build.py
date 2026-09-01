# -*- coding: utf-8 -*-
"""release_build.py — FH6LiveryViewer 自动编译 + 发布脚本(仓库工具, 随版本管理)。

用法(在仓库根目录运行; 任意可用 Python, 仅用标准库):
    python release_build.py                 # 本地产物: 校验 → 打包 → 五语言 exe/zip
    python release_build.py --publish       # 上述 + git tag + push origin + gh release create
    python release_build.py --publish --gitee   # 发布后顺带推送 Gitee 镜像(remote 需已配置)

参数:
    --publish       执行发布: git tag vX.Y.Z → push origin tag → gh release create
                    (说明优先读 dist/release_notes_v{version}.md, 无则 --generate-notes)
    --gitee         发布后推送 gitee 镜像(main+tags); Gitee 的 release 需在网页端手动创建并
                    上传同样的五个 zip(附件单文件上限 100MB, 见 AGENTS.md「Gitee 镜像」)
    --skip-checks   跳过 check_i18n / pyright / git 干净度门禁(仅应急, 发布不应使用)
    --allow-dirty   允许工作区有未提交改动(只放宽 git 干净度; 发布建议每次从提交点出包)

产物约定(照抄 v1.6.0 已验证格式):
    dist/FH6LiveryViewer.exe                       基础 exe
    dist/FH6LiveryViewer_{zh-CN|zh-TW|en|ja|ko}.exe 五语言变体(应用内按自身文件名选语言)
    dist/FH6LiveryViewer_v{version}_win64_{lang}.zip 每语言一个 zip(内含同名单 exe)

版本唯一来源: app.py 的 APP_VERSION(--version 传参时校验一致性)。
"""
import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
LANGS = ("zh-CN", "zh-TW", "en", "ja", "ko")
PYINSTALLER = ROOT / ".venv-build" / "Scripts" / "python.exe"
GH = r"C:\Program Files\GitHub CLI\gh.exe"
PYRIGHT_FILES = ("app.py", "fh6save.py", "gamemem.py", "i18n.py", "check_i18n.py",
                 "lang_en.py", "lang_ja.py", "lang_ko.py", "lang_zhtw.py")


def _run(cmd: list, *, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], cwd=ROOT, check=check,
                          capture_output=True, text=True, errors="replace")


def _out(r: subprocess.CompletedProcess) -> str:
    return (r.stdout or "") + (r.stderr or "")


def read_version() -> str:
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("APP_VERSION"):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("app.py 中找不到 APP_VERSION")


def check_gates(skip: bool, allow_dirty: bool, force_pyright: bool) -> None:
    if skip:
        print("[门禁] 已跳过全部检查(-skip-checks)")
        return
    # 1) i18n 覆盖(发布前必过)
    r = _run([sys.executable, "check_i18n.py"], check=False)
    if r.returncode != 0 or "COVERAGE OK" not in _out(r):
        raise SystemExit("check_i18n 未通过(需 COVERAGE OK)")
    print("[门禁] check_i18n COVERAGE OK")
    # 2) Pylance(有 npx 则跑; 无则提示人工在 VS Code 面板确认)
    if shutil.which("npx") or force_pyright:
        if not shutil.which("npx"):
            raise SystemExit("--pyright 指定但找不到 npx")
        files = [f for f in PYRIGHT_FILES if (ROOT / f).is_file()]
        r = _run(["cmd", "/c", "npx", "--yes", "pyright", *files], check=False)
        if "0 errors, 0 warnings, 0 informations" not in _out(r):
            raise SystemExit("Pylance/pyright 未清零, 发布前必须消除全部告警")
        print("[门禁] Pylance 0 errors / 0 warnings / 0 informations")
    else:
        print("[门禁] 未找到 npx, 跳过 pyright(请在 VS Code Pylance Problems 面板确认清零)")
    # 3) git 干净度(仅追踪文件; 未跟踪目录 .vscode/.zcode 之类不属于发布内容)
    if allow_dirty:
        print("[门禁] 工作区允许未提交改动(-allow-dirty)")
        return
    r = _run(["git", "status", "--porcelain"], check=False)
    dirty = [ln for ln in (r.stdout or "").splitlines()
             if ln.strip() and not ln.startswith("?? ")]
    if dirty:
        raise SystemExit("工作区有未提交改动(追踪文件): " + "; ".join(dirty)
                         + " —— 先提交再发布(或 --allow-dirty 应急)")
    print("[门禁] 工作区干净(追踪文件无改动)")


def build() -> None:
    if not PYINSTALLER.is_file():
        raise SystemExit(
            f"找不到打包环境 {PYINSTALLER}(先建 .venv-build, 见 AGENTS.md 构建与发布)")
    r = _run([PYINSTALLER, "-m", "PyInstaller", str(ROOT / "FH6LiveryViewer.spec"),
              "--noconfirm", "--distpath", str(DIST), "--workpath", str(ROOT / "build")],
             check=False)
    if r.returncode != 0:
        raise SystemExit("PyInstaller 构建失败:\n" + _out(r))
    exe = DIST / "FH6LiveryViewer.exe"
    if not exe.is_file():
        raise SystemExit("构建完成但未找到 dist/FH6LiveryViewer.exe")
    # 内嵌数据校验: 直接以 stdin 喂 'l' 给 archive_viewer(不经 cmd, 避免引号解析问题)
    r = subprocess.run(
        [PYINSTALLER, "-m", "PyInstaller.utils.cliutils.archive_viewer", str(exe)],
        input="l\n", cwd=ROOT, capture_output=True, text=True, errors="replace")
    if "cars.json" not in _out(r):
        raise SystemExit("内嵌数据校验失败: 未在 EXE 中找到 cars.json")
    print(f"[构建] {exe.name} {exe.stat().st_size:,}B, cars.json 内嵌校验通过")


def make_variants(version: str) -> list[Path]:
    base = DIST / "FH6LiveryViewer.exe"
    zips: list[Path] = []
    for lang in LANGS:
        v = DIST / f"FH6LiveryViewer_{lang}.exe"
        shutil.copyfile(base, v)
        z = DIST / f"FH6LiveryViewer_v{version}_win64_{lang}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(v, f"FH6LiveryViewer_{lang}.exe")
        zips.append(z)
        print(f"[变体] {z.name} {z.stat().st_size:,}B")
    return zips


def publish(version: str, zips: list[Path], gitee: bool) -> None:
    if not Path(GH).is_file():
        raise SystemExit(f"未找到 gh CLI: {GH}(AGENTS.md: 用完整路径)")
    _run(["git", "tag", "-a", f"v{version}", "-m", f"FH6 Livery Viewer v{version}"])
    _run(["git", "push", "origin", f"v{version}"])
    notes = DIST / f"release_notes_v{version}.md"
    cmd = [GH, "release", "create", f"v{version}",
           "--repo", "Hx-zh/fh6-livery-viewer",
           "--title", f"FH6 Livery Viewer v{version}"]
    if notes.is_file():
        cmd += ["--notes-file", str(notes)]
    else:
        cmd += ["--generate-notes"]
    cmd += [str(z) for z in zips]
    r = _run(cmd, check=False)
    if r.returncode != 0:
        raise SystemExit("gh release create 失败:\n" + _out(r))
    print(f"[发布] v{version} 已创建; 资产 {len(zips)} 个 zip")
    if gitee:
        r = _run(["git", "config", "--get", "remote.gitee.url"], check=False)
        if r.returncode == 0 and r.stdout.strip():
            _run(["git", "push", "gitee", "main", "--tags"])
            print("[Gitee] 镜像已推送; 请在 Gitee 网页端创建同名 release 并上传同五个 zip")
        else:
            print("[Gitee] 未配置 gitee remote, 跳过推送(AGENTS.md: git remote add gitee https://gitee.com/hx_zh/fh6-livery-viewer.git)")


def main() -> int:
    ap = argparse.ArgumentParser(description="FH6LiveryViewer 自动编译/发布")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--gitee", action="store_true")
    ap.add_argument("--skip-checks", action="store_true")
    ap.add_argument("--pyright", action="store_true", help="强制运行 pyright 门禁")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--version", default=None, help="目标版本(默认读 app.py APP_VERSION)")
    args = ap.parse_args()

    version = read_version()
    if args.version and args.version != version:
        raise SystemExit(f"版本不一致: app.py={version} vs --version={args.version}")

    check_gates(args.skip_checks, args.allow_dirty, args.pyright)
    build()
    zips = make_variants(version)
    print(f"\n本地产物就绪(dist/, v{version}): 基础 exe + {len(LANGS)} 语言变体 + {len(zips)} zip")
    if args.publish:
        publish(version, zips, args.gitee)
    else:
        print("未加 --publish: 只产包不发布; 手动测试通过后再运行 --publish。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
