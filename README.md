<div align="center">

# ai-life

**有自己生活的 AI 伴侣：QQ 私聊里陪你的有希，和混在 QQ 群、微信群里的她**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-555)](#放在服务器上24-小时在线)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[三个角色](#一个仓库三个角色) · [Windows 上装和启动](#windows-上装和启动) · [服务器](#放在服务器上24-小时在线) · [命令](#常用命令) · [更新记录](CHANGELOG.md)

</div>

---

## 一个仓库，三个角色

代码只有一份，三个角色各有自己的“家”（配置、密钥、数据、你自己写的人设），可以只用一个，也可以三个一起开。QQ 群和微信群的她都可以同时待在好几个群里：小屋里每个群有自己的卡片、聊天记录、群友、学到的说法，开关也能每个群单独调。

| 名字 | 是谁 | 在哪聊 | 她的家 | 小屋（本机网页） | 详细说明 |
| --- | --- | --- | --- | --- | --- |
| `yuki` | 有希：一对一的陪伴，有记忆、有情绪、有自己的生活，会主动找你 | QQ 私聊（NapCat） | `home/yuki/` | http://127.0.0.1:8765 | [docs/YUKI.md](docs/YUKI.md) |
| `qq` | 群里的她：会看气氛接话、记得每个群友、会玩梗 | QQ 群（NapCat） | `home/qq/` | http://127.0.0.1:8767 | [docs/GROUP_QQ.md](docs/GROUP_QQ.md) |
| `wechat` | 同一个群聊大脑，接到微信群 | 微信群（Windows 微信 / 服务器上 Docker 里的微信） | `home/wechat/` | http://127.0.0.1:8768 | [docs/GROUP_WECHAT.md](docs/GROUP_WECHAT.md) |

```
ai-life/
├── run.py                 唯一的入口：python run.py <名字> [命令]
├── ailife/                有希的代码
├── aigroup/               群聊的代码（QQ 群和微信群共用，差别只在 aigroup/qq 和 aigroup/wechat）
├── persona/               自带的人设（有希的 linxi / shenyan，群里的 xiaoman）和模板
├── config.example.yaml            有希的全部配置项和说明
├── config.qq.example.yaml         QQ 群的
├── config.wechat.example.yaml     微信群的
├── home/                  ← 她们的家，不进 git
│   ├── yuki/    config.yaml · config.llm.yaml · .env · data/ · napcat/ · persona/（你自己的人设）
│   ├── qq/      同上
│   └── wechat/  同上（没有 napcat）
├── start.bat · start.vbs · install.bat · setup.bat     Windows
├── install.sh · start.sh                               Linux / macOS
├── scripts/               托盘、体检、换大模型、开机自启……（.bat 都可以带名字：llm.bat qq）
├── deploy/                服务器：systemd 服务、小管家（ops.py：看门、备份、日报、首页）
└── docs/                  每个角色的详细说明、写人设、代码结构、测试清单
```


## Windows 上装和启动

**1. 装**（只要做一次）

在 PowerShell 里粘贴这一行（代码下到 `文档\ai-life`，GitHub 慢时自动换加速地址）：

```powershell
irm https://raw.githubusercontent.com/star031104/ai-life/main/get.ps1 | iex
```

或者在 GitHub 上 **Code → Download ZIP**，解压后双击 **`install.bat`**。它会：装 Python 环境和依赖（三个共用一个 `.venv`）→ 问你先配哪一个（1 有希 / 2 QQ 群 / 3 微信群）→ 打开那个的配置向导（QQ 的会自动下载、登录 NapCat；最后加一个大模型接口）。

想再配另一个：双击 **`setup.bat`**，选它（或者命令行 `setup.bat qq`）。

**2. 启动**

| 怎么做 | 效果 |
| --- | --- |
| 双击 **`start.vbs`** | 配好的几个全部启动，**一个黑窗口都没有** |
| 双击 **`start.bat`** | 问你开哪个（1 有希 / 2 QQ 群 / 3 微信群 / 4 全部） |
| `start.bat qq` | 只开这一个（快捷方式的“目标”里也可以这么写） |
| `scripts\start_console.bat wechat` | 在窗口里跑、能看实时输出（排查问题用） |

每个角色在任务栏右下角一个小图标（可能藏在 `^` 里）：粉色 **L** = 有希，绿色 **G** = QQ 群，微信绿 **W** = 微信群。双击打开她的小屋；右键：状态、看日志、体检、换大模型、重启、开机自动启动、退出。程序意外退出 10 秒后自动重开；同一个角色只会跑一份。

**3. 开机自动启动**：右键图标 → “开机自动启动”（只管这一个），或者双击 `scripts\autostart_on.bat`（全部）。关掉：再点一次，或 `scripts\autostart_off.bat`。

**4. 平时**

- 换 / 加大模型：小屋的“大模型”页，或 `scripts\llm.bat qq`（名字换成你要的那个）
- 哪里没配好：`scripts\check.bat qq` / `scripts\check.bat wechat`；微信接入专门的体检：`scripts\wxdiag.bat`
- 有希的软件更新：她的小屋里点“检查更新”（或 `scripts\update.bat`），**会把三个的代码一起更新**，她们的家不会动

## 放在服务器上，24 小时在线

服务器（Ubuntu）上的样子：代码在 `~/ailife/app`，三个家在 `~/ailife/app/home/{yuki,qq,wechat}`，各一个 systemd 服务。

```bash
git clone https://github.com/star031104/ai-life.git ~/ailife/app
cd ~/ailife/app
bash install.sh qq                     # 建 .venv、装依赖，然后跑 qq 的配置向导（--manual 填 NapCat 地址）
python run.py qq check                 # 看看连得上 NapCat 和大模型没有
sudo cp deploy/systemd/ai-life-group.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ai-life-group
journalctl -u ai-life-group -f         # 看日志
```

| 名字 | systemd 服务 | 服务文件 | 用户 |
| --- | --- | --- | --- |
| yuki | `ai-life` | `deploy/systemd/ai-life.service` | ubuntu |
| qq | `ai-life-group` | `deploy/systemd/ai-life-group.service` | ubuntu |
| wechat | `ai-life-wechat` | `deploy/systemd/ai-life-wechat.service` | root（要读 Docker 里微信进程的内存） |

- NapCat / 微信用 Docker 跑，端口只绑 `127.0.0.1`。QQ 的见 [docs/YUKI.md](docs/YUKI.md#放在-linux-服务器上24-小时在线)，微信的见 [docs/WECHAT_LINUX.md](docs/WECHAT_LINUX.md)。
- 小屋只开在服务器本机：用 SSH 隧道看（`ssh -L 8767:127.0.0.1:8767 ubuntu@服务器`），或者按 [deploy/server/README.md](deploy/server/README.md) 配 nginx（带密码）和“小管家”（每 5 分钟看门、每天备份和日报、三个共用的每日花费上限）。
- 换人设、托盘里的“重启”在服务器上就是程序自己退出，systemd 马上重开；退出码 3（配置有问题）不重开，看日志改好后 `sudo systemctl restart ai-life-group`。
- 更新：`cd ~/ailife/app && git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart ai-life ai-life-group ai-life-wechat`。

Linux / macOS 桌面上不用 systemd 也行：`bash start.sh qq`（退出会自动重开，Ctrl+C 停）。

## 常用命令

都是 `python run.py <名字> <命令>`（Windows 上先 `scripts\env.bat` 进环境，或者用对应的 .bat）。不写命令 = 启动她；`help` 看这个角色能用的全部命令。

| 命令 | yuki | qq | wechat | 做什么 |
| --- | :-: | :-: | :-: | --- |
| （不写） | ✓ | ✓ | ✓ | 启动 |
| `setup` | ✓ | ✓ | ✓ | 配置向导（再跑一次只改你想改的） |
| `check` | ✓ | ✓ | ✓ | 体检：连接、大模型、群、人设 |
| `llm` | ✓ | ✓ | ✓ | 加 / 换 / 测大模型接口 |
| `people` |  | ✓ | ✓ | 她心里每个群友的样子 |
| `replay` / `calibrate` |  | ✓ | ✓ | 拿真实聊天回放、按扣分制评她；看质检会不会误伤 |
| `seed` |  | ✓ | ✓ | 导入你提前写好的群友印象 |
| `qqpass` |  | ✓ |  | 存小号密码（少扫几次码） |
| `wxdiag` · `wxkeys` · `wxtail` · `wxsend` · `wxexport` |  |  | ✓ | 微信接入：体检、拿密钥、看收到的消息、发一句试试、导出聊天 |

想把某个家放到别处：`python run.py qq --home D:\bots\qq`，或者设环境变量 `AIGROUP_HOME`（群聊）/ `AILIFE_HOME`（有希）。

## 更多

- 写人设：[docs/PERSONA.md](docs/PERSONA.md) · 有希的全部功能：[docs/FEATURES.md](docs/FEATURES.md)
- 代码结构：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)（有希）· [docs/群聊开发说明.md](docs/群聊开发说明.md)（群聊）
- 跑测试：`python -m pytest tests/yuki`、`python -m pytest tests/group`（微信群）、`python -m pytest tests/group_qq`（QQ 群）——三组要分开跑
- 只有发给大模型的那部分内容会经过对应的服务商；程序里不内置任何接口和 Key。
- 致谢：QQ 接入靠 [NapCat](https://github.com/NapNeko/NapCatQQ)（OneBot 11）；设计风格参考了 [Wakuwaku](https://github.com/zhoupengjie/Wakuwaku)（MIT）；微信读库的思路见 [NOTICE](NOTICE)。

MIT License
