# -*- coding: utf-8 -*-
"""tools/fastdl.py — 多线程分块下载器(纯标准库, 支持本地 Clash 代理)。

用法:
    python tools/fastdl.py <url> [-o 输出文件] [-t 线程数] [--proxy auto|host:port|none]
                           [--header "Name: Value"] ...

特性:
  · Range 分块 + 线程池并行(默认 8 线程), 逐块重试(3 次), 逐块校验 206/长度;
  · --proxy auto 自动探测本地代理: 环境变量 HTTPS_PROXY → Clash 常见端口
    (7890/7891/2080/10808/1080) → netstat 本地监听端口逐一探测 → 直连兜底;
  · 首请求可带 --header(如 GitHub API 的 Authorization); 手动跟随重定向,
    认证头只在首跳携带(签名 CDN URL 自含凭据, 避免 401);
  · 进度单行刷新(百分比/速度/已下载), 结束校验总字节数。

已知坑(GitHub Actions 工件实测): 该 API 端点实际正文可比 Content-Length
多出若干字节(分块按声明长度拼装会缺尾部 → zip 损坏); 本工具遇此情形会
以「大小不符」失败退出(宁失败不出坏文件), 此时用单流 curl 下载:
    curl -sL -H "Authorization: Bearer $(gh auth token)" -o out.zip <url>

GitHub Actions 工件快速下载示例(gh run download 太慢时的替代):
    python tools/fastdl.py "$(gh api repos/OWNER/REPO/actions/artifacts \
        --jq '.artifacts[0].archive_download_url')" \
        --header "Authorization: Bearer $(gh auth token)" -o artifact.zip
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

CLASH_PORTS = (7890, 7891, 2080, 10808, 1080, 8888)
RETRY = 3
PROBE_TIMEOUT = 4


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁用自动跟随: 30x 原样抛 HTTPError(其 headers 带 Location)。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener(proxy: str | None) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
    handlers.append(urllib.request.ProxyHandler(
        {} if proxy is None else {"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def _proxy_candidates() -> list[str | None]:
    out: list[str | None] = []
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        v = os.environ.get(var, "").strip()
        if v and v not in out:
            out.append(v)
    out.append(None)          # 直连始终垫底
    return out


def _port_alive(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def _local_listeners(limit: int = 48) -> list[str]:
    """netstat 扫 127.0.0.1 监听端口, 返回可当 HTTP 代理探测的候选(异常静默)。"""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, timeout=8).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    ports: list[int] = []
    for line in out.splitlines():
        m = re.search(r"127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING", line)
        if m:
            p = int(m.group(1))
            if 1024 <= p < 65536 and p not in ports:
                ports.append(p)
    return [f"http://127.0.0.1:{p}" for p in ports[:limit]]


def build_opener(proxy_arg: str) -> tuple[urllib.request.OpenerDirector, str]:
    """按 --proxy 参数构造 opener; 返回 (opener, 描述)。auto 顺序:
    环境变量代理 → Clash 常见端口 → netstat 本地监听逐一探测 → 直连。"""
    if proxy_arg in ("none", "off"):
        return _opener(None), "direct"
    if proxy_arg not in ("auto", ""):
        return _opener(proxy_arg), proxy_arg
    for cand in _proxy_candidates():          # env 代理优先
        if cand is None:
            continue                          # 直连垫底, 后面试
        opener = _opener(cand)
        if _probe(opener):
            return opener, cand
    for p in [f"http://127.0.0.1:{port}" for port in CLASH_PORTS]:
        opener = _opener(p)
        if _probe(opener):
            return opener, p
    for p in _local_listeners():              # 非常规端口的本地代理(实测 7074 等)
        opener = _opener(p)
        if _probe(opener):
            return opener, p
    opener = _opener(None)                    # 全部失败: 直连兜底(探测失败仅告警)
    print("[proxy] 告警: 未探测到可用代理, 回退直连")
    return opener, "direct"


def _probe(opener: urllib.request.OpenerDirector) -> bool:
    """经 opener 对 github.com 发 HEAD 探测(4s 超时)。"""
    req = urllib.request.Request("https://github.com", method="HEAD")
    try:
        with opener.open(req, timeout=PROBE_TIMEOUT) as r:
            return r.status < 500
    except urllib.error.HTTPError:
        return True                          # 有响应就算通(4xx 也说明链路可用)
    except OSError:
        return False


def resolve(url: str, opener, headers: dict[str, str]):
    """手动跟随重定向(认证头只在首跳携带, 防 CDN 401), 拿最终 URL/总长/Range 支持。"""
    cur, cur_headers = url, headers
    for _ in range(6):
        req = urllib.request.Request(cur, headers=cur_headers, method="GET")
        try:
            r = opener.open(req, timeout=30)
        except urllib.error.HTTPError as e:    # 30x(NoRedirect): 从 headers 取 Location
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location")
                e.close()
                if not loc:
                    break
                cur, cur_headers = loc, {}   # 后续跳转不带认证头
                continue
            raise
        with r:
            total = int(r.headers.get("Content-Length") or 0)
            accept = r.headers.get("Accept-Ranges", "")
            return cur, total, "bytes" in accept
    raise SystemExit("重定向次数超限或响应异常")


def fetch_range(url: str, opener, start: int, end: int, fh, lock,
                counters, name: str) -> None:
    """下载一个块并 pwrite 进预分配文件; 逐块重试。"""
    last_err: Exception | None = None
    for _ in range(RETRY):
        try:
            req = urllib.request.Request(
                url, headers={"Range": f"bytes={start}-{end}"})
            with opener.open(req, timeout=60) as r:
                if r.status != 206:
                    raise OSError(f"range 请求返回 {r.status}(期望 206)")
                data = r.read()
            if len(data) != end - start + 1:
                raise OSError(f"short read: {len(data)}")
            with lock:                            # os.pwrite 非 Windows 通用, 锁内 seek+write
                os.lseek(fh, start, os.SEEK_SET)
                os.write(fh, data)
            with counters["lock"]:
                counters["done"] += len(data)
            return
        except OSError as e:                  # 超时/重置/代理抖动
            last_err = e
            time.sleep(0.8)
    raise SystemExit(f"块 {name} 下载失败(重试 {RETRY} 次): {last_err}")


def main() -> int:
    ap = argparse.ArgumentParser(description="多线程分块下载器(Clash 友好)")
    ap.add_argument("url")
    ap.add_argument("-o", "--output", default=None, help="输出文件(默认 URL 末段)")
    ap.add_argument("-t", "--threads", type=int, default=8)
    ap.add_argument("--proxy", default="auto",
                    help="auto|none|http://host:port (默认 auto)")
    ap.add_argument("--header", action="append", default=[],
                    help="首请求附加头, 可多次")
    args = ap.parse_args()

    headers = dict(h.split(": ", 1) for h in args.header if ": " in h)
    opener, via = build_opener(args.proxy)
    print(f"[proxy] {via}")

    final, total, ranged = resolve(args.url, opener, headers)
    out = args.output or final.split("?", 1)[0].rsplit("/", 1)[-1] or "download.bin"
    print(f"[size] {total:,}B | ranges={ranged} | -> {out}")
    if total <= 0:
        raise SystemExit("响应无 Content-Length, 无法分块")

    nthread = max(1, min(args.threads, 32))
    if not ranged:
        nthread = 1
    step = (total + nthread - 1) // nthread
    chunks = [(i, min(i + step, total) - 1) for i in range(0, total, step)]

    t0 = time.time()
    lock, fh = threading.Lock(), os.open(
        out, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
    counters = {"lock": threading.Lock(), "done": 0}
    try:
        with ThreadPoolExecutor(max_workers=nthread) as pool:
            futs = [pool.submit(fetch_range, final, opener, s, e, fh, lock,
                                counters, f"{s//step+1}/{len(chunks)}")
                    for s, e in chunks]
            while any(not f.done() for f in futs):
                time.sleep(0.4)
                with counters["lock"]:
                    done = counters["done"]
                spd = done / max(time.time() - t0, 0.001) / 1048576
                pct = done * 100 // total
                print(f"\r  {pct:3d}%  {done / 1048576:8.1f}/"
                      f"{total / 1048576:.1f}MB  {spd:6.1f}MB/s   ",
                      end="", flush=True)
            for f in futs:                    # 逐个 get, 抛出首个异常
                f.result()
    finally:
        os.close(fh)
    print()
    size = os.path.getsize(out)
    if size != total:
        raise SystemExit(f"大小不符: {size:,} != {total:,}")
    if out.lower().endswith(".zip"):
        # 坏块(长度对内容错)只有整档校验能兜住; zip 自校验失败即删档, 不交付坏文件
        import zipfile
        try:
            bad = zipfile.ZipFile(out).testzip()
        except Exception as e:              # noqa: BLE001
            os.remove(out)
            raise SystemExit(f"zip 校验异常(坏文件已删): {e}") from e
        if bad is not None:
            os.remove(out)
            raise SystemExit(f"zip 内容损坏(已删): {bad}")
    dt = time.time() - t0
    print(f"[done] {out} {size:,}B in {dt:.1f}s ({size/1048576/max(dt,0.01):.1f}MB/s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
