# 更新记录

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
