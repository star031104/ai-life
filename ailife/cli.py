"""命令行入口：`python run.py <命令>`（或 `python -m ailife <命令>`）。不带命令就是启动她。

加一个命令：在 COMMANDS 里加一行（名字、说明、处理函数），帮助信息自动生成。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Callable

from ailife.app import EXIT_NO_RESTART

NOISY = ("httpx", "httpx2", "openai", "telegram", "apscheduler", "aiohttp.access")


def setup_logging():
    logging.basicConfig(level=logging.INFO, force=True, format="%(asctime)s %(levelname)s %(name)s | %(message)s",
                        datefmt="%m-%d %H:%M:%S")
    for n in NOISY:                              # 桌宠每 5 秒轮询一次，访问日志会刷屏
        logging.getLogger(n).setLevel(logging.WARNING)


def _cfg():
    from ailife.core.config import load_config
    return load_config()


def _quiet():
    logging.getLogger().setLevel(logging.WARNING)


# ---------------- 各个命令 ----------------
def cmd_run(args: list[str]) -> int:
    from ailife.app import run
    try:
        res = asyncio.run(run())
    except KeyboardInterrupt:
        res = None
    if res == "stop":                            # 托盘里点了退出：别再自动重开
        return EXIT_NO_RESTART
    if res == "restart" and not os.environ.get("AILIFE_LAUNCHER"):
        os.execv(sys.executable, [sys.executable] + sys.argv)   # 没有托盘 / start.bat 负责重开：自己重开
    return 0


def cmd_console(args):
    from ailife.app import console
    asyncio.run(console())
    return 0


def cmd_setup(args):
    from ailife.wizard import setup
    setup()
    return 0


def cmd_llm(args):
    _quiet()
    from ailife.llm.cli import main
    return main(args)


def cmd_bench(args):
    from ailife.llm.cli import bench
    asyncio.run(bench())
    return 0


def cmd_qqsetup(args):
    _quiet()
    if "--manual" in args:                       # 只读现成的 NapCat 配置，不下载、不启动
        from ailife.channels.qq.setup import main
        return main(args)
    from ailife.channels.qq.napcat_install import main
    return main(args)


def cmd_persona(args):
    from ailife.wizard import persona_main
    persona_main(args)
    return 0


def cmd_pet(args):
    from ailife.pet.window import run_window
    run_window(_cfg(), auto="--auto" in args)
    return 0


def cmd_selftest(args):
    _quiet()
    from ailife.core.selftest import run
    return 0 if run(_cfg()) else 1


def cmd_eval(args):
    from ailife.mind.evaluate import main
    main(_cfg(), judge="--judge" in args, quick="--quick" in args)
    return 0


def cmd_export(args):
    from ailife.core import backup
    try:
        backup.export(_cfg())
    except RuntimeError as e:
        print(f"没做成：{e}")
        return 1
    return 0


def cmd_import(args):
    from ailife.core import backup
    files = [a for a in args if not a.startswith("--")]
    if not files:
        print("用法：python run.py import 包.zip")
        return 1
    try:
        backup.import_(_cfg(), files[0])
    except RuntimeError as e:
        print(f"没做成：{e}")
        return 1
    return 0


def cmd_update(args):
    from ailife.core.update import main
    return main(args)


COMMANDS: dict[str, tuple[str, Callable[[list[str]], int]]] = {
    "run": ("启动她（Telegram / QQ 聊天、主动消息、小屋、桌宠）", cmd_run),
    "setup": ("第一次用：配置向导（聊天软件、代理、人设、加一个大模型）", cmd_setup),
    "llm": ("管理大模型：填接口地址和 Key → 测连通 → 选模型 → 试一下 → 选用途", cmd_llm),
    "bench": ("测一遍每个大模型的首字延迟和速度", cmd_bench),
    "qqsetup": ("QQ 一键配置：自动下载、配好 NapCat，扫码登录一次（--manual 只读现成的配置）", cmd_qqsetup),
    "persona": ("人设管理：list / check / use 文件 / new / import 角色卡.png", cmd_persona),
    "console": ("不连聊天软件，在命令行里和她聊（调人设用）", cmd_console),
    "pet": ("打开桌宠窗口（主程序要在运行）", cmd_pet),
    "selftest": ("离线自检（不联网、不碰你的数据），更新代码后跑一下", cmd_selftest),
    "eval": ("人设回归评测（--judge 让大模型打分，--quick 只跑基础部分）", cmd_eval),
    "export": ("打包她（程序 + 记忆 + 设置 + 密钥），备份或者换电脑", cmd_export),
    "import": ("在新电脑上把包里的记忆和设置导进来：import 包.zip", cmd_import),
    "update": ("检查 / 安装更新：update [apply|rollback]", cmd_update),
}
ALIASES = {"napcat": "qqsetup"}


def usage() -> str:
    lines = ["用法：python run.py [命令]（不写命令＝run）", ""]
    lines += [f"  {name:<10}{desc}" for name, (desc, _) in COMMANDS.items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    setup_logging()
    name = argv[0] if argv and not argv[0].startswith("-") else "run"
    rest = argv[1:] if argv and not argv[0].startswith("-") else argv
    name = ALIASES.get(name, name)
    if name in ("help", "-h", "--help") or name not in COMMANDS:
        print(usage())
        return 0 if name in ("help", "-h", "--help") else 1
    return COMMANDS[name][1](rest) or 0
