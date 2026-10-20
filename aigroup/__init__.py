"""aigroup：住在群里的 AI 朋友——QQ 群（NapCat）和微信群（读本地库收、窗口操作发）是同一套代码。

分层（上层可以用下层，下层不知道上层）：
    cli.py · app.py · wizard.py      入口：命令行、运行时装配、微信的配置向导（QQ 的在 qq/wizard.py）
    web/                             小屋（网页）
    chat/                            群聊主流程（GroupChat）
    memory/                          她记得的东西（群友、整理、以前的事、学到的、梗、表情包）
    net/                             上网：读链接、搜一搜、看 B 站视频、群友在哪和天气、画照片
    brain.py · persona.py            人设 + 大模型 + 数据库 + 她自己的一天
    llm/ · qq/ · wechat/             对外的接口：大模型、QQ（OneBot / NapCat）、微信
    core/                            配置（渠道、她的家）、密钥、存储、时间工具
    eval/                            回放、评审、每周评测
渠道看 config.yaml 的 channel: qq / wechat（python run.py qq / wechat 会帮你选好）。
"""
__version__ = "6.0.0"            # 13.0：群聊 6.0（12.0 起和有希合成一个仓库 ai-life）
