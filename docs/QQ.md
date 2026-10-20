# QQ：一对一的她

她在 QQ 私聊里陪你：聊天、主动消息、命令、每周来信、纪念册、关程序时的道别，全都走 QQ。QQ 私聊是唯一的渠道。

> **想要一个待在 QQ 群里的角色？** 同一个仓库里的 `python run.py qq`，见 [GROUP_QQ.md](GROUP_QQ.md)。
> 它用另一个 QQ 小号、另一个人设、另一份数据，和这里的她互不相干，两个可以同时开。ai-life 只管私聊，收到的群消息一律不理。

## 先知道这几件事

1. **QQ 没有给个人用的机器人接口**，这里用的是 NapCat（非官方协议端）登录一个 QQ 小号。**有封号风险，一定用小号，别用主号。**
2. NapCat 和 ai-life 跑在同一台机器上：你的电脑，或者一台 Linux 服务器（NapCat 用 Docker）。机器关着时她在 QQ 上也不在线（开机后会补上错过的事）。

## 第一步：一键配置（Windows）

双击项目文件夹里的 **`scripts\qq_setup.bat`**（第一次用 `install.bat` 时在配置向导里选 QQ 也一样）。它会：

1. **找 NapCat**：程序目录下的 `napcat/`、或者你以前自己装的（桌面、下载、各个盘的根目录）。都没有就**自动下载**官方的
   `NapCat.Shell.Windows.Node.zip`（约 120 MB，自带 QQ 内核，**不用另外装 QQ**）解压到 `napcat/`。
   GitHub 慢时依次换几个公共加速地址；全失败就提示你手动下载，把 zip 放进项目文件夹再运行一次。
2. **配好 WebSocket**：在 NapCat 的配置里写好 `127.0.0.1:3001`、消息格式 array、随机 token（不用打开 WebUI 手动建）。
3. **启动 NapCat 并登录**：第一次会弹出二维码，**用她的小号**在手机 QQ 上扫码确认。
   扫码前先退出电脑上已登录的这个号的 QQ。⚠️ NapCat 是非官方协议端，**有封号风险，一定用小号**。
4. **打开自动登录**：把小号写进 NapCat 的自动登录设置，以后带 `-q 号码` 启动，**不用再扫码**。
5. **选出你自己**：列出小号的好友，输入编号选你自己的 QQ。
6. **写好 `config.yaml`** 的 `qq:` 一节（先备份到 `data/backup/`，token 放进 `.env`），写完重新读一遍核对，不对就原样恢复。

<details>
<summary>手动装 NapCat / Linux 服务器（Docker）</summary>

1. Windows：打开 NapCat 的官方发布页 <https://github.com/NapNeko/NapCatQQ/releases>，下载 `NapCat.Shell.Windows.Node.zip`。只从官方 GitHub 下载。
   Linux：用 Docker 版 `mlikiowa/napcat-docker`，端口只绑在本机：

   ```bash
   mkdir -p ~/napcat/config ~/napcat/qq
   sudo docker run -d --name napcat --restart always \
     -e NAPCAT_UID=$(id -u) -e NAPCAT_GID=$(id -g) \
     -p 127.0.0.1:3001:3001 -p 127.0.0.1:6099:6099 \
     -v ~/napcat/config:/app/napcat/config -v ~/napcat/qq:/app/.config/QQ \
     mlikiowa/napcat-docker:latest
   sudo docker logs -f napcat      # 日志里有登录二维码
   ```

2. 启动后用小号扫码登录；浏览器打开 WebUI（默认 `http://127.0.0.1:6099`，token 在窗口 / 日志里；服务器上先在自己电脑开
   `ssh -N -L 6099:127.0.0.1:6099 ubuntu@你的服务器地址`），**网络配置** → 新建 **WebSocket 服务器**：主机 `127.0.0.1`
   （Docker 里写 `0.0.0.0`，外面靠 `-p 127.0.0.1:3001:3001` 挡住）、端口 `3001`、消息格式 **array**、设一个 token，启用并保存。
3. 运行 `python run.py qqsetup --manual`：只读现成的 NapCat 配置，选你的 QQ，写好 `config.yaml`。

`config.yaml` 里的 `qq:` 一节长这样（字段说明见 `config.example.yaml`）：

```yaml
qq:
  enabled: true
  ws_url: "ws://127.0.0.1:3001"
  access_token: "${QQ_ACCESS_TOKEN}"   # .env 里写 QQ_ACCESS_TOKEN=NapCat 里那串 token
  owner_id: 12345678                   # 你自己的 QQ 号（这里是占位）
  owner_name: ""                       # 你在她的世界里是谁；一般写在人设里，这里是备用
  napcat_dir: "C:/ai-life/napcat"      # Windows：start.bat 顺便启动 NapCat 用（服务器上不用）
  napcat_qq: 87654321                  # Windows：她的小号，快速登录用，有了它就不用每次扫码
```

</details>

## 第二步：启动和测试

1. **加好友**：用你自己的 QQ 加她的小号为好友（程序开着时会自动同意）。**这一步必须做**：QQ 不允许给非好友主动发私聊，
   不加好友的话她只能回你，早安、想你、提醒这些主动消息都发不出去。`qq_setup` 和 `/diag` 都会提示还不是好友。
2. 双击 `start.bat`（右下角出现托盘图标；服务器上是 `sudo systemctl start ai-life`）。右键图标 →“看日志”
   （或 `data/logs/ailife.log`），看到 `QQ 已连上 NapCat` 和 `QQ 账号：…` 就通了。
3. 用你的 QQ 给她发消息。小屋在电脑浏览器里打开 `http://127.0.0.1:8765`（给她发 `/小屋` 也会告诉你）；她在服务器上时用 SSH 隧道，见 README。

以后（Windows）只要双击 `start.bat`：NapCat 没在运行的话，它会顺便在后台把 NapCat 启动（没有窗口，输出在 `data/logs/napcat.log`；想要窗口就在 `qq:` 里写 `napcat_hidden: false`），
带 `-q 小号` 快速登录，不用再扫码。只有 QQ 那边要求重新验证（换了密码、在手机上被踢、很久没登录）时才要再扫，
那时二维码图片会自动弹出来。
NapCat 已经开着就不会再开第二个。不想让它自动开：`config.yaml` 的 `qq:` 里写 `napcat_autostart: false`。
Linux 上 NapCat 由 Docker 自己管（`--restart always`），这几项不起作用。

## QQ 私聊里的她

| | QQ 私聊 |
|---|---|
| 文字、分条、打字停顿 | ✓，你那边会显示“对方正在输入…” |
| 发照片 | ✓ |
| 表情 | 先用**从你那学来的表情包**，没有合适的就发 QQ 小黄脸，再不行用 emoji |
| 打错字 | QQ 改不了已发的消息，所以像真人一样补一句“*正确的字” |
| 撤回 | ✓ 说出口又后悔，几秒后撤回 |
| 认你发的图片 | ✓ |
| 认你发的表情包 | ✓ 看懂是什么情绪，存下来自己以后也用 |
| 你戳她 | ✓ 她会回应，有时戳回去（`poke_back`） |
| 你撤回消息 | ✓ 她已经看到了，可能逗你一句，也可能装没看见 |
| 合并转发的聊天记录 | ✓ 会读里面的内容 |
| 分享卡片（B 站、小程序、链接） | ✓ 认出标题和链接，能点开读的就读完再回 |
| 推歌 | ✓ 附上网易云链接 |
| 引用很早以前的消息 | ✓（去 NapCat 取原文） |
| 个性签名 | ✓ 每天跟着她的安排和心情变（`signature`） |
| 命令 | ✓（也能用中文：/日记 /记忆 /心情 /小屋 /打卡 /帮助） |
| 小屋 | /小屋 告诉你地址（`http://127.0.0.1:8765`，只在本机能打开） |
| 告诉她“这句不对味” | 引用她那句话，回一个 👎（进小屋的“她会避开的说法”） |
| 每周来信、纪念册、时间胶囊、提醒、周报 | ✓ |
| 关程序时道别 | ✓（走 NapCat 的 WebSocket，不行再用 `qq.http_url`） |

发送失败（NapCat 偶尔超时、正在重连）会自动等一下再试一次。`/diag` 里有一行 QQ 状态：连没连上、是不是好友、你上次在 QQ 说话是多久前。

## 出问题时

- 日志一直说 `连不上 NapCat`：NapCat 没开，或者 WebSocket 服务器没启用 / 端口不是 3001。
- 她回你，但从来不主动找你：她的小号还不是你的好友（看 `/diag` 的 QQ 那行）。
- 你那边看不到“正在输入”、戳不回去、签名没变：NapCat 版本太旧，没有这几个扩展接口，更新 NapCat；聊天本身不受影响。
- 连上了但私聊不回：`qq.owner_id` 填的不是你自己的号（日志会提示“不是 qq.owner_id，不理”）。
- 服务器上连不上：`docker ps` 看 NapCat 容器在不在跑；容器里 WebSocket 服务器的主机要写 `0.0.0.0`，`-p` 那边绑 `127.0.0.1:3001`。
