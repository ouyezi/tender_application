#!/usr/bin/env python3
"""一键启动标书诊断 Demo（后端 FastAPI + 前端 Vite）。

默认监听 0.0.0.0，局域网内其他机器可通过本机 IP 访问。
前后端在当前终端内托管，不再额外弹出 Terminal 窗口。
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
VENV_UVICORN = ROOT / ".venv" / "bin" / "uvicorn"
BACKEND_PORT = 8000
FRONTEND_PORT = 5173
BIND_HOST = "0.0.0.0"
HEALTH_URL = f"http://127.0.0.1:{BACKEND_PORT}/api/health"
LOCAL_FRONTEND_URL = f"http://127.0.0.1:{FRONTEND_PORT}"


def die(msg: str, code: int = 1) -> None:
    print(f"[startup] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def lan_ip() -> str:
    """探测用于局域网访问的本机 IP（失败则回退 127.0.0.1）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def print_access_urls() -> None:
    ip = lan_ip()
    print(f"[startup] 本机前端:  {LOCAL_FRONTEND_URL}")
    print(f"[startup] 本机 API:   http://127.0.0.1:{BACKEND_PORT}/docs")
    if ip != "127.0.0.1":
        print(f"[startup] 局域网前端: http://{ip}:{FRONTEND_PORT}")
        print(f"[startup] 局域网 API:  http://{ip}:{BACKEND_PORT}/docs")
        print("[startup] 若外机无法访问，请检查本机防火墙是否放行 5173/8000")


def ensure_venv() -> None:
    if not VENV_PYTHON.exists():
        die(
            f"未找到虚拟环境 {VENV_PYTHON}。请先执行：\n"
            "  python3 -m venv .venv\n"
            "  .venv/bin/pip install -r backend/requirements.txt"
        )
    if not VENV_UVICORN.exists():
        die("虚拟环境中缺少 uvicorn，请执行：.venv/bin/pip install -r backend/requirements.txt")


def ensure_frontend_deps() -> None:
    if (ROOT / "frontend" / "node_modules").exists():
        return
    print("[startup] 安装前端依赖 (npm install)...")
    if subprocess.run(["npm", "install"], cwd=ROOT / "frontend").returncode != 0:
        die("npm install 失败")


def wait_http(url: str, timeout: float = 45.0, label: str = "service") -> None:
    deadline = time.time() + timeout
    last_err = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if 200 <= resp.status < 500:
                    print(f"[startup] {label} 就绪: {url}")
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = str(e)
        time.sleep(0.4)
    die(f"等待 {label} 超时 ({url}): {last_err}")


def run_managed(*, open_browser: bool) -> None:
    """在当前进程托管两个子进程（Ctrl+C 一并停止）。"""
    env = os.environ.copy()
    backend = subprocess.Popen(
        [
            str(VENV_UVICORN),
            "app.main:app",
            "--reload",
            "--app-dir",
            "backend",
            "--host",
            BIND_HOST,
            "--port",
            str(BACKEND_PORT),
        ],
        cwd=ROOT,
        env=env,
    )
    frontend = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(FRONTEND_PORT), "--host", BIND_HOST],
        cwd=ROOT / "frontend",
        env=env,
    )
    procs = [backend, frontend]
    stopping = False

    def shutdown(*_args) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        print("\n[startup] 正在停止服务...")
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    wait_http(HEALTH_URL, label="后端")
    wait_http(LOCAL_FRONTEND_URL, label="前端")
    print_access_urls()
    if open_browser:
        webbrowser.open(LOCAL_FRONTEND_URL)
    print("[startup] 按 Ctrl+C 停止")

    while True:
        if backend.poll() is not None:
            die(f"后端异常退出，code={backend.returncode}")
        if frontend.poll() is not None:
            die(f"前端异常退出，code={frontend.returncode}")
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="启动标书诊断 Demo")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="启动后不自动打开浏览器",
    )
    args = parser.parse_args()

    os.chdir(ROOT)
    ensure_venv()
    ensure_frontend_deps()

    print(f"[startup] 启动后端与前端（监听 {BIND_HOST}）...")
    run_managed(open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
