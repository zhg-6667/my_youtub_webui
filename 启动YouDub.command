#!/bin/bash
set -e

PROJECT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_DIR"

if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
  echo "缺少项目 Python 环境，请先按 README 安装 .venv 和依赖。"
  exit 1
fi

exec "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/start_local.py" "$@"
