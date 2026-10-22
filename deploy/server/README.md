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
  21.1：也看“群窗口不见了 / 连着发不出去”。提醒先走推送服务（QQ 掉线时也能收到）：把推送地址写进 `$AILIFE_HOME/notify_url`
  （第一行地址，第二行 token，可以没有），或者设环境变量 `AILIFE_NOTIFY_URL` / `AILIFE_NOTIFY_TOKEN`。地址里有 `{text}` 就用 GET
  （比如 Bark：`https://api.day.app/<你的key>/{text}`），否则 POST JSON（PushPlus：`http://www.pushplus.plus/send` + token；Server 酱：`https://sctapi.ftqq.com/<key>.send`）。
  硬盘超过 90%、内存超过 92% 也会提醒。结果写进 `/var/www/ailife/status.json`，首页显示。
- **NapCat 掉登录不用扫码（12.1，watch 里顺便做）**：QQ 把号踢下线后 NapCat 会停在“请扫描二维码”。
  `$AILIFE_HOME/napcat/<名字>/password.md5` 存了小号密码的 MD5 时（`read -rs p && printf %s "$p" | md5sum | cut -d" " -f1 > group/password.md5`），
  就用 `napcat/up.sh <名字>` 重启它（先快速登录，不行用密码；同一个最多 30 分钟一次）。QQ 要短信验证时，在 QQ 上提醒你打开
  `<公网地址>/webui/` 去收短信、填验证码。公网地址写在 `$AILIFE_HOME/public_url`（或环境变量 `AILIFE_PUBLIC_URL`）。
  nginx 里这样开放 NapCat 网页（`/webui/` 走小屋的用户名密码；`/api/` 自带 NapCat 口令，和 basic auth 冲突，改用一个只有过了密码那关才拿得到的 cookie 把门，`随机串` 自己换一个）：

  ```nginx
  location /webui/ { proxy_pass http://127.0.0.1:6100/webui/; add_header Set-Cookie "ncgate=随机串; Path=/; HttpOnly; SameSite=Strict" always; }
  location /api/   { auth_basic off; if ($cookie_ncgate != "随机串") { return 403; } proxy_pass http://127.0.0.1:6100/api/; proxy_buffering off; }
  location /files/ { auth_basic off; if ($cookie_ncgate != "随机串") { return 403; } proxy_pass http://127.0.0.1:6100/files/; }
  ```
- **backup（每天）**：三个数据库（sqlite 在线备份，程序在写也不会坏）、`config.yaml`、`config.llm.yaml`、本地人设 `persona/local_*.md`、
  `data/seed*.yaml`、照片 → `~/ailife/backups/ailife-日期.tar.gz`，留 30 天；`latest.tar.gz` 指向最新的。**`.env`（密钥）不进备份。**
  电脑上双击 `scripts\pull_backup.bat install`，以后每天 12:30 自动拉一份到 `backups\`（留 30 份）。
- **report（每天）**：最近 7 天每个她的消息数、大模型调用次数和失败率、token、估算花费、日志里的报错次数、学到 / 分享 / 提醒 / 语音，
  写进 `report.json`，首页画成表和图；每周一另存一份到 `~/ailife/reports/`。

恢复：解开备份包，把 `data/*.db` 和配置放回对应目录，`.env` 照原样再填，重启服务。
