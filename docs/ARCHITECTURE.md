# 代码结构

这份文档写给要改代码的人：每个包管什么、谁依赖谁、加一个功能该动哪里。用的人看 [README](../README.md)。

## 分层

```
            cli.py ──► app.py（把下面的东西装起来、启动、收尾）
                         │
        ┌────────────────┼─────────────┬──────────┐
   channels/qq/      proactive/      web/       sense/    （对外：QQ 私聊、定时、小屋网页、手机信号）
        └────────────────┼─────────────┴──────────┘
                       chat/（和渠道无关的聊天中枢 Companion：等他说完、回复、拟人化发送、/命令）
                         │
                 mind/（她的“脑子”：Brain 和它的各个部件）
             ┌─────┴──────┬───────────┐
          persona/      media/       llm/        （人设、链接 / 照片 / ffmpeg、大模型）
             └────────────┴─────┬─────┘
                              core/              （配置、密钥、数据库、日志、自检、备份、更新）
```

依赖只往下走：`core` 不 import 上面任何包；`llm`、`persona`、`media` 只用 `core`；`mind` 用它们；
`chat` 用 `mind`；对外的几个包用 `chat` 和 `mind`。同一层之间尽量不互相 import（少数例外是函数里的延迟 import，
比如 `chat/companion.py` 要调 `proactive` 的免打扰判断，`chat/delivery.py` 要用 `channels/qq/onebot.py` 的 QQ 小黄脸表）。

QQ 私聊（NapCat，OneBot 11）是现在唯一的渠道。`chat/` 不认识任何具体渠道：它通过一个“发送器”往外发
（方法名是通用的 `send_message` / `send_photo` / `delete_message` / `typing` ……），QQ 那边的发送器是
`channels/qq/onebot.py` 的 `QQOut`。以后要接别的渠道，给一个同样方法的发送器、把收到的话交给 `Companion` 就行。

## 每个包

| 包 | 管什么 | 主要的类 / 入口 |
| --- | --- | --- |
| `ailife/cli.py` | 命令行：`python run.py <命令>`，命令表 `COMMANDS` | `main()` |
| `ailife/app.py` | 启动顺序、托盘通信（`data/tray_cmd`）、收尾道别 | `run()`、`console()` |
| `ailife/wizard.py` | 第一次的配置向导（代理、你的 QQ、城市、人设、大模型，再接着配 NapCat）、人设命令 | `setup()` |
| `core/` | `config`（读 config.example.yaml → config.yaml → config.llm.yaml，`${名字}` 从 .env 取）、`secrets`（密钥进 .env、日志打码）、`db`（SQLite）、`health`（日志文件、/diag、开机自启）、`selftest`、`backup`（打包 / 导入）、`update`（自动更新与回滚）、`timeutil`、`deps` | `load_config()`、`DB` |
| `llm/` | 大模型。`roles` 用途和兜底规则；`client` 聊天 / 看图（按路由兜底、冷却、统计）；`store` 你加的模型（config.llm.yaml）；`probe` 测连通、列模型、试一下；`embed` 向量；`image` 画图；`cli` 命令行管理 | `LLM`、`store.add_model()` |
| `persona/` | 人设文件的格式（`loader`）、角色库（新建 / 导入角色卡 / 切换，`library`）、对话示例挑选 | `load_persona()` |
| `mind/` | `brain` 拼提示词、生成回复；`memory` + `retrieval` 记忆与检索；`emotion` 情绪；`rhythm` 作息与校历；`story` 故事线；`herworld` 身边的人与目标；`inner` 内心独白；`away` 关机道别与开机补课；`output` 回复的格式与标签；`quality` 不重复自己；`together` / `moments` / `games` 一起做的事；`world` 天气与城市；`report` / `evaluate` 周报和评测 | `Brain` |
| `proactive/` | `scheduler` 定时主动消息、提醒、每日整理；`events` 早安晚安、纪念日等具体事件 | `Scheduler` |
| `chat/` | `companion` 中枢：等他打完字、生成回复、出错重试、睡前故事、聊完后整理记忆；`delivery` 拟人化发送（分条、打字停顿、打错字再补一句“*正确的字”、表情包 → 学来的 QQ 表情包 → QQ 小黄脸 → emoji、说完撤回、推歌附链接）；`commands` + `life_cmds` 各个 /命令；`command` 一条命令（`text` / `args` / `rest` / `reply()`）；`common` 共用的常量（错字表、命令列表、“等我一下”、代词） | `Companion`、`Command` |
| `channels/qq/` | `onebot` OneBot 11 客户端和发送器 `QQOut`；`bridge` 收 QQ 私聊（图片、表情包、戳一戳、撤回、合并转发、分享卡片……），交给 `Companion`，`/命令` 包成 `Command`；`setup` 读 NapCat 配置写 config；`napcat_install` 一键下载配置；`napcat_launcher` 启动和自动登录 | `QQ` |
| `media/` | `links` 读分享的链接；`catalog` 番剧和歌；`photo` 她的照片（用 `llm.image`）；`ffmpeg` 截帧 | `PhotoMaker` |
| `sense/` | `phone` 手机信号：订阅 ntfy 频道，收快捷指令发来的“起床 / 到家 / 充电” | `PhoneSense` |
| `web/` | 小屋（`http://127.0.0.1:8765`，只认本机地址）：`server` 应用和状态接口；`auth` 门卫（本机限定、拒绝跨站调用）；`records` 记忆 / 纪念日 / 习惯等可改的东西；`life` 愿望清单、时间胶囊；`system` 大模型、连接状态、软件更新；`page.html` 页面。放在服务器上时用 SSH 隧道在自己电脑上打开 | `Dashboard` |

## 运行时

`app.run()` 的顺序：读配置 → 密钥挪进 .env → 单实例检查 → 文件日志 → 检查有没有聊天模型 → `Brain` → `Companion`
→ `Scheduler` → `QQ`（NapCat）→ 手机信号 → 小屋 → 等托盘的 `stop` / `restart`（或 Ctrl+C / systemd 的停止）。
收尾时先道别（`mind/away.py`，走 NapCat 的 WebSocket，不行再用 `qq.http_url`），再依次关掉各部分。
退出码 3 表示“别再自动重开”（配置有问题、托盘点了退出）。

谁来负责“退出了再开”：
- Windows：托盘（`scripts/tray.pyw`）用 pythonw 跑 `python run.py`，没有窗口；程序意外退出 10 秒后重开；刚更新过的新版本
  连续两次刚启动就退出，就调用 `core.update.on_quick_exit()` 换回旧版。
- Linux：`start.sh` 循环重开；放在服务器上时用 systemd（`Restart=always`、`RestartPreventExitStatus=3`，例子在 README）。

## 配置

1. `config.example.yaml`：所有设置和默认值，带注释。**加新设置时在这里加默认值**，代码里 `cfg.get(...)` 再给一次兜底。
2. `config.yaml`：用户只写和默认值不一样的。
3. `config.llm.yaml`：程序写的（小屋 / `run.py llm`），只放 `llm.profiles` 和 `llm.routes`。
4. `.env`：密钥。配置里写 `${名字}`，读的时候替换。

大模型不内置任何地址：`llm.profiles` 默认是空的，用途见 `llm/roles.py` 的 `ROLES`。

## 数据

全部在 `data/`：`ailife.db`（聊天、记忆、日记、各种 key-value）、`chars/`（其他角色的库）、`backup/`、`logs/`、`photos/`、`update/`。
数据库表结构的升级写在 `core/db.py` 的 `MIGRATIONS`（只加列、不删列），旧库启动时自动补上。

## 约定

- **导入**：一律写完整路径 `from ailife.mind.memory import Memory`，不用相对导入；包的 `__init__.py` 保持空的。
- **日志**：`logging.getLogger("ailife.<短名>")`（短名沿用旧模块名，比如 `ailife.brain`、`ailife.llm`），面向用户的话写中文。
- **用户看到的文字**：口语、简短；出错时说怎么了、该怎么办（见 [DESIGN.md](DESIGN.md)）。
- **不联网的测试**：大模型用 `tests/fakes.py` 的 `FakeLLM`，QQ 用 `FakeQQ`（发出去的东西记在 `FakeOut` 里），接口用本机起的假服务。`env` fixture（`tests/conftest.py`）给一套完整的假环境。
- **检查**：`python -m pytest`、`ruff check .`，两个都过再提交。

## 加一个功能该改哪里

| 想做的 | 改哪里 |
| --- | --- |
| 加一个命令行命令 | `cli.py` 写一个 `cmd_xxx(args)`，在 `COMMANDS` 里加一行；需要的话在 `scripts/` 加个 .bat |
| 加一个聊天里的 /命令 | `chat/commands.py`（或 `chat/life_cmds.py`）写一个 `async def cmd_xxx(self, c: Command)`，用 `c.reply()` 回话；在 `chat/common.py` 的 `HELP` 加一行；要中文别名就加到 `channels/qq/bridge.py` 的 `ALIASES` |
| 加一种大模型用途 | `llm/roles.py` 的 `ROLES`（和 `FALLBACK`），调用处 `llm.chat(..., role="新用途")`；小屋和命令行会自动列出来 |
| 小屋加一组接口 | 在 `web/` 写一个 `XxxMixin`（带 `xxx_routes(app)`），`Dashboard` 继承它并在 `app()` 里注册；只给电脑用的放进 `system_routes` |
| 她的一种新行为 | `mind/` 里加部件，`Brain.__init__` 挂上；要定时触发的放 `proactive/events.py` |
| 她发消息的方式（分条、表情包、撤回……） | `chat/delivery.py` |
| 新的聊天软件 | `channels/<名字>/`，参考 `channels/qq/bridge.py`：收到的话写进聊天记录、调 `Companion.schedule_reply()`；给一个有 `send_message` / `send_photo` 等方法的发送器（参考 `onebot.py` 的 `QQOut`） |
| 新设置 | `config.example.yaml` 加默认值和注释，README 的“配置详解”里提一句 |

## 发版本

1. 改 `ailife/__init__.py` 的 `__version__`，在 `CHANGELOG.md` 写这一版改了什么。
2. `python -m pytest`、`ruff check .` 都过；`python run.py selftest` 全是 ✅。
3. 推到 GitHub 的 main：用户那边的程序每天检查一次，在小屋里点“更新”就会装上（先备份、能回滚）。
   移走或删掉的代码文件，更新时会被拿掉（同样有备份）。
