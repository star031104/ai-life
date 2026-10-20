#!/usr/bin/env bash
# Linux / macOS 安装：bash install.sh [yuki|qq|wechat]（三个共用一个 .venv；最后跑那一个的配置向导）
# 服务器上微信群用 Docker 跑微信，见 docs/服务器部署.md
set -e
cd "$(dirname "$0")"
T=${1:-yuki}
PY=${PYTHON:-python3}
echo "[1/3] 创建虚拟环境 .venv（Python 3.10+）"
[ -d .venv ] || $PY -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
echo "[2/3] 安装依赖"
pip install -r requirements.txt || pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install lunar_python || echo "（农历库没装上，不影响使用）"
pip install trafilatura || echo "（trafilatura 没装上，不影响使用：读网页用自带的办法）"
echo "[3/3] 检查 ffmpeg（看动图 / 视频、听 B 站视频要用）"
command -v ffmpeg >/dev/null 2>&1 || echo "  没找到 ffmpeg：Ubuntu/Debian 运行 sudo apt install ffmpeg；macOS 运行 brew install ffmpeg"
if [ ! -f "home/$T/config.yaml" ]; then
  echo; echo "第一次用：配置 $T"
  python run.py "$T" setup
fi
echo; echo "装好了。bash start.sh $T 启动（服务器上用 systemd，见 docs/服务器部署.md）。"
