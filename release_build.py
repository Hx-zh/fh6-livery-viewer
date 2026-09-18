# -*- coding: utf-8 -*-
"""release_build.py — FH6LiveryViewer 自动编译 + 发布脚本(仓库工具, 随版本管理)。

用法(在仓库根目录运行; 任意可用 Python, 仅用标准库):
    python release_build.py                 # 交互式向导(无参数默认进入; 见下)
    python release_build.py --publish       # 非交互: 校验 → 打包 → 五语言 exe/zip
                                            #   + git tag + push origin + gh release create
    python release_build.py --publish --gitee   # 发布后顺带推送 Gitee 镜像
    python release_build.py --gitee-release     # 仅在 Gitee 建 release + 传 5 zip(需 GITEE_TOKEN)
    python release_build.py --data-only         # 仅发布 cars.json 数据(不发版, 见下)

发布管线(v1.8.0 起, 推荐):
    ① git commit → post-commit 钩子自动本地编译测试版(tools/dev_build.py)
    ② 人工测试 dist/FH6LiveryViewer_zh-CN.exe
    ③ git tag -a v1.8.0 -m "发布说明" 后:
         python release_build.py --gitee-local   # 本地: 门禁+构建+简中zip+推gitee镜像+建gitee release
         git push origin main --tags             # 触发 GitHub Action: 门禁+构建+五语言+GitHub release
       (--publish/--gitee-release 老路径保留作应急)

交互式向导(无参数或 -i): 依次询问
  1) 发布版本号(默认读 app.py APP_VERSION; 不一致时可选择自动改写 app.py,
     改写后需先提交再重新运行——发布要求 git 工作区干净);
  2) 发布目标 github / gitee / all / local(local = 只出包不发布);
  3) 自动检测 dist/release_notes_v{version}.md, 缺失时需确认才继续;
  4) 目标含 Gitee release 时主动询问 Gitee 令牌(getpass 不回显,
     环境变量 GITEE_TOKEN 已设置则跳过), 并用 API 轻量校验。

参数:
    --publish       执行发布: git tag vX.Y.Z → push origin tag → gh release create
                    (说明优先读 dist/release_notes_v{version}.md, 无则 --generate-notes)
    --gitee         发布后推送 gitee 镜像(main+tags); 有令牌时走一次性凭据 URL(不落盘),
                    无令牌时依赖本机 git 凭据; 推送失败仅告警不阻断
    --gitee-release 经 Gitee API v5 创建同名 release 并上传 5 zip 附件;
                    令牌见上方交互说明; tag 不存在时 Gitee 会在 main 上自动创建
    --data-only     仅发布车型表数据(v1.8.0 起): 校验 git 干净 → push origin main
                    → 推 Gitee 镜像 → 双端 raw URL 与本地 cars.json 字节比对。
                    不打 tag、不建 release、不出 exe——客户端在线更新
                    (carupdate.py) 直接读仓库 raw 文件, 官方加新车后走本通道
    --ci            GitHub Actions 专用(.github/workflows/release.yml, tag 触发):
                    门禁 → 构建 → 五语言 zip → 在已有 tag 上创建 GitHub release
                    (GH_TOKEN 环境变量鉴权; 发布说明取 tag 注释, 无则自动生成)
    --gitee-local   本地发布 Gitee 侧(推荐流程③): 门禁 → 构建 → 仅简中 zip →
                    推 gitee 镜像(main+tags, tag 先打) → Gitee release 只挂简中 zip
                    ——Gitee 用户拿到的即本地亲手测试过的二进制
    --skip-checks   跳过 check_i18n / pyright / git 干净度门禁(仅应急, 发布不应使用)
    --allow-dirty   允许工作区有未提交改动(只放宽 git 干净度; 发布建议每次从提交点出包)

产物约定(照抄 v1.6.0 已验证格式):
    dist/FH6LiveryViewer.exe                       基础 exe
    dist/FH6LiveryViewer_{zh-CN|zh-TW|en|ja|ko}.exe 五语言变体(应用内按自身文件名选语言)
    dist/FH6LiveryViewer_v{version}_win64_{lang}.zip 每语言一个 zip(内含同名单 exe)

版本唯一来源: app.py 的 APP_VERSION(--version 传参时校验一致性)。
"""
import argparse
import getpass
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import carupdate   # 数据源 URL 常量单一来源(--data-only 校验用)

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
LANGS = ("zh-CN", "zh-TW", "en", "ja", "ko")
PYINSTALLER = ROOT / ".venv-build" / "Scripts" / "python.exe"
GH = r"C:\Program Files\GitHub CLI\gh.exe"
GITEE_REPO = "hx_zh/fh6-livery-viewer"
GITEE_API = f"https://gitee.com/api/v5/repos/{GITEE_REPO}"
PYRIGHT_FILES = ("app.py", "carupdate.py", "fh6save.py", "gamemem.py",
                 "i18n/__init__.py", "check_i18n.py",
                 "i18n/lang_en.py", "i18n/lang_ja.py", "i18n/lang_ko.py", "i18n/lang_zhtw.py")


def pyinstaller_python() -> str:
    """打包用 Python 解释器: CI 用 PYINSTALLER_PY 环境变量指定(workflow 的
    setup-python), 本地用 .venv-build(勿用损坏的 pyinstaller.exe shim)。"""
    ovr = os.environ.get("PYINSTALLER_PY", "").strip()
    if ovr:
        return ovr
    if PYINSTALLER.is_file():
        return str(PYINSTALLER)
    raise SystemExit(f"找不到打包环境 {PYINSTALLER}(本地先建 .venv-build 见 AGENTS.md; "
                     "CI 设 PYINSTALLER_PY)")


def gh_exe() -> str:
    """gh CLI: 优先 PATH(CI 运行器自带), 回退本机完整路径。"""
    return shutil.which("gh") or GH


def _run(cmd: list, *, check: bool = True, redact: str = "") -> subprocess.CompletedProcess:
    shown = [str(c).replace(redact, "***") if redact else str(c) for c in cmd]
    print("+", " ".join(shown))
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


# ------------------------------------------------------------ 交互式向导

def bump_app_version(new: str) -> None:
    """把 app.py 的 APP_VERSION 改写为 new(版本号唯一来源)。"""
    p = ROOT / "app.py"
    text = p.read_text(encoding="utf-8")
    new_text, n = re.subn(r'(?m)^APP_VERSION\s*=\s*"[^"]*"',
                          f'APP_VERSION = "{new}"', text, count=1)
    if n != 1:
        raise SystemExit("app.py 中未找到 APP_VERSION 赋值行, 无法自动改写")
    p.write_text(new_text, encoding="utf-8")
    print(f"[版本] app.py APP_VERSION 已改为 {new}")


def ask_version(current: str) -> str:
    print(f"\n当前 app.py APP_VERSION = {current}")
    v = input(f"发布版本号 [回车 = {current}]: ").strip().lstrip("v")
    if not v or v == current:
        return current
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        raise SystemExit(f"版本号格式应为 X.Y.Z: {v!r}")
    if input(f"与 app.py 不一致({current}): 先把 app.py APP_VERSION 改为 {v}? [y/N] "
             ).strip().lower() == "y":
        bump_app_version(v)
        print("请提交该改动后重新运行发布(发布要求 git 工作区干净)")
        raise SystemExit(0)
    raise SystemExit("已取消")


def ask_target() -> str:
    print("\n发布目标: 1) github  2) gitee  3) all(双端)  4) local(只出包不发布)")
    ans = input("选择 [1/2/3/4 或名称, 回车 = all]: ").strip().lower()
    table = {"": "all", "1": "github", "github": "github",
             "2": "gitee", "gitee": "gitee",
             "3": "all", "all": "all",
             "4": "local", "local": "local"}
    if ans not in table:
        raise SystemExit(f"无法识别的目标: {ans!r}")
    return table[ans]


def check_notes(version: str, interactive: bool) -> None:
    """自动检测 dist/release_notes_v{version}.md(双端共用); 交互模式下缺失需确认才继续。"""
    notes = DIST / f"release_notes_v{version}.md"
    if notes.is_file():
        print(f"[说明] 发布说明: {notes.relative_to(ROOT)}")
        return
    print(f"[说明] 警告: 未找到 {notes.relative_to(ROOT)}")
    print("       GitHub 将回退 --generate-notes(自动提交列表), Gitee 用默认一句话说明")
    if interactive:
        if input("仍要继续? [y/N] ").strip().lower() != "y":
            raise SystemExit("已取消(先写发布说明再来)")


def _validate_gitee_token(token: str) -> bool:
    q = urllib.parse.urlencode({"access_token": token})
    try:
        with urllib.request.urlopen(f"{GITEE_API}?{q}", timeout=30):
            return True
    except Exception:
        return False


def get_gitee_token(interactive: bool) -> str:
    """Gitee 令牌: 环境变量 GITEE_TOKEN 优先; 交互终端缺失时主动询问(getpass 不回显)
    并用 API 轻量校验(最多 3 次); 非交互终端缺失直接报错。"""
    token = os.environ.get("GITEE_TOKEN", "").strip()
    if token:
        print("[Gitee] 令牌: 来自 GITEE_TOKEN 环境变量")
        return token
    if not (interactive and sys.stdin.isatty()):
        raise SystemExit("缺少 GITEE_TOKEN 环境变量(Gitee 设置 → 私人令牌, 勾选 projects 权限)")
    for attempt in range(1, 4):
        token = getpass.getpass(
            "请输入 Gitee 私人令牌(输入不回显; 也可设 GITEE_TOKEN 环境变量): ").strip()
        if not token:
            raise SystemExit("已取消(未输入令牌)")
        if _validate_gitee_token(token):
            print("[Gitee] 令牌校验通过")
            return token
        print(f"[Gitee] 令牌无效或权限不足({attempt}/3)")
    raise SystemExit("Gitee 令牌三次校验失败, 发布取消")


def push_gitee_mirror(token: str = "") -> bool:
    """推送 gitee 镜像(main + tags)。有令牌走一次性凭据 URL(令牌不落盘、输出脱敏);
    无令牌尝试已配置的 gitee remote(依赖本机 git 凭据)。失败仅告警不阻断。"""
    r = _run(["git", "config", "--get", "remote.gitee.url"], check=False)
    has_remote = r.returncode == 0 and bool(r.stdout.strip())
    if token:
        user = GITEE_REPO.split("/", 1)[0]
        url = f"https://{user}:{token}@gitee.com/{GITEE_REPO}.git"
        r2 = _run(["git", "push", url, "main", "--tags"], check=False, redact=token)
    elif has_remote:
        r2 = _run(["git", "push", "gitee", "main", "--tags"], check=False)
    else:
        print("[Gitee] 未配置 gitee remote, 跳过镜像推送")
        return False
    if r2.returncode == 0:
        print("[Gitee] 镜像已推送(main + tags)")
        return True
    print("[Gitee] 镜像推送失败(不阻断发布): "
          + _out(r2).replace(token or "\x00", "***")[:300])
    return False


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
    py = pyinstaller_python()
    r = _run([py, "-m", "PyInstaller", str(ROOT / "FH6LiveryViewer.spec"),
              "--noconfirm", "--distpath", str(DIST), "--workpath", str(ROOT / "build")],
             check=False)
    if r.returncode != 0:
        raise SystemExit("PyInstaller 构建失败:\n" + _out(r))
    exe = DIST / "FH6LiveryViewer.exe"
    if not exe.is_file():
        raise SystemExit("构建完成但未找到 dist/FH6LiveryViewer.exe")
    # 内嵌数据校验: 直接以 stdin 喂 'l' 给 archive_viewer(不经 cmd, 避免引号解析问题)
    r = subprocess.run(
        [py, "-m", "PyInstaller.utils.cliutils.archive_viewer", str(exe)],
        input="l\n", cwd=ROOT, capture_output=True, text=True, errors="replace")
    if "cars.json" not in _out(r):
        raise SystemExit("内嵌数据校验失败: 未在 EXE 中找到 cars.json")
    print(f"[构建] {exe.name} {exe.stat().st_size:,}B, cars.json 内嵌校验通过")


def make_variants(version: str, langs: tuple = LANGS) -> list[Path]:
    """按语言列表产出变体 exe + 同名 zip(Gitee 本地发布只取简中子集)。"""
    base = DIST / "FH6LiveryViewer.exe"
    zips: list[Path] = []
    for lang in langs:
        v = DIST / f"FH6LiveryViewer_{lang}.exe"
        shutil.copyfile(base, v)
        z = DIST / f"FH6LiveryViewer_v{version}_win64_{lang}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(v, f"FH6LiveryViewer_{lang}.exe")
        zips.append(z)
        print(f"[变体] {z.name} {z.stat().st_size:,}B")
    return zips


def tag_notes(version: str) -> str:
    """发布说明来源: dist/release_notes_vX.md → tag 注释(git tag -a 时写的)
    → 空串(GitHub 回退 --generate-notes / Gitee 用默认一句话)。"""
    notes = DIST / f"release_notes_v{version}.md"
    if notes.is_file():
        return notes.read_text(encoding="utf-8")
    r = _run(["git", "tag", "-l", "--format=%(contents)", f"v{version}"], check=False)
    return (r.stdout or "").strip()


def publish_github_release(version: str, zips: list[Path]) -> None:
    """CI 路径: 在已推送的 tag 上创建 GitHub release(gh 用 GH_TOKEN 环境变量鉴权,
    无需登录; 发布说明取 tag 注释)。"""
    gh = gh_exe()
    if not gh:
        raise SystemExit(f"未找到 gh CLI(本机路径 {GH})")
    cmd = [gh, "release", "create", f"v{version}",
           "--repo", "Hx-zh/fh6-livery-viewer",
           "--title", f"FH6 Livery Viewer v{version}"]
    notes = tag_notes(version)
    cmd += ["--notes", notes] if notes else ["--generate-notes"]
    cmd += [str(z) for z in zips]
    r = _run(cmd, check=False)
    if r.returncode != 0:
        raise SystemExit("gh release create 失败:\n" + _out(r))
    print(f"[发布] GitHub release v{version} 已创建; 资产 {len(zips)} 个 zip")


def publish(version: str, zips: list[Path], gitee: bool, token: str = "") -> None:
    gh = gh_exe()
    if not gh:
        raise SystemExit(f"未找到 gh CLI(本机路径 {GH}; AGENTS.md: 用完整路径)")
    _run(["git", "tag", "-a", f"v{version}", "-m", f"FH6 Livery Viewer v{version}"])
    _run(["git", "push", "origin", f"v{version}"])
    notes = DIST / f"release_notes_v{version}.md"
    cmd = [gh, "release", "create", f"v{version}",
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
        if not push_gitee_mirror(token):
            print("[Gitee] 注意: 镜像未同步, 随后 Gitee release 的 tag 可能落在旧 main 上")


def gitee_upload(version: str, zips: list[Path], token: str) -> None:
    """Gitee release: 用 API v5 创建 release 并上传 5 个 zip 附件。

    令牌由调用方备好(get_gitee_token: 环境变量优先, 交互终端主动询问)。
    tag 若仓库中不存在, Gitee 会在指定 target_commitish(=main) 上自动创建。
    Gitee 附件单文件上限 100MB(我们 zip ~19MB, 无碍)。"""
    if not token:
        raise SystemExit("缺少 Gitee 令牌(传参或 GITEE_TOKEN 环境变量)")
    q = urllib.parse.urlencode({"access_token": token})
    body = tag_notes(version) or f"FH6 Livery Viewer v{version}"

    def _api(method: str, path: str, data=None, headers=None) -> dict:
        req = urllib.request.Request(f"{GITEE_API}/{path}?{q}", data=data, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise SystemExit(f"Gitee API {method} {path} 失败 {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")

    release = _api("POST", "releases", urllib.parse.urlencode({
        "tag_name": f"v{version}",
        "target_commitish": "main",
        "name": f"FH6 Livery Viewer v{version}",
        "body": body,
    }).encode(), {"Content-Type": "application/x-www-form-urlencoded"})
    rid = release.get("id")
    if not rid:
        raise SystemExit("Gitee release 创建成功但未返回 id: " + str(release)[:200])

    def _attach(path: Path) -> None:
        boundary = "----DSH" + str(hash(path))[:12]
        parts = []
        for name, value in (("access_token", token),):
            parts.append(
                (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                 + value + "\r\n").encode())
        fmime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        parts.append(
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{path.name}\"\r\nContent-Type: {fmime}\r\n\r\n").encode() + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        payload = b"".join(parts)
        req = urllib.request.Request(
            f"{GITEE_API}/releases/{rid}/attach_files",
            data=payload, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                j = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise SystemExit(f"Gitee 附件上传 {path.name} 失败 {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        print(f"[Gitee] 附件已上传: {path.name} -> {j.get('download_url') or j.get('name')}")

    for z in zips:
        _attach(z)
    print(f"[Gitee] release v{version} 完成: https://gitee.com/{GITEE_REPO}/releases/v{version}")


def verify_remote_cars(tries: int = 5, wait_s: float = 10.0) -> None:
    """双端 raw 与仓库 cars.json 字节级比对(gitee/github 必须一致, 重试兜传播抖动;
    jsdelivr 对分支引用有缓存滞后, 仅提示不阻断)。
    比对基准用 HEAD blob 而非工作区文件——autocrlf 可能把工作区 CRLF 转成
    入库 LF, raw 端点返回 blob 字节, 与工作区直接比会因换行符误报。"""
    local = subprocess.run(["git", "show", "HEAD:cars.json"],
                           capture_output=True, check=True).stdout
    pending = {"gitee": carupdate.SOURCE_URLS["gitee"],
               "github": carupdate.SOURCE_URLS["github"]}
    for attempt in range(1, tries + 1):
        for name in list(pending):
            try:
                req = urllib.request.Request(
                    pending[name], headers={"User-Agent": "release_build"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.read() == local:
                        print(f"[校验] {name} 线上内容与本地一致 ✓")
                        del pending[name]
            except OSError:
                pass
        if not pending:
            break
        print(f"[校验] 第 {attempt}/{tries} 轮未全一致, 余 {', '.join(pending)}"
              f", {wait_s:.0f}s 后重试")
        time.sleep(wait_s)
    if pending:
        raise SystemExit("线上内容校验未通过: " + ", ".join(pending)
                         + " (检查推送是否成功/网络, 稍后可单独重跑)")
    try:
        req = urllib.request.Request(carupdate.SOURCE_URLS["jsdelivr"],
                                     headers={"User-Agent": "release_build"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            same = resp.read() == local
        print(f"[校验] jsdelivr: "
              + ("一致 ✓" if same else "仍有分支缓存滞后(数小时级, 不阻断, 兜底源可容忍)"))
    except OSError as e:
        print(f"[校验] jsdelivr: 暂不可达({e}), 不阻断")


def data_only_publish() -> None:
    """数据更新发布: cars.json 已提交后把 main 推到双端并校验线上内容一致。
    不打 tag、不建 release、不出 exe——客户端在线更新(carupdate.py)直接读仓库
    raw 文件, 官方加新车后用本命令发布, 用户 24h 内自动拿到新表。"""
    r = _run(["git", "status", "--porcelain"], check=False)
    dirty = [ln for ln in (r.stdout or "").splitlines()
             if ln.strip() and not ln.startswith("?? ")]
    if dirty:
        raise SystemExit("工作区有未提交改动: " + "; ".join(dirty)
                         + " —— 数据更新=普通 commit, 先提交再发布")
    r = _run(["git", "log", "-1", "--oneline", "--", "cars.json"], check=False)
    print(f"[数据] 最新 cars.json 提交: {(r.stdout or '').strip()}")
    _run(["git", "push", "origin", "main"])
    token = os.environ.get("GITEE_TOKEN", "").strip()
    if not push_gitee_mirror(token):
        print("[数据] 警告: Gitee 镜像未同步, 国内用户暂时读不到新数据(不阻断)")
    verify_remote_cars()


def main() -> int:
    ap = argparse.ArgumentParser(description="FH6LiveryViewer 自动编译/发布")
    ap.add_argument("-i", "--interactive", action="store_true",
                    help="交互式向导(不带任何参数运行时默认进入)")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--gitee", action="store_true", help="发布后推送 gitee 镜像(main+tags)")
    ap.add_argument("--gitee-release", action="store_true",
                    help="在 Gitee 创建同名 release 并上传 5 个 zip(令牌见交互说明/GITEE_TOKEN)")
    ap.add_argument("--data-only", action="store_true",
                    help="仅发布 cars.json 数据: push 双端 + 线上内容校验(不发版)")
    ap.add_argument("--ci", action="store_true",
                    help="GitHub Actions 专用: 门禁+构建+五语言+在已有 tag 上建 GitHub release")
    ap.add_argument("--gitee-local", action="store_true",
                    help="本地发布 Gitee 侧: 门禁+构建+仅简中 zip+推镜像+建 Gitee release")
    ap.add_argument("--skip-checks", action="store_true")
    ap.add_argument("--pyright", action="store_true", help="强制运行 pyright 门禁")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--version", default=None, help="目标版本(默认读 app.py APP_VERSION)")
    args = ap.parse_args()

    version = read_version()
    if args.version and args.version != version:
        raise SystemExit(f"版本不一致: app.py={version} vs --version={args.version}")

    if args.data_only:
        data_only_publish()
        return 0

    if args.ci:
        # CI(.github/workflows/release.yml, tag 触发): tag 已推送, 不再建/推;
        # 门禁(check_i18n + pyright; git 干净天然满足) → 构建 → 五语言 → GH release
        check_gates(False, False, force_pyright=True)
        build()
        zips = make_variants(version)
        publish_github_release(version, zips)
        print(f"\n[CI] GitHub release v{version} 完成(Gitee 侧由本地 --gitee-local 负责)")
        return 0

    if args.gitee_local:
        # 推荐 release 流程③的 Gitee 侧: 产物 = 本地亲手测试过的构建
        if not _run(["git", "rev-parse", "-q", "--verify", f"v{version}"],
                    check=False).stdout.strip():
            raise SystemExit(f"tag v{version} 不存在——先 git tag -a v{version} -m '发布说明…'")
        token = get_gitee_token(sys.stdin.isatty())
        check_gates(args.skip_checks, args.allow_dirty, args.pyright)
        build()
        zh = make_variants(version, langs=("zh-CN",))[0]
        if not push_gitee_mirror(token):      # main+tags: tag 随镜像到达 Gitee
            print("[Gitee] 警告: 镜像未同步, release 的 tag 可能落在旧 main 上")
        gitee_upload(version, [zh], token)
        print("\n[下一步] git push origin main --tags 触发 GitHub Action"
              " 构建五语言并发布 GitHub release")
        return 0

    # 无参数(或显式 -i)进入交互式向导: 问版本 → 问目标 → (后文自动检测说明/问令牌)
    interactive = args.interactive or len(sys.argv) == 1
    if interactive:
        version = ask_version(version)
        target = ask_target()
        args.publish = target in ("github", "all")
        args.gitee = target == "all"
        args.gitee_release = target in ("gitee", "all")

    check_notes(version, interactive)

    token = ""
    if args.gitee_release:
        token = get_gitee_token(interactive)

    if interactive:
        steps = []
        if args.publish:
            steps.append("GitHub release")
        if args.gitee:
            steps.append("Gitee 镜像推送")
        if args.gitee_release:
            steps.append("Gitee release + 5 zip")
        plan = " → ".join(steps) if steps else "仅本地产物(不发布)"
        print(f"\n== 发布计划 ==\n版本: v{version}\n流程: 门禁 → 构建 → 五语言 zip → {plan}")
        if input("确认开始? [y/N] ").strip().lower() != "y":
            raise SystemExit("已取消")

    check_gates(args.skip_checks, args.allow_dirty, args.pyright)
    build()
    zips = make_variants(version)
    print(f"\n本地产物就绪(dist/, v{version}): 基础 exe + {len(LANGS)} 语言变体 + {len(zips)} zip")
    if args.publish:
        publish(version, zips, args.gitee, token)
    if args.gitee_release:
        if not args.publish:
            # 仅 Gitee 侧: 先同步镜像, 否则 Gitee 自动建 tag 会落在旧 main 上
            if not push_gitee_mirror(token):
                print("[Gitee] 注意: 镜像未同步, Gitee release 的 tag 可能落在旧 main 上")
        gitee_upload(version, zips, token)
    if not (args.publish or args.gitee_release):
        print("未加 --publish: 只产包不发布; 手动测试通过后再运行 --publish。")
    if args.gitee_release and not args.publish:
        print("(仅做了 Gitee release; GitHub 侧未动)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
