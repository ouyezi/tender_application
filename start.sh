#!/usr/bin/env bash
# 标书诊断 Demo 一键启动
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "[start] 未找到 .venv，正在创建并安装依赖..."
  python3 -m venv .venv
  .venv/bin/pip install -r backend/requirements.txt
fi

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "[start] 安装前端依赖..."
  (cd frontend && npm install)
fi

exec .venv/bin/python "$ROOT/startup.py" "$@"
