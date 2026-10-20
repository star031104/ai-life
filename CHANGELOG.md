# 更新记录

## 12.0 — 三个合成一个仓库；群聊 5.0（照着真实微信群改的）

**一个仓库**（以前的 ai-life、ai-life-group、ai-life-wechat）：
- 代码只有一份：`ailife/`（有希）+ `aigroup/`（群聊，QQ 群和微信群共用，差别只在 `aigroup/qq`、`aigroup/wechat`）；
  入口只有 `run.py`：`python run.py yuki | qq | wechat [命令]`（`group`、`wx` 这些老名字也认）
- 三个角色各有自己的家 `home/yuki`、`home/qq`、`home/wechat`（配置、密钥、数据、NapCat、自己写的人设，不进 git）；
  有希的老安装（配置在仓库根目录）照样能用。`--home` / `AILIFE_HOME` / `AIGROUP_HOME` 可以放到别处
- Windows：`start.vbs` 全部启动、`start.bat [名字]`、每个角色一个托盘图标（L / G / W）、开机自启按角色分开；
  `install.bat`、`setup.bat` 会问配哪一个；`scripts\*.bat` 都可以带名字（`llm.bat qq`）
- `scripts/migrate_old.py`：把旧的两个文件夹里的配置、数据、NapCat、人设复制进 `home/`（旧路径自动改）
- 服务器：`deploy/systemd/` 三个服务文件（服务名不变），小管家 `ops.py` 认新的目录；`README.md` 重写成“怎么装、怎么启动”
- 各角色的详细说明搬进 `docs/YUKI.md`、`docs/GROUP_QQ.md`、`docs/GROUP_WECHAT.md`；测试分三组 `tests/yuki`、`tests/group`、`tests/group_qq`

**群聊 5.0**（拿 10 月 20 日两个微信群一早上的真实聊天对出来的问题，都改成了对所有群、所有人设都适用的规则）：
- **没 @ 也接追问**（`chat/address.py`）：她刚说完，同一个人接着说“你……”、或者接她刚说的词（“你小本本上都记啥了”），当成在跟她说；
  有人在背后说她（“它不聪明”“这机器人……”），十分钟内可以回一句（每 10 分钟最多一次）。开关 `guess_to_her`
- **一个梗不连着玩**：她这几个小时用过的口头禅、自己一天里反复说的说法（“验货”“替你答到”）提示词里列出来“别再用”，质检也扣分（`stale_hours`）
- **大多数时候一条**：两条都很短、或者最近老是分两条发，就合成一条（`merge_bubbles`）；质检在一样好的候选里挑条数少的
- **刚认识的人**：种子里事先写了、但真正和她说过话（叫她 + 她回他）不到 5 次的人算“刚认识”：近况、在哪、记得的私事先不写进提示词，
  别一上来叫真名、说出听来的事；问“你怎么知道”就照实说是谁讲的（导入种子时记下 `qq_seeded_<群>`）
- **不叫名字的群**：每个群可以单独关掉“叫人名字”（`call_names`）：不叫任何人的名字、外号，回的不是最新那条就引用；质检拦叫名字的说法。
  每个群还能写一句“关于这个群的交代”（`group_note`），都在小屋“性格开关”里每个群单独调
- **做不到的事别硬圆**：线下的事（替人上课、去找他）、群管理、发红包做不到：开玩笑可以，被戳穿就认；质检拦“人是真坐在课室了”
- **给具体答案**：问吃什么、选哪个，直接给一个答案；质检拦“你们学校附近有啥”“你决定吧”这种推回去的
- **不说幕后的事**：没公开是 AI 的人设，不说“被测试”“验货”“代码还没传完”；毛病大的说法：没被叫到就不说，被叫到就让她重写一次
- **重启前没回的 @**：重启前 20 分钟内有人叫她、她还没回，重启后补回（不说“才看到”，`pending_reply_minutes`）
- 好几个人同一批叫她，每个人都回应到；她今天早就在聊了，就别说“刚爬起来”；在别的群说过的自己的事带过来保持一致（提到别的群里人的不带）；
  起哄让她卖惨讲隐私就挡回去；翻旧账只翻真有的

## 11.4 — 会看视频、写回忆录

- **B 站视频真的“看”了**（`ailife/media/bili.py`，和群聊版同一个文件）：你发 B 站视频，她先回你（“我看看”），后台把视频“看”一遍——
  有字幕读字幕；没字幕就把声音拿下来，一分钟一段转成文字（硅基流动的 SenseVoiceSmall，借你看图那个 Key；只转前 20 分钟）；
  再看弹幕最热闹的几段大家在刷什么、热评说什么，整理成“讲了什么、几个带时间的要点、最好笑的地方、评论区”；
  看完会跟你说两句感想（能说“第 3 分钟那段”），也记成她看过的（`links.watch_videos`）
- **季度回忆录**（`ailife/mind/memoir.py`，表 `memoirs`）：1、4、7、10 月初，她把上个季度写成一本小书——书名、序、四到六章
  （按时间，每章小标题，引用你们真说过的话，配上那个月她发给你的照片）、“给以后的我们”；写好了跟你说一声。
  小屋“她的日子”里有书架，点开像翻书；也能点“先写一份”看这个季度到现在的草稿
- 网页脚本加了语法检查（11.3 的小屋首页因为一个符号整页没动）

## 11.3 — 去掉语音、每天花费上限

- **语音去掉了**：她不发语音（11.2 的 `[语音]` 标签、`voice` 配置、`ailife/media/voice.py` 都删了；模型偶尔还打这个标签，照样去掉、发文字）
- **每天花费上限**（`llm.budget`，`ailife/llm/budget.py`）：按大模型返回的 token 估算（画图按张算），到了上限后台的活（整理记忆、日记、搜一搜、画图……）先停，
  聊天再多用一成也停，第二天恢复。放在服务器上时三个程序共用一本账（`shared_file`，服务器小管家每 5 分钟写一次），就是三个加起来的上限；
  用到八成、到上限都会在 QQ 上提醒你；小屋首页显示今天花了多少
- 服务器小管家：写共享账本 `budget.json`、首页加“今天花了”

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
