# 更新记录

## 11.2 — 会发语音、会随手拍、自己慢慢长

- **语音**：很短、想让他听见声音的时候（撒娇、哄他、早安晚安）她会在那条前面打 `[语音]`，这条用语音发（`config.yaml` 的 `voice`，
  默认硅基流动的 CosyVoice2，和看图同一个 Key，一条不到 1 分钱；每天最多 6 条，超过 60 个字的不念；没配 / 失败照样发文字）（`ailife/media/voice.py`）
- **随手拍**：除了你想看时发照片，她每周会主动发几次“看看我在干嘛”的照片（和今天的安排对得上；`photo.proactive_per_week`，默认 3）。
  画图接口兼容了硅基流动的返回格式（Z-Image-Turbo / Kolors）
- **说出口的喜好不改口**：每天把昨天她亲口说的喜好和看法整理一遍，第一次说的就定下来，每次聊天都带上（`ailife/mind/selfgrow.py`）
- **每月整理她自己的事**：每月初把“她自己说过的事”合并一遍：重复的合成一条、前后矛盾的以新的为准（一次最多删三成）
- **被纠正了就改**：你说“不对 / 其实是……”而她刚用了查过的东西，后台对一下，对的就把那条改掉
- **记 token 和花费**：大模型每天的用量多记了 token 数和估算花费（和群聊版一样），服务器上的小屋首页“每周体检”用
- **小屋**：“日记与来信”改名“她的日子”，最上面多了她说过的喜好（能删、能加）、这周的语音 / 照片 / 整理、她最近查过的事；“设置”收进右边的“管理”
- **服务器上的小管家**（`deploy/server/`）：小屋首页（三个她在不在线、今天聊了多少、服务器状态、每周体检、最近的提醒）；
  每天备份三个数据库 + 配置 + 本地人设（留 30 天，`scripts\pull_backup.bat install` 每天拉一份到电脑）；
  每 5 分钟看一眼，掉线超过 10 分钟在 QQ 上提醒你（`deploy/server/README.md`）

## 11.1 — 会上网查、有学期主线

- **不知道的先搜一搜**：你问到她可能不知道的事（是什么 / 讲了什么 / 什么时候出 / 比分……），后台模型先判断要不要查、查什么，
  要查就在百度 / 360 / 搜狗里搜（不用 Key，结果对不上就换下一个；`net.tavily_key` 填了用 Tavily 兜底），把几条摘要给她看，
  她像自己刚看到的那样回你；查过的要点记成她自己知道的事（`ailife/media/search.py`，`Companion._maybe_search`）。每小时最多 `net.per_hour` 次
- **要登录才能看的链接**：`links.cookies` 可以填你自己浏览器里某个网站的 cookie（比如知乎），读链接时带上
- **这学期的主线**：学生人设每学期开一条跟着校历走的长期目标——她这学期想做成的一件事（比如钢琴考级）、期中忙起来、期末考试周，
  最后一步是放假回家见你；每到一步她会跟你说进展（`herworld.semester_goal`）

## 11.0 — 只走 QQ，能放服务器

这一版做减法：只留 QQ 私聊，去掉只在 Windows 桌面上才有意义的东西，让她可以放在一台 Linux 服务器上 24 小时在线。

**从 10.x 升级**：`config.yaml`、`config.llm.yaml`、`.env`、`data/` 和你的人设都不用动。`config.yaml` 里旧的 `telegram:`、
`miniapp:`、`pet:`、`sense.pc` 几节不再读，可以删掉。以前只用 Telegram 的话，要先配一次 QQ（`scripts\qq_setup.bat`），
聊天记录和记忆都还在。`data/pet/` 也可以删掉。

### 去掉的

- **Telegram**：整块去掉，不再依赖 python-telegram-bot。原来的 `ailife/channels/telegram/` 拆成和渠道无关的 `ailife/chat/`（见下）。
- **手机上的小屋**（Telegram Mini App）：连同对外的 8766 端口、cloudflared 免费隧道（`web/tunnel.py`）、Mini App 签名校验一起去掉。
- **桌宠**（Live2D / 像素宠物，`ailife/pet/`、`pet_setup.bat`、PySide6 / pywebview）：整块去掉。
- **电脑感知**（前台程序、游戏、视频，正在放的歌，`sense/sensors.py`、`/sense` 里的电脑设置、`sense.pc`）：整块去掉。

### 留下的、改了的

- **QQ 私聊是唯一的渠道**（NapCat，OneBot 11，`ailife/channels/qq/`）。每周来信、纪念册、周报、提醒、关程序时的道别都走 QQ；
  道别走 NapCat 的 WebSocket，不行再用 `qq.http_url`。
- **新的 `ailife/chat/` 包**：`companion.py`（`Companion`，中枢：等他打完字、生成回复、出错重试、睡前故事、聊完整理记忆）、
  `delivery.py`（拟人化发送：分条、打字停顿、打错字再补一句“*正确的字”、表情包 → 学来的 QQ 表情包 → QQ 小黄脸 → emoji、撤回、推歌附链接）、
  `commands.py` + `life_cmds.py`（各个 /命令）、`command.py`（一条命令：`text` / `args` / `rest` / `reply()`）、`common.py`。
- **手机信号**（ntfy 快捷指令：起床 / 到家 / 充电）留着，挪到 `ailife/sense/phone.py`；`/sense` 只管它。
- **小屋**只在本机：`http://127.0.0.1:8765`。放在服务器上时，在自己电脑上开 SSH 隧道
  `ssh -N -L 8765:127.0.0.1:8765 ubuntu@服务器地址` 再打开同一个地址。
- **配置**：`qq.enabled` 默认 `true`；新加 `qq.owner_name`（你在她世界里的称呼，人设没写时的备用）。配置向导只问：代理（只有大模型接口需要时）、
  你自己的 QQ 号、城市、人设、大模型，然后接着配 NapCat。
- **能放 Linux 服务器**：Windows 照旧用 `start.bat` + 托盘；Linux 用 `start.sh` 或 systemd（`ai-life.service`，`Restart=always`、
  `RestartPreventExitStatus=3`）。README 新加“放在 Linux 服务器上（24 小时在线）”：装程序、从电脑搬数据、NapCat 用 Docker
  （`mlikiowa/napcat-docker`，端口只绑 127.0.0.1）、systemd 服务、SSH 隧道看小屋。一台 2 核 4G 的轻量服务器就能同时跑她、
  ai-life-group（QQ 群）和 ai-life-wechat（微信群）。
- **测试**：114 个（原来 134 个，去掉了 Telegram、桌宠、电脑感知的测试）；`tests/fakes.py` 里用 `FakeQQ` 代替原来的 `FakeTGBot`。
- 文档（README、docs/）全部按上面改过。私人人设（比如自己用的角色）的调整不在仓库里：私人人设文件在 `.gitignore` 里，不提交。

## 10.0 — 重构

这一版不加新功能，把代码整理成好维护的样子，并把大模型改成“全部由你自己加”。

**从 9.x 升级**：9.x 的自动更新装不了 10.0（目录结构变了），请手动更新一次：下载新的代码覆盖（或 `git pull`），
`config.yaml`、`.env`、`data/` 和你的人设都不用动。然后双击 `scripts\llm.bat` 加一个聊天模型（以前内置的地址没有了，
原来 `.env` 里的 Key 还能直接粘贴进去）。旧版本留在 `ailife/` 根下的 `.py` 文件和根目录的旧 `.bat` 可以删掉，
下次自动更新也会把它们挪进备份。

### 大模型

- 不再内置任何接口地址和预设：`llm.profiles` 默认是空的，所有模型都是你填 URL 加的。
- 加模型的流程统一成：填接口地址和 Key → 测连通、列出模型 → 选一个 → 试一下 → 选它干什么。小屋“设置 → 大模型”、
  `python run.py llm`、配置向导走的是同一套代码（`ailife/llm/store.py`、`probe.py`）。
- 按“用途”分配模型：聊天、后台、记忆整理、夜里、看图、画图、记忆检索（`ailife/llm/roles.py`）。每个用途可以排几个，
  前一个出错换下一个；后台类的用途没配就跟着上一级，看图 / 画图 / 检索没配就不做。
- 画图和向量也走同一套配置（`llm.routes.image`、`llm.routes.embed`），不再单独写地址和 Key。
- 你加的模型写在 `config.llm.yaml`，Key 写进 `.env`（`LLM_<名字>_API_KEY`），配置里只放 `${名字}` 引用。
- 没有聊天模型时启动会直接说清楚怎么加，不再带着坏配置跑起来。

### 代码结构

- `ailife/` 从四十多个平铺的文件分成包：`core/`、`llm/`、`persona/`、`mind/`、`proactive/`、`channels/telegram/`、
  `channels/qq/`、`media/`、`sense/`、`web/`、`pet/`。依赖只往下走，说明在 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
- 命令行改成命令表（`ailife/cli.py` 的 `COMMANDS`），加命令只加一行；启动流程挪到 `ailife/app.py`，`run.py` 只剩入口。
- 小屋的接口按功能拆成几个 Mixin（`records`、`life`、`persona`、`system`），门卫单独放 `web/auth.py`。
- 桌宠的网页接口和窗口代码分开（`pet/server.py`、`pet/window.py`）。
- 根目录只留 `start.bat`、`setup.bat`、`install.bat`，其他脚本挪进 `scripts/`。
- 测试用的假对象统一放 `tests/fakes.py`，共用环境放 `tests/conftest.py`；加了 `pyproject.toml`（pytest、ruff 设置）。
- 自动更新会把新版本里已经没有的旧代码文件拿掉（先放进备份，能回滚）。
- 桌宠默认的 Live2D 形象换回 Haru（Rice 还在，`pet.preset: rice`）。
- 仓库里不再带任何私人内容：示例配置里的号码、Key 都是占位符，私人人设不进仓库。

## 9.7

- 桌宠：提醒方式可选（气泡 / 小红点 / 不提醒）和停留时间；有程序全屏时自动躲开；你很久没动电脑时安静一点。
- 支持更多像素宠物，形象和人格分开（换皮不换人）。

## 9.6

- 桌宠支持 Codex 风格的像素精灵图（从 codex-pets.net 下载）。

## 9.5

- 桌宠支持 Live2D 形象；整理了一键部署和文档。

更早的版本见 git 历史。
