<div align="center">

# ai-life

**让 AI 像一个人那样待在你身边：有自己的性格和日子，懂分寸，记得每一个人**

QQ 私聊里陪你的有希 · 混在 QQ 群、微信群里的她

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-555)](#放在服务器上24-小时在线)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[像人的四件事](#像人的四件事) · [人格怎么刻画](#她的人格是怎么刻画的) · [怎么理解每个人](#她怎么理解每一个人) · [安装](#windows-上装和启动) · [服务器](#放在服务器上24-小时在线) · [更新记录](CHANGELOG.md)

</div>

---

大多数聊天机器人是“被 @ 了就回一句”的助手。这个项目想做的是另一件事：**一个像人的群友 / 朋友**。
不只是说话像人，还包括什么时候开口、什么时候闭嘴、怎么对不同的人、被逗了怎么办、记不记得你上周说过的事、会不会慢慢变得更懂你。

## 像人的四件事

| | 做了什么 | 详细 |
| --- | --- | --- |
| **有自己的人格** | 不用一串形容词，而是用心理学的人格模型写：特质剖面（大五 30 facet）、“如果……就……”的情境签名、价值排序、幽默分寸、依恋风格；每一轮按当下的情境提醒她“你一贯是这样的” | [人格刻画](docs/人格刻画.md) |
| **懂每一个人** | 用同一套办法给每个群友建画像：性格、说话风格、他在什么情况下怎么反应、他在群里图什么、别碰的话题、记得的事；回话前先判断对方这句想要什么 | [人格刻画 · 第 5 节](docs/人格刻画.md#5-她怎么理解每一个人) |
| **懂分寸、会看时机** | 不是每条都接：先估计这时候插话自不自然，拿不准再问一个便宜模型；有人连着逗她会越回越短；没人接就安静；深夜不主动开口；每天主动开口有预算；按群友自己的回复节奏打字、分条发 | [群聊开发说明](docs/群聊开发说明.md) |
| **会慢慢长大** | 记住群里的梗和说法、约好的事到点提醒；每周回想“这一周的我”、每月整理“我是怎样的人”（要有证据）；性格要改得你点头；每周自动评测她有没有走样 | [人格刻画 · 第 6、7 节](docs/人格刻画.md#6-成长与审核) |

## 她的人格是怎么刻画的

形容词不是人格。“嘴贫、傲娇、心软”这种写法，模型会执行过头（被逗时回个不停、毒舌变刻薄），长对话里还会被稀释。所以人格拆成六层：

| 层 | 写什么 | 依据 | 谁能改 |
| --- | --- | --- | --- |
| 核心身份 | 一两句：她是谁、和这个群什么关系 | — | 你 |
| 特质剖面 | 只写和她有关的十来个 facet，每个 1~5 档 + 一句说明 | 大五 / IPIP-NEO | 你（她只能提建议） |
| 情境签名 | 什么时候 → 她会怎样 → 她不会怎样 | Mischel & Shoda 的 CAPS | 你 |
| 价值与动机 | 冲突时的裁决顺序；想要的、真正需要的、毛病、矛盾；被冷落时怎么做；四种幽默的配比 | Schwartz、依恋理论、Martin 幽默风格 | 你 |
| 声音 | 句长、口头禅配额、绝不说的话、按情境挑的对话示例 | — | 你 |
| 叙事身份 | 这一阵在意什么、怎么应对、一个关键场景、故事往哪走 | McAdams | 她自己写，要有证据 |

写在人设旁边的一个 YAML 里（示例 [`persona/traits.example.yaml`](persona/traits.example.yaml)）：

```yaml
facets:
  A2: {level: 4, note: 说话直，不懂就说不懂}          # 坦率高
  A4: {level: 2, note: 被怼先呛回一句，但真错了就认}   # 顺从低
  A6: {level: 4, note: 见人真难过立刻软下来}          # 温柔高 → 嘴上不让人、心里软
humor:
  mix: {亲和: 50, 调侃: 30, 自嘲: 20, 攻击: 0}
signatures:
  - when: 同一个人连着逗你、让你复述骂人的话
    flags: [pestered]
    do: 回一次就够，后面越回越短，或者不回
    not: 跟着骂别人；同一个问题追问第二遍
```

运行时，稳定的部分放在提示词最前面；程序认出当下的情境（被叫到、被连着逗、没人接、被夸、被损、有人难过、问推荐、被纠正、深夜……），
挑 1~3 条签名和 1~2 段对话示例放在**提示词最后、离生成最近的地方**——这是针对“人设在长对话里会漂移、越聊越像对方”（Persona Drift）的做法。

每周一，评测模型**不看人设**、只读她这一周说的话，给每个 facet 打分，和人格文件里的目标比：平均差几档、哪一项跑偏了、四种幽默各说了几句、有没有真伤人的话。

## 她怎么理解每一个人

| 层 | 画像字段 |
| --- | --- |
| 特质 | 性格、说话风格、口头禅 |
| 情境签名 | 他的反应：“被损时……；累了时……；兴奋时……” |
| 动机 | 在意：他在群里图什么 |
| 看法 | 看法、兴趣 |
| 当下和故事 | 近况（带日期）、记得的事 |
| 关系 | 称呼、群里关系、和她、怎么聊、别碰 |

- **进群前先认识大家**：把以前的聊天记录整理成画像草稿，你看过、删掉不想让她知道的再灌给她；真正说上话以前，她不会把听来的私事说出来。
- **每晚更新**：只写有证据的；别人损他的话不当成他的性格；画像有历史版本，能退回。
- **回话前读心思**：先判断这句是逗她、认真问、求安慰、分享、吐槽还是随口一说，再决定怎么接。
- **分寸**：熟悉度分三档，刚认识的人先客气；每个人的“别碰”在发之前会被拦下；私下说的、别的群听到的不外传；入库前脱敏，可以让她“忘掉某人”。

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

每个角色在任务栏右下角一个小图标（可能藏在 `^` 里）：粉色 **L** = 有希，绿色 **G** = QQ 群，微信绿 **W** = 微信群。双击打开她的小屋（每一页、每个开关的说明见 [docs/小屋.md](docs/小屋.md)）；右键：状态、看日志、体检、换大模型、重启、开机自动启动、退出。程序意外退出 10 秒后自动重开；同一个角色只会跑一份。

**3. 开机自动启动**：右键图标 → “开机自动启动”（只管这一个），或者双击 `scripts\autostart_on.bat`（全部）。关掉：再点一次，或 `scripts\autostart_off.bat`。

**4. 群里的她：进群前先“认识大家”**（可选，推荐）

她刚进群时谁也不认识。双击 `scripts\onboard.bat`（默认是微信群；QQ 群在命令行里运行 `scripts\onboard.bat qq`），一步步来：

1. **导出群里以前的聊天记录**（只读）：微信从电脑上开着的某个号读——一般选你自己的大号，它在群里待得最久；QQ 让她的 NapCat 往回翻每个群最近的消息。导出的文本在 `home\<名字>\data\export\`。
2. **用你配的大模型整理成一份草稿** `home\<名字>\data\seed_draft.yaml`：每个群在聊什么、不成文的规矩、群梗，每个常说话的人的画像（大家怎么叫他、性格、说话风格、兴趣、近况、别碰的话题）；群友之间几乎不叫名字的群，顺手把“叫人名字”关掉。不记电话、住址、证件号。
3. **你看一遍、改一改**（删掉不想让她知道的），回车就灌给她，重启她就生效。

灌进去的人算“事先听说的”：真正和她说上几句话以前，她不会把听来的近况、私事说出来。三步也能分开跑：`wxexport` / `qqexport` → `seedgen` → `seed`。

**5. 平时**

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
| `onboard` |  | ✓ | ✓ | 进群前的准备：导出聊天记录 → 整理成草稿 → 你看过 → 灌给她 |
| `wxexport` · `qqexport` |  | ✓（qq） | ✓（wx） | 导出群里以前的聊天记录（只读） |
| `seedgen` |  | ✓ | ✓ | 把导出的记录整理成 `data/seed_draft.yaml` |
| `seed` |  | ✓ | ✓ | 把草稿（或你自己写的）灌给她 |
| `qqpass` |  | ✓ |  | 存小号密码（少扫几次码） |
| `wxdiag` · `wxkeys` · `wxtail` · `wxsend` |  |  | ✓ | 微信接入：体检、拿密钥、看收到的消息、发一句试试、导出聊天 |

想把某个家放到别处：`python run.py qq --home D:\bots\qq`，或者设环境变量 `AIGROUP_HOME`（群聊）/ `AILIFE_HOME`（有希）。

## 更多

- **小屋每一页、每个开关是干什么的**：[docs/小屋.md](docs/小屋.md)（第一次打开先看这个）
- **人格刻画（她的人格怎么写、她怎么理解每个人、怎么检查有没有走样）**：[docs/人格刻画.md](docs/人格刻画.md)
- 写人设：[docs/PERSONA.md](docs/PERSONA.md) · 有希的全部功能：[docs/FEATURES.md](docs/FEATURES.md)
- 代码结构：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)（有希）· [docs/群聊开发说明.md](docs/群聊开发说明.md)（群聊）
- 跑测试：`python -m pytest tests/yuki`、`python -m pytest tests/group`（微信群）、`python -m pytest tests/group_qq`（QQ 群）——三组要分开跑
- 只有发给大模型的那部分内容会经过对应的服务商；程序里不内置任何接口和 Key。
- 致谢：QQ 接入靠 [NapCat](https://github.com/NapNeko/NapCatQQ)（OneBot 11）；设计风格参考了 [Wakuwaku](https://github.com/zhoupengjie/Wakuwaku)（MIT）；微信读库的思路见 [NOTICE](NOTICE)。

MIT License
