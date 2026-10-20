"""把各个部分装起来跑：大脑、QQ 私聊、主动消息、手机感知、小屋、托盘通信。

    ailife.cli 解析命令后调这里的 run()；console() 是不连聊天软件、在命令行里和她聊（调人设用）。
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time

from ailife.core.config import load_config

log = logging.getLogger("ailife")

EXIT_NO_RESTART = 3          # start.bat / start.sh / 托盘看到这个就不再自动重启


async def run():
    cfg = load_config()
    if not cfg.has_user_config:
        if sys.stdin and sys.stdin.isatty():
            print("还没有 config.yaml：先跑一遍配置向导。")
            from ailife.wizard import setup
            setup()
            print("\n配置完成。重新运行一次就会启动。")
        else:
            print("还没有 config.yaml：运行 python run.py setup（或者把 config.example.yaml 复制成 config.yaml 自己填）。")
        sys.exit(EXIT_NO_RESTART)
    from ailife.core import secrets
    moved = secrets.migrate(cfg.root)              # 明文密钥挪进 .env（第一次启动时）
    if moved:
        log.info("已把 %d 个密钥从 config.yaml 挪进 .env（config.yaml 里现在写的是 ${名字} 引用）", len(moved))
        cfg = load_config()
    if cfg.proxy and not _proxy_alive(cfg.proxy):
        log.warning("连不上代理 %s：走代理的大模型会连不上，先把代理软件打开（会一直自动重试）", cfg.proxy)
    from ailife.core.health import Health, setup_file_logging
    from ailife.core import secrets
    from ailife.core.deps import fix_hint, missing, version_line
    from ailife.core.backup import host_running
    if (cfg.get("web") or {}).get("enabled", True) and host_running(cfg):
        print(f"她已经在这台电脑上运行了（另一个 start.bat 窗口还开着？）。同时开两个会抢消息，这个窗口不再启动。\n"
              f"（如果确实没开着：可能是 {int((cfg.get('web') or {}).get('port', 8765))} 端口被别的程序占了，"
              f"在 config.yaml 的 web.port 换一个）")
        sys.exit(EXIT_NO_RESTART)
    logpath = setup_file_logging(cfg.root)
    log.info("%s 启动。日志也会写进 %s", version_line(), logpath)
    for pkg, why, req in missing(cfg):
        (log.error if req else log.warning)("缺少 %s：%s（%s）", pkg, why, fix_hint())
    if not (cfg.get("llm") or {}).get("routes", {}).get("chat"):
        log.error("还没加聊天模型，她没法说话：运行 python run.py llm（或者双击 scripts\\llm.bat）加一个，"
                  "用途里勾上“聊天”")
        sys.exit(EXIT_NO_RESTART)
    from ailife.chat.companion import Companion
    from ailife.mind.brain import Brain
    from ailife.proactive.scheduler import Scheduler

    brain = Brain(cfg)
    bot = Companion(cfg, brain)
    bot.health = Health(cfg, bot)
    secrets.install_redaction(secrets.secret_values(cfg))      # 日志里万一出现密钥，替换成 ***
    brain.away.check()                             # 关机 / 断开了很久：先记下来，他离线时发的消息回复时就能顺便说
    from ailife.mind.away import install_console_close
    _close_handler = install_console_close(brain.away)     # noqa: F841  点窗口 × / 关机时也道别
    await bot.start()
    bot.health.start()

    sched = Scheduler(cfg, brain, bot)
    bot.sched = sched
    sched.start()

    from ailife.channels.qq.bridge import setup as qq_setup
    qq = qq_setup(cfg, bot)                        # QQ 私聊（config 里 qq.enabled: true 时）
    if not qq:
        log.warning("QQ 没开（config.yaml 的 qq.enabled）：她收不到你的消息")

    from ailife.sense.phone import PhoneSense
    bot.phone = PhoneSense(cfg, brain.db, bot.on_phone)
    bot.phone.start()

    from ailife.core.update import check_loop, confirm_later
    confirm_later(cfg.get("code") or cfg.root)                        # 刚更新过：正常跑够 2 分钟才算更新成功
    update_task = asyncio.create_task(check_loop(cfg, brain.db))  # noqa: F841  每天看一次有没有新版本

    from ailife.web.server import Dashboard
    dash = Dashboard(cfg, brain, bot)
    bot.dash = dash
    try:
        await dash.start()
        log.info("电脑上看小屋：浏览器打开 http://127.0.0.1:%d ", dash.port)
    except Exception as e:  # noqa: BLE001
        log.error("小屋网页没启动（端口被占用？）：%s", e)

    from ailife.core.health import keep_awake
    if cfg.get("keep_awake", True) and keep_awake(True):
        log.info("运行期间不会让电脑自动睡眠（屏幕照常熄灭；合盖或手动睡眠还是会睡）。不想要就在 config.yaml 里写 keep_awake: false")
    log.info("%s 上线了。按 Ctrl+C 退出。", brain.persona.name)
    if not bot.owner_id:
        log.info("提示：config.yaml 的 qq.owner_id 还没填（或者 NapCat 还没登录），她暂时收不到你的消息。")
    restart = False
    leaving = False
    tray_stop = asyncio.Event()

    async def watch_tray():                        # 托盘里点了“重启 / 退出”：它往 data/tray_cmd 里写一个字
        p = cfg.root / "data" / "tray_cmd"
        born = time.time() - 2
        while True:
            await asyncio.sleep(1)
            try:
                cmd = p.read_text(encoding="utf-8").strip() if p.exists() else ""
                if cmd:
                    old = p.stat().st_mtime < born          # 12.0 启动前就留在那的（比如没在跑时别人写的“重启”）：不算
                    p.unlink(missing_ok=True)
                    cmd = "" if old else cmd
            except OSError:
                cmd = ""
            if cmd == "stop":
                tray_stop.set()
                bot.restart_event.set()
            elif cmd == "restart":
                bot.restart_event.set()
    tray_task = asyncio.create_task(watch_tray())  # noqa: F841
    try:
        await bot.restart_event.wait()             # 换人设后需要重启：结束这一轮，start.bat / start.sh 会自动再开
        if tray_stop.is_set():
            log.info("托盘里点了退出")
            leaving = True                         # 她说一声再走
        else:
            log.info("正在重启……")
            restart = True
    except (asyncio.CancelledError, KeyboardInterrupt):
        leaving = True                             # 你按了 Ctrl+C：她说一声再走（出错崩溃重启时不说）
        raise
    finally:
        if leaving:
            brain.away.goodbye_sync()
        await bot.health.stop()
        await sched.stop()
        await bot.phone.stop()
        await dash.stop()
        if qq:
            await qq.stop()
        await bot.stop()
        keep_awake(False)
    return "restart" if restart else "stop" if leaving else None


def _proxy_alive(url: str) -> bool:
    import socket
    from urllib.parse import urlparse
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 7890), timeout=2):
            return True
    except OSError:
        return False


async def console():
    cfg = load_config()
    from ailife.mind.brain import Brain
    brain = Brain(cfg)
    await brain.memory.plan_life(brain.now())
    print(f"和 {brain.persona.name} 聊天（输入 /q 退出，/pro 让{brain.persona.ta}主动找你）")
    while True:
        try:
            text = await asyncio.to_thread(input, "你：")
        except (EOFError, KeyboardInterrupt):
            break
        if text.strip() == "/q":
            break
        if text.strip() == "/pro":
            p = await brain.proactive("你突然想到他了")
        else:
            brain.db.add_message("user", text)
            p = await brain.reply()
        if p.skip:
            print("（她决定先不打扰你）")
        for b in p.bubbles:
            line = f"[贴纸:{b.sticker}]" if b.sticker else b.text
            print(f"{brain.persona.name}：{line}")
            brain.db.add_message("assistant", line)
        await brain.memory.maybe_extract()


