"""ai-life 启动入口（12.0 起有希、QQ 群、微信群在一个仓库里）。

    python run.py yuki   [命令]     有希（QQ 私聊陪伴）        她的家：home/yuki
    python run.py qq     [命令]     QQ 群里的她（NapCat）       她的家：home/qq
    python run.py wechat [命令]     微信群里的她                她的家：home/wechat
    python run.py yuki help         看这个角色能用的所有命令（不带命令 = 启动她）

“她的家”放她自己的东西：config.yaml、.env（密钥）、data/（数据库、日志、备份）、你自己写的人设。
想放到别处：加 --home 目录，或者设环境变量 AILIFE_HOME（有希）/ AIGROUP_HOME（群聊）。
老的用法（直接 python run.py [命令]，配置放在仓库根目录）照样能用：等于 python run.py yuki。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parent
TARGETS = {"yuki": "有希（QQ 私聊）", "qq": "QQ 群", "wechat": "微信群"}
ALIASES = {"ailife": "yuki", "group": "qq", "wx": "wechat", "weixin": "wechat"}


def home_of(target: str, override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get("AILIFE_HOME" if target == "yuki" else "AIGROUP_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if target == "yuki" and (CODE / "config.yaml").exists() and not (CODE / "home" / "yuki").exists():
        return CODE                                   # 老版本：有希的配置和数据就放在仓库根目录
    return CODE / "home" / target


def main(argv: list[str]) -> int:
    argv = list(argv)
    override = None
    if "--home" in argv:
        i = argv.index("--home")
        if i + 1 >= len(argv):
            print("--home 后面要写目录")
            return 2
        override = argv[i + 1]
        del argv[i:i + 2]
    target = ALIASES.get(argv[0], argv[0]) if argv else ""
    if target in TARGETS:
        argv = argv[1:]
    else:
        target = "yuki"                               # 老用法：python run.py [命令] 就是有希
    home = home_of(target, override)
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(CODE))
    if target == "yuki":
        os.environ["AILIFE_HOME"] = str(home)
        from ailife.cli import main as cli
    else:
        os.environ["AIGROUP_HOME"] = str(home)
        os.environ["AIGROUP_CHANNEL"] = target
        from aigroup.cli import main as cli
    return int(cli(argv) or 0)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help", "targets"):
        print(__doc__)
        sys.exit(0)
    sys.exit(main(sys.argv[1:]))
