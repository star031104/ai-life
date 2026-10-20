# 放在 Linux 服务器上（24 小时在线）

Windows 版要电脑一直开着、不锁屏。放到一台 Linux 服务器（2 核 4G 就够，和 QQ 私聊、QQ 群两个项目一起跑也够）上，
她就能一直在群里。Windows 版照旧能用，两边代码是同一份：**config.yaml 里写了 `wechat.container`（或 `wechat.linux: true`）
才走 Linux 的方式**，不写就和以前一样。

## 原理（和 Windows 版一样只读、不注入）

| 要做的事 | Windows 版 | Linux 版 |
| --- | --- | --- |
| 微信本身 | 电脑上的微信 4.x | Linux 版微信 4.x，跑在 Docker 容器里（[ricwang/docker-wechat](https://github.com/ricwang/docker-wechat)，带网页 VNC） |
| 读群消息 | 读微信本地库（SQLCipher 4） | 一样：容器里的 `/root/xwechat_files` 挂到宿主机，程序直接读 |
| 拿库密钥 | `ReadProcessMemory` 只读微信内存 | `/proc/<pid>/mem` 只读微信内存（宿主机能看到容器里的进程，要 root） |
| 认哪个进程开着她的库 | Restart Manager | `/proc/<pid>/fd`（按“账号目录/db_storage/…”这一段比，容器里外路径不一样也认得出） |
| 发消息 | 切窗口 → 剪贴板 → Ctrl+V → 回车 | 一样的步骤，用 `xdotool` + `xclip`，通过 `docker exec` 在容器的显示里做 |

服务器上没有人用键盘鼠标，也不会锁屏，所以不用“等你停手”。代码在 `aigroup/wechat/linux.py`。

## 步骤

1. 服务器装好 Docker 和 Python 3.10+（Ubuntu 24.04 自带 3.12），把代码放到比如 `~/ailife/ai-life-wechat`：

   ```bash
   git clone https://github.com/star031104/ai-life-wechat.git ~/ailife/ai-life-wechat
   cd ~/ailife/ai-life-wechat && bash install.sh      # 建 .venv、装依赖（向导可以先 Ctrl+C 跳过，下面直接用电脑上的配置）
   ```

2. 起微信容器（第一次会构建一个加了 xdotool / xclip 的镜像）：

   ```bash
   bash scripts/linux/wechat_up.sh                   # 数据放在 ~/ailife/wechat，可以用 WECHAT_DIR=… 换
   ```

3. 扫码：网页 VNC 只开在服务器的 127.0.0.1:5800，在**自己电脑**上开隧道再打开：

   ```bash
   ssh -N -L 5800:127.0.0.1:5800 ubuntu@服务器IP       # 开着别关
   ```

   浏览器打开 http://127.0.0.1:5800 ，用**她的小号**扫码登录。登录后在微信里**双击每个她待的群，让它单独开一个窗口**
   （和 Windows 版一样：没有单独窗口不发，保证不会发错群）。

4. 配置：把电脑上的 `config.yaml`、`config.llm.yaml`、`.env`、`persona/`、`data/`（她的记忆）复制过去，
   然后在 `config.yaml` 的 `wechat:` 下面加上：

   ```yaml
   wechat:
     container: wechat                                   # 微信所在的容器名 → 自动用 Linux 方式
     data_dir: /home/ubuntu/ailife/wechat/data           # 容器里 /root/xwechat_files 挂出来的目录
     outbox: /home/ubuntu/ailife/wechat/data/_outbox     # 发图片时先放这（微信看得到）
     path_map: {"/home/ubuntu/ailife/wechat/data": "/root/xwechat_files"}
     account: ""                                         # Linux 上的账号目录名和电脑上不一样：先清空，体检会列出来
   ```

   Linux 上的库密钥和电脑上不一样，`data/wechat/keys.json` 里电脑的那份用不上也没关系，会重新拿。

5. 体检、试发：

   ```bash
   sudo .venv/bin/python run.py wechat wxdiag                # 数据目录、密钥、能不能读到群消息、窗口
   sudo .venv/bin/python run.py wechat wxsend 群名 测试一下
   ```

6. 开机自启（systemd）：

   ```bash
   sudo cp deploy/systemd/ai-life-wechat.service /etc/systemd/system/   # 路径不一样的话先改里面的路径
   sudo systemctl daemon-reload && sudo systemctl enable --now ai-life-wechat
   journalctl -u ai-life-wechat -f                     # 看日志
   ```

7. 小屋：也只开在服务器本机（8768），在自己电脑上 `ssh -N -L 8768:127.0.0.1:8768 ubuntu@服务器IP` 后打开
   http://127.0.0.1:8768 。装了 ai-life 的话，它的 `scripts/open_home.bat` 会把三个小屋和微信 VNC 的端口一次接好。

## 常见问题

- **读不了微信进程的内存**：程序要用 root 跑（上面的 systemd 服务就是 `User=root`），或者 `sudo setcap cap_sys_ptrace+ep` 给 python。
- **容器一直重启、日志里 dpkg 报 statoverride**：别用 `ENABLE_CJK_FONT` / `INSTALL_PACKAGES` 这两个环境变量，用这里的 Dockerfile（已经装好字体和工具）。
- **微信退出登录了**：手机上点过“退出电脑版”或者长时间没确认，重新开隧道扫一次码，单独窗口也要重新双击打开。
- **内存**：微信容器大约 300～600 MB，限制在 1.5 GB（`--memory 1500m`）。
