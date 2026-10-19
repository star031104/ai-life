# 代码结构

这份文档写给要改代码的人：每个包管什么、谁依赖谁、加一个功能该动哪里。用的人看 [README](../README.md)。

## 分层

```
            cli.py ──► app.py（把下面的东西装起来、启动、收尾）
                         │
   ┌──────────┬──────────┼───────────┬──────────┬─────────┐
channels/   proactive/   web/        pet/       sense/     （对外：聊天软件、定时、网页、桌宠、感知）
   └──────────┴────┬─────┴───────────┴──────────┴─────────┘
                 mind/（她的“脑子”：Brain 和它的各个部件）
             ┌─────┴──────┬───────────┐
          persona/      media/       llm/        （人设、链接 / 照片 / ffmpeg、大模型）
             └────────────┴─────┬─────┘
                              core/              （配置、密钥、数据库、日志、自检、备份、更新）
```

依赖只往下走：`core` 不 import 上面任何包；`llm`、`persona`、`media` 只用 `core`；`mind` 用它们；
对外的几个包用 `mind`。同一层之间尽量不互相 import（少数例外是函数里的延迟 import，比如 Telegram 的命令要调
`proactive` 的提醒）。

## 每个包

| 包 | 管什么 | 主要的类 / 入口 |
| --- | --- | --- |
| `ailife/cli.py` | 命令行：`python run.py <命令>`，命令表 `COMMANDS` | `main()` |
| `ailife/app.py` | 启动顺序、托盘通信（`data/tray_cmd`）、收尾道别 | `run()`、`console()` |
| `ailife/wizard.py` | 第一次的配置向导、人设命令 | `setup()` |
| `core/` | `config`（读 config.example.yaml → config.yaml → config.llm.yaml，`${名字}` 从 .env 取）、`secrets`（密钥进 .env、日志打码）、`db`（SQLite）、`health`（日志文件、/diag、开机自启）、`selftest`、`backup`（打包 / 导入）、`update`（自动更新与回滚）、`timeutil`、`deps` | `load_config()`、`DB` |
| `llm/` | 大模型。`roles` 用途和兜底规则；`client` 聊天 / 看图（按路由兜底、冷却、统计）；`store` 你加的模型（config.llm.yaml）；`probe` 测连通、列模型、试一下；`embed` 向量；`image` 画图；`cli` 命令行管理 | `LLM`、`store.add_model()` |
| `persona/` | 人设文件的格式（`loader`）、角色库（新建 / 导入角色卡 / 切换，`library`）、对话示例挑选 | `load_persona()` |
| `mind/` | `brain` 拼提示词、生成回复；`memory` + `retrieval` 记忆与检索；`emotion` 情绪；`rhythm` 作息与校历；`story` 故事线；`herworld` 身边的人与目标；`inner` 内心独白；`away` 关机道别与开机补课；`output` 回复的格式与标签；`quality` 不重复自己；`together` / `moments` / `games` 一起做的事；`world` 天气与城市；`report` / `evaluate` 周报和评测 | `Brain` |
| `proactive/` | `scheduler` 定时主动消息、提醒、每日整理；`events` 早安晚安、纪念日等具体事件 | `Scheduler` |
| `channels/telegram/` | `bot` 收发和分条发送；`commands` 命令；`delivery` 发送细节；`media` 图片 / 动图 / 文件；`life` 生活类命令；`common` 共用的常量 | `ChatBot` |
| `channels/qq/` | `onebot` OneBot v11 客户端；`bridge` 把 QQ 私聊接到同一个大脑；`setup` 读 NapCat 配置写 config；`napcat_install` 一键下载配置；`napcat_launcher` 启动和自动登录 | `QQ` |
| `media/` | `links` 读分享的链接；`catalog` 番剧和歌；`photo` 她的照片（用 `llm.image`）；`ffmpeg` 截帧 | `PhotoMaker` |
| `sense/` | 电脑感知（前台窗口、正在播放）、手机快捷指令 | `PCSense`、`PhoneSense` |
| `web/` | 小屋：`server` 应用和状态接口；`auth` 门卫（本机限定、Telegram 签名）；`records` 记忆 / 纪念日 / 习惯等可改的东西；`life` 愿望清单、时间胶囊；`system` 大模型、连接状态、软件更新（只在本机入口）；`tunnel` 手机 Mini App 隧道；`page.html` 页面 | `Dashboard` |
| `pet/` | `server` 桌宠的页面、接口（聊天、轮询、提醒方式、像素宠物）；`window` PySide6 窗口（全屏让开、鼠标位置、散步）；`sprite` 从 codex-pets.net 下载像素宠物；`page.html` 渲染（Live2D / 精灵图） | `Pet`、`run_window()` |

## 运行时

`app.run()` 的顺序：读配置 → 密钥挪进 .env → 单实例检查 → 文件日志 → 检查有没有聊天模型 → `Brain` → `ChatBot`
（Telegram）→ `Scheduler` → `QQ` → 感知 → 小屋（含桌宠接口）→ 隧道 → 桌宠窗口进程 → 等托盘的 `stop` / `restart`。
退出码 3 表示“别再自动重开”（配置有问题、托盘点了退出）。

托盘（`scripts/tray.pyw`）用 pythonw 跑 `python run.py`，没有窗口；程序意外退出 10 秒后重开；刚更新过的新版本
连续两次刚启动就退出，就调用 `core.update.on_quick_exit()` 换回旧版。

## 配置

1. `config.example.yaml`：所有设置和默认值，带注释。**加新设置时在这里加默认值**，代码里 `cfg.get(...)` 再给一次兜底。
2. `config.yaml`：用户只写和默认值不一样的。
3. `config.llm.yaml`：程序写的（小屋 / `run.py llm`），只放 `llm.profiles` 和 `llm.routes`。
4. `.env`：密钥。配置里写 `${名字}`，读的时候替换。

大模型不内置任何地址：`llm.profiles` 默认是空的，用途见 `llm/roles.py` 的 `ROLES`。

## 数据

全部在 `data/`：`ailife.db`（聊天、记忆、日记、各种 key-value）、`backup/`、`logs/`、`photos/`、`pet/`、`update/`。
数据库表结构的升级写在 `core/db.py` 的 `MIGRATIONS`（只加列、不删列），旧库启动时自动补上。

## 约定

- **导入**：一律写完整路径 `from ailife.mind.memory import Memory`，不用相对导入；包的 `__init__.py` 保持空的。
- **日志**：`logging.getLogger("ailife.<短名>")`（短名沿用旧模块名，比如 `ailife.brain`、`ailife.llm`），面向用户的话写中文。
- **用户看到的文字**：口语、简短；出错时说怎么了、该怎么办（见 [DESIGN.md](DESIGN.md)）。
- **不联网的测试**：大模型用 `tests/fakes.py` 的 `FakeLLM`，Telegram 用 `FakeTGBot`，接口用本机起的假服务。`env` fixture（`tests/conftest.py`）给一套完整的假环境。
- **检查**：`python -m pytest`、`ruff check .`，两个都过再提交。

## 加一个功能该改哪里

| 想做的 | 改哪里 |
| --- | --- |
| 加一个命令行命令 | `cli.py` 写一个 `cmd_xxx(args)`，在 `COMMANDS` 里加一行；需要的话在 `scripts/` 加个 .bat |
| 加一个 Telegram / QQ 命令 | `channels/telegram/commands.py`（QQ 私聊的 `/命令` 会转给同一套） |
| 加一种大模型用途 | `llm/roles.py` 的 `ROLES`（和 `FALLBACK`），调用处 `llm.chat(..., role="新用途")`；小屋和命令行会自动列出来 |
| 小屋加一组接口 | 在 `web/` 写一个 `XxxMixin`（带 `xxx_routes(app)`），`Dashboard` 继承它并在 `app()` 里注册；只给电脑用的放进 `system_routes` |
| 她的一种新行为 | `mind/` 里加部件，`Brain.__init__` 挂上；要定时触发的放 `proactive/events.py` |
| 新的聊天软件 | `channels/<名字>/`，参考 `channels/qq/bridge.py`：收消息交给 `bot` 的同一套处理，发消息实现 `send_text` 等 |
| 桌宠的新动作 / 提醒方式 | `pet/page.html` 的 `Pet` 接口（`think / said / wait / oops / tap / rest / back`），后端开关放 `pet/server.py` 的 `prefs()` |
| 新设置 | `config.example.yaml` 加默认值和注释，README 的“配置详解”里提一句 |

## 发版本

1. 改 `ailife/__init__.py` 的 `__version__`，在 `CHANGELOG.md` 写这一版改了什么。
2. `python -m pytest`、`ruff check .` 都过；`python run.py selftest` 全是 ✅。
3. 推到 GitHub 的 main：用户那边的程序每天检查一次，在小屋里点“更新”就会装上（先备份、能回滚）。
   移走或删掉的代码文件，更新时会被拿掉（同样有备份）。
