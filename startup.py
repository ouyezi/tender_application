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
BACKEND_PORT = 8888
FRONTEND_PORT = 5555
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
        print(f"[startup] 若外机无法访问，请检查本机防火墙是否放行 {FRONTEND_PORT}/{BACKEND_PORT}")


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


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_ports_free() -> None:
    busy = [p for p in (BACKEND_PORT, FRONTEND_PORT) if port_in_use(p)]
    if not busy:
        return
    detail = "、".join(str(p) for p in busy)
    die(
        f"端口被占用: {detail}。\n"
        f"  本项目需要后端 {BACKEND_PORT}、前端 {FRONTEND_PORT}。\n"
        f"  请先结束占用进程后再启动，例如：\n"
        f"    lsof -iTCP:{BACKEND_PORT} -sTCP:LISTEN\n"
        f"    kill <PID>\n"
        f"  可用 lsof -iTCP:{FRONTEND_PORT} -sTCP:LISTEN 查看前端端口占用。"
    )


def wait_backend(timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    last_err = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=1.5) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if resp.status == 200 and '"ok"' in body:
                    print(f"[startup] 后端就绪: {HEALTH_URL}")
                    return
                last_err = f"HTTP {resp.status} body={body[:120]}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = str(e)
        time.sleep(0.4)
    die(
        f"等待后端超时 ({HEALTH_URL}): {last_err}\n"
        "  请确认启动的是本仓库 backend，且 /api/health 返回 {\"ok\": true}"
    )


def wait_frontend(timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    last_err = ""
    while time.time() < deadline:
        try:
            req = urllib.request.Request(LOCAL_FRONTEND_URL, method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if resp.status == 200 and "text/html" in ctype:
                    print(f"[startup] 前端就绪: {LOCAL_FRONTEND_URL}")
                    return
                last_err = f"HTTP {resp.status} Content-Type={ctype}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = str(e)
        time.sleep(0.4)
    die(f"等待前端超时 ({LOCAL_FRONTEND_URL}): {last_err}")


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

    wait_backend()
    wait_frontend()
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
    ensure_ports_free()

    print(f"[startup] 启动后端与前端（监听 {BIND_HOST}）...")
    run_managed(open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
