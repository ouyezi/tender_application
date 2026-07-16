#!/usr/bin/env python3
"""一键启动标书诊断 Demo（后端 FastAPI + 前端 Vite）。"""

from __future__ import annotations

import os
import signal
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
HEALTH_URL = f"http://127.0.0.1:{BACKEND_PORT}/api/health"
FRONTEND_URL = f"http://localhost:{FRONTEND_PORT}"
DOCS_URL = f"http://localhost:{BACKEND_PORT}/docs"


def die(msg: str, code: int = 1) -> None:
    print(f"[startup] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


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


def _as_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def open_macos_terminals() -> bool:
    """在 Terminal.app 中各开一个窗口跑前后端。"""
    if sys.platform != "darwin":
        return False

    backend_line = (
        f"cd {_as_escape(str(ROOT))} && "
        f"{_as_escape(str(VENV_UVICORN))} app.main:app --reload "
        f"--app-dir backend --port {BACKEND_PORT}"
    )
    frontend_line = (
        f"cd {_as_escape(str(ROOT / 'frontend'))} && "
        f"npm run dev -- --port {FRONTEND_PORT} --host 127.0.0.1"
    )
    script = f'''
tell application "Terminal"
    activate
    do script "{backend_line}"
    delay 0.4
    do script "{frontend_line}"
end tell
'''
    return subprocess.run(["osascript", "-e", script]).returncode == 0


def run_managed() -> None:
    """在当前进程托管两个子进程（Ctrl+C 一并停止）。"""
    env = os.environ.copy()
    backend = subprocess.Popen(
        [
            str(VENV_UVICORN),
            "app.main:app",
            "--reload",
            "--app-dir",
            "backend",
            "--port",
            str(BACKEND_PORT),
        ],
        cwd=ROOT,
        env=env,
    )
    frontend = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(FRONTEND_PORT), "--host", "127.0.0.1"],
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
    wait_http(FRONTEND_URL, label="前端")
    print(f"[startup] 前端: {FRONTEND_URL}")
    print(f"[startup] API 文档: {DOCS_URL}")
    webbrowser.open(FRONTEND_URL)
    print("[startup] 按 Ctrl+C 停止")

    while True:
        if backend.poll() is not None:
            die(f"后端异常退出，code={backend.returncode}")
        if frontend.poll() is not None:
            die(f"前端异常退出，code={frontend.returncode}")
        time.sleep(1)


def main() -> None:
    os.chdir(ROOT)
    ensure_venv()
    ensure_frontend_deps()

    print("[startup] 启动后端与前端...")

    if open_macos_terminals():
        wait_http(HEALTH_URL, label="后端")
        wait_http(FRONTEND_URL, label="前端")
        print(f"[startup] 前端: {FRONTEND_URL}")
        print(f"[startup] API 文档: {DOCS_URL}")
        print("[startup] 已在 Terminal.app 中弹出两个窗口")
        webbrowser.open(FRONTEND_URL)
        return

    run_managed()


if __name__ == "__main__":
    main()
