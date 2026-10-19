#!/usr/bin/env bash
# 她跑在 Linux 服务器上时，在自己电脑（macOS / Linux）上打开小屋：SSH 隧道把服务器只开在本机的端口接过来。
#   8765 有希（QQ 私聊）  8767 小满（QQ 群）  8768 小满（微信群）  5800 微信网页 VNC（扫码）  6099/6100 NapCat 网页
# 用法：bash scripts/open_home.sh ubuntu@1.2.3.4 [端口]（服务器第一次填完会记在 data/server.txt）
cd "$(dirname "$0")/.." || exit 1
mkdir -p data
[ -n "$1" ] && echo "$1" > data/server.txt
SRV=$(cat data/server.txt 2>/dev/null)
[ -z "$SRV" ] && { echo "用法：bash scripts/open_home.sh ubuntu@服务器IP"; exit 1; }
KEY=(); [ -f data/server_key ] && KEY=(-i data/server_key)
ssh "${KEY[@]}" -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -o ServerAliveInterval=30 -f -N \
  -L 8765:127.0.0.1:8765 -L 8767:127.0.0.1:8767 -L 8768:127.0.0.1:8768 \
  -L 5800:127.0.0.1:5800 -L 6099:127.0.0.1:6099 -L 6100:127.0.0.1:6100 "$SRV" || exit 1
URL="http://127.0.0.1:${2:-8765}"
(command -v open >/dev/null && open "$URL") || (command -v xdg-open >/dev/null && xdg-open "$URL") || echo "浏览器打开 $URL"
