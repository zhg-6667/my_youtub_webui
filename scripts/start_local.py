"""Start the local API and web UI together, without extra launcher dependencies."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
API_URL = "http://127.0.0.1:8000"
WEB_URL = "http://127.0.0.1:3000"
HTTP = build_opener(ProxyHandler({}))


def api_ready(url: str) -> bool:
    try:
        with HTTP.open(f"{url}/api/health", timeout=1) as response:
            payload = json.load(response)
            return isinstance(payload, dict) and payload.get("status") == "ok"
    except (OSError, URLError, ValueError):
        return False


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def stop_children(children: list[subprocess.Popen]) -> None:
    # Each child owns a process group so Next.js workers also stop on Ctrl+C.
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    for child in children:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description="同时启动 YouDub 前后端")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    paths = [
        f"{prefix}/{suffix}"
        for prefix in ("/opt/homebrew", "/usr/local")
        for suffix in ("opt/node@24/bin", "opt/ffmpeg-full/bin", "bin", "sbin")
        if Path(f"{prefix}/{suffix}").is_dir()
    ]
    os.environ["PATH"] = os.pathsep.join([*paths, os.environ.get("PATH", "")])
    os.chdir(ROOT)

    if api_ready(API_URL) and api_ready(WEB_URL):
        print(f"YouDub 已在运行：{WEB_URL}，无需重复启动。", flush=True)
        if not args.no_browser:
            webbrowser.open(WEB_URL)
        return 0

    occupied = [str(port) for port in (8000, 3000) if port_in_use(port)]
    if occupied:
        print(f"端口 {', '.join(occupied)} 已占用。请先关闭原服务，再重新启动。")
        return 1
    if not (ROOT / ".env").is_file():
        print("缺少 .env，请先按 README 完成环境配置。")
        return 1
    if not (WEB / "node_modules" / "next" / "dist" / "bin" / "next").is_file():
        print("缺少前端依赖，请先执行：npm --prefix apps/web ci")
        return 1
    missing = [name for name in ("node", "ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing:
        print(f"找不到运行工具：{', '.join(missing)}，请先按 README 安装。")
        return 1

    children: list[subprocess.Popen] = []

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)

    try:
        print("正在启动 YouDub；请保留本终端窗口，按 Ctrl+C 可关闭前后端。", flush=True)
        children.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.app.main:app",
             "--host", "127.0.0.1", "--port", "8000"],
            cwd=ROOT, start_new_session=True,
        ))
        children.append(subprocess.Popen(
            [shutil.which("node"), str(WEB / "node_modules/next/dist/bin/next"),
             "dev", "--hostname", "127.0.0.1", "--port", "3000"],
            cwd=WEB, start_new_session=True,
            env={**os.environ, "NEXT_SERVER_API_BASE_URL": API_URL},
        ))
        deadline = time.monotonic() + 120
        while not (api_ready(API_URL) and api_ready(WEB_URL)):
            if any(child.poll() is not None for child in children):
                raise RuntimeError("服务启动失败，请查看上方日志。")
            if time.monotonic() >= deadline:
                raise RuntimeError("等待服务启动超时，请查看上方日志。")
            time.sleep(0.5)
        print(f"\nYouDub 已就绪：{WEB_URL}\n按 Ctrl+C 可关闭前后端。\n", flush=True)
        if not args.no_browser:
            webbrowser.open(WEB_URL)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise RuntimeError("一个服务已退出，正在关闭另一个服务。")
    except KeyboardInterrupt:
        print("\n正在关闭 YouDub……", flush=True)
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        stop_children(children)


if __name__ == "__main__":
    sys.exit(main())
