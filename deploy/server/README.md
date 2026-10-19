# 放在服务器上：小屋首页、备份、掉线提醒、每周体检

`ops.py` 和 `index.html` 是给“三个她都放在一台 Linux 服务器上”准备的（目录结构：`~/ailife/ai-life`、`~/ailife/ai-life-group`、`~/ailife/ai-life-wechat`）。

## 装一次

```bash
sudo mkdir -p /var/www/ailife
sudo cp ~/ailife/ai-life/deploy/server/index.html /var/www/ailife/index.html
# root 的定时任务（要读三个程序的数据、看 systemd 日志、写小屋首页）
sudo crontab -e
```

加这几行（路径按你的改）：

```cron
*/5 * * * * AILIFE_HOME=/home/ubuntu/ailife /home/ubuntu/ailife/ai-life/.venv/bin/python /home/ubuntu/ailife/ai-life/deploy/server/ops.py watch  >> /var/log/ailife-ops.log 2>&1
10 4 * * *  AILIFE_HOME=/home/ubuntu/ailife /home/ubuntu/ailife/ai-life/.venv/bin/python /home/ubuntu/ailife/ai-life/deploy/server/ops.py backup >> /var/log/ailife-ops.log 2>&1
30 4 * * *  AILIFE_HOME=/home/ubuntu/ailife /home/ubuntu/ailife/ai-life/.venv/bin/python /home/ubuntu/ailife/ai-life/deploy/server/ops.py report >> /var/log/ailife-ops.log 2>&1
```

nginx 里让首页能读到两个数据文件（放在已有的 `location = /index.html` 旁边）：

```nginx
location ~ ^/(status|report)\.json$ { root /var/www/ailife; add_header Cache-Control "no-store"; }
```

试一下：`sudo ... ops.py all`，再 `ops.py test-notify`（会在 QQ 上给你发一条测试提醒）。

## 做了什么

- **watch（每 5 分钟）**：三个程序在不在跑、小屋开不开得了、QQ 有没有掉线（直接问 NapCat 的 `get_status`）、微信有没有接上；
  连着两次（10 分钟）不正常就在 QQ 上提醒你（先用 QQ 群小满的号发，不行再用有希的号），一直没好 6 小时再提醒一次，好了也说一声。
  硬盘超过 90%、内存超过 92% 也会提醒。结果写进 `/var/www/ailife/status.json`，首页显示。
- **backup（每天）**：三个数据库（sqlite 在线备份，程序在写也不会坏）、`config.yaml`、`config.llm.yaml`、本地人设 `persona/local_*.md`、
  `data/seed*.yaml`、照片 → `~/ailife/backups/ailife-日期.tar.gz`，留 30 天；`latest.tar.gz` 指向最新的。**`.env`（密钥）不进备份。**
  电脑上双击 `scripts\pull_backup.bat install`，以后每天 12:30 自动拉一份到 `backups\`（留 30 份）。
- **report（每天）**：最近 7 天每个她的消息数、大模型调用次数和失败率、token、估算花费、日志里的报错次数、学到 / 分享 / 提醒 / 语音，
  写进 `report.json`，首页画成表和图；每周一另存一份到 `~/ailife/reports/`。

恢复：解开备份包，把 `data/*.db` 和配置放回对应目录，`.env` 照原样再填，重启服务。
