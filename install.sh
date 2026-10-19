#!/usr/bin/env bash
# Linux / macOS 安装：bash install.sh
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
echo "[1/3] 创建虚拟环境 .venv（Python 3.10+）"
$PY -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
echo "[2/3] 安装依赖"
pip install -r requirements.txt || pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install lunar_python || echo "（农历库没装上，不影响使用）"
echo "[3/3] 检查 ffmpeg（看动图 / 视频要用）"
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "  没找到 ffmpeg：Ubuntu/Debian 运行 sudo apt install ffmpeg；macOS 运行 brew install ffmpeg"
fi
if [ ! -f config.yaml ]; then
  echo; echo "第一次用：回答几个问题（Telegram bot token、大模型 key、选人设）"
  python run.py setup
fi
echo; echo "装好了。先跑一次 python run.py selftest，然后 bash start.sh 启动。"
