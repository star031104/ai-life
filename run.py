"""ai-life 启动入口。

    python run.py            启动（Telegram 聊天 + 主动消息 + 打电话）
    python run.py login      第一次用：登录“她”的 Telegram 账号（打电话用）
    python run.py console    不连 Telegram，在命令行里和她聊（调人设用）
    python run.py bench      测一遍配置里每个大模型接口的速度和能否连通
    python run.py eval       人设回归评测（--judge 让大模型打分，--quick 只跑基础部分）；改完设置后跑一遍，看她变好还是变差
    python run.py pet        打开桌宠窗口（主程序启动时会自动打开，关掉后可以用它再打开）
    python run.py export     打包她（程序 + 记忆 + 设置 + 密钥），用来备份或者换电脑
    python run.py import 包.zip   在新电脑上把包里的记忆和设置导进来
    python run.py selftest   离线自检（不联网、不碰你的数据），更新代码后跑一下
    python run.py setup      第一次用：配置向导（Telegram、大模型 key、代理、选人设）
    python run.py persona    人设管理：list / check / use 文件 / new（问答生成）/ import 角色卡.png
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ailife.config import load_config  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    force=True,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%m-%d %H:%M:%S",
)
for noisy in ("httpx", "httpx2", "openai", "telegram", "telethon", "apscheduler", "pytgcalls", "ntgcalls",
              "aiohttp.access"):                 # 桌宠每 5 秒轮询一次，访问日志会刷屏
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("ailife")


EXIT_NO_RESTART = 3          # start.bat / start.sh 看到这个就不再自动重启


async def main():
    cfg = load_config()
    if not cfg.has_user_config:
        if sys.stdin and sys.stdin.isatty():
            print("还没有 config.yaml：先跑一遍配置向导。")
            from ailife.cli import setup
            setup()
            print("\n配置完成。重新运行一次就会启动。")
        else:
            print("还没有 config.yaml：运行 python run.py setup（或者把 config.example.yaml 复制成 config.yaml 自己填）。")
        sys.exit(EXIT_NO_RESTART)
    from ailife import secrets
    moved = secrets.migrate(cfg.root)              # 明文密钥挪进 .env（第一次启动时）
    if moved:
        log.info("已把 %d 个密钥从 config.yaml 挪进 .env（config.yaml 里现在写的是 ${名字} 引用）", len(moved))
        cfg = load_config()
    if cfg.proxy and not _proxy_alive(cfg.proxy):
        log.warning("连不上代理 %s：Telegram 可能连不上，先把 Clash / 代理软件打开（会一直自动重试）", cfg.proxy)
    from ailife.health import Health, setup_file_logging
    from ailife import secrets
    from ailife.deps import fix_hint, missing, version_line
    from ailife.move import host_running
    if (cfg.get("web") or {}).get("enabled", True) and host_running(cfg):
        print(f"她已经在这台电脑上运行了（另一个 start.bat 窗口还开着？）。同时开两个会抢消息，这个窗口不再启动。\n"
              f"（如果确实没开着：可能是 {int((cfg.get('web') or {}).get('port', 8765))} 端口被别的程序占了，"
              f"在 config.yaml 的 web.port 换一个）")
        sys.exit(EXIT_NO_RESTART)
    logpath = setup_file_logging(cfg.root)
    log.info("%s 启动。日志也会写进 %s", version_line(), logpath)
    for pkg, why, req in missing(cfg):
        (log.error if req else log.warning)("缺少 %s：%s（%s）", pkg, why, fix_hint())
    from ailife.bot import ChatBot
    from ailife.brain import Brain
    from ailife.proactive import Scheduler
    from ailife.voice import ASR, TTS

    brain = Brain(cfg)
    tts, asr = TTS(cfg), ASR(cfg)
    tts.persona = brain.persona                    # 人设里的声音设置（音色、语速、试听的话）
    bot = ChatBot(cfg, brain, tts, asr)
    bot.health = Health(cfg, bot)
    secrets.install_redaction(secrets.secret_values(cfg))      # 日志里万一出现密钥，替换成 ***
    brain.away.check()                             # 关机 / 断开了很久：先记下来，他离线时发的消息回复时就能顺便说
    from ailife.away import install_console_close
    _close_handler = install_console_close(brain.away)     # noqa: F841  点窗口 × / 关机时也道别
    await bot.start()
    bot.health.start()

    async def warm_voice():          # 后台准备本地语音模型（第一次会自动下载：合成约 150MB、识别约 160MB）
        try:
            await tts.local.ensure_model()
            await asyncio.to_thread(tts.local._load)
        except Exception as e:  # noqa: BLE001
            log.warning("本地语音没准备好：%s（会在发语音时再试）", e)
        if asr.local:
            try:
                await asr.local.ensure_model()
                await asyncio.to_thread(asr.local._load)
            except Exception as e:  # noqa: BLE001
                log.warning("本地语音识别没准备好：%s（会先用在线识别）", e)
    voice_task = asyncio.create_task(warm_voice())  # noqa: F841
    if cfg.call.enabled:
        from ailife.caller import Caller
        caller = Caller(cfg, brain, tts, asr, bot)
        try:
            await caller.start()
            bot.caller = caller
        except Exception as e:  # noqa: BLE001
            log.error("打电话模块没启动：%s", e)
    sched = Scheduler(cfg, brain, bot)
    bot.sched = sched
    sched.start()

    from ailife.sense import PCSense, PhoneSense
    bot.pc = PCSense(cfg, brain.db, on_song=brain.media.on_song)
    bot.pc.start()
    bot.phone = PhoneSense(cfg, brain.db, bot.on_phone)
    bot.phone.start()

    from ailife.web import Dashboard
    dash = Dashboard(cfg, brain, bot)
    bot.dash = dash
    if getattr(dash, "pet", None) and dash.pet.enabled:
        bot.pc.on_event = dash.pet.on_pc_event
    try:
        await dash.start()
        log.info("电脑上看小屋：浏览器打开 http://127.0.0.1:%d （或者点托盘里桌宠图标 → 打开小屋）", dash.port)
    except Exception as e:  # noqa: BLE001
        log.error("小屋网页没启动（端口被占用？）：%s", e)

    pet_proc = None
    if (cfg.get("pet") or {}).get("enabled", True) and (cfg.get("pet") or {}).get("autostart", True) \
            and sys.platform == "win32" and dash.runner is not None:
        from ailife.pet import spawn_window
        pet_proc = spawn_window(cfg.root)

    tunnel = None
    if (cfg.get("miniapp") or {}).get("enabled", True) and dash.enabled:
        from ailife.tunnel import Tunnel

        kick = asyncio.Event()

        async def _woke(gap):                      # 睡眠醒来：临时隧道多半已经失效，强制重连
            kick.set()
        sched.on_wake.append(_woke)

        async def open_miniapp():
            nonlocal tunnel
            # 开机自启时网络可能还没好：第一次失败也会一分钟后再试，直到连上
            first, fails = True, 0
            while True:
                try:
                    if first or kick.is_set() or tunnel is None or not (
                            tunnel.fixed_url or (tunnel.proc and tunnel.proc.returncode is None)):
                        if not first:
                            log.info("手机小屋的隧道%s，重新连接…", "要刷新（刚醒来）" if kick.is_set() else "断了")
                        kick.clear()
                        if dash.public_runner is None:
                            await dash.start_public()
                        if tunnel is None:
                            tunnel = Tunnel(cfg, dash.public_port)
                        else:
                            await tunnel.stop()
                        url = await tunnel.start()
                        if url:
                            if url != dash.public_url:
                                dash.public_url = url
                                await bot.setup_miniapp(url)
                            fails = 0
                        first = False
                except Exception as e:  # noqa: BLE001
                    fails += 1
                    first = False
                    if fails in (1, 10) or fails % 60 == 0:
                        log.warning("手机小屋（Mini App）没开起来：%s（会自动重试）", e)
                try:
                    await asyncio.wait_for(kick.wait(), 60)
                except asyncio.TimeoutError:
                    pass
        miniapp_task = asyncio.create_task(open_miniapp())  # noqa: F841  （留着引用，免得被回收）
    from ailife.health import keep_awake
    if cfg.get("keep_awake", True) and keep_awake(True):
        log.info("运行期间不会让电脑自动睡眠（屏幕照常熄灭；合盖或手动睡眠还是会睡）。不想要就在 config.yaml 里写 keep_awake: false")
    log.info("%s 上线了。按 Ctrl+C 退出。", brain.persona.name)
    if not bot.owner_id:
        log.info("提示：用你自己的 TG 给 bot 发 /start 完成绑定。")
    restart = False
    leaving = False
    try:
        await bot.restart_event.wait()             # 换人设后需要重启：结束这一轮，start.bat / start.sh 会自动再开
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
        await bot.pc.stop()
        await bot.phone.stop()
        if tunnel:
            await tunnel.stop()
        if pet_proc and pet_proc.poll() is None:
            pet_proc.terminate()
        await dash.stop()
        await bot.stop()
        keep_awake(False)
    return "restart" if restart else None


def _proxy_alive(url: str) -> bool:
    import socket
    from urllib.parse import urlparse
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 7890), timeout=2):
            return True
    except OSError:
        return False


def pet():
    """只打开桌宠窗口（主程序要先在运行）。"""
    from ailife.pet import run_window
    run_window(load_config(), auto="--auto" in sys.argv)


async def login():
    cfg = load_config()
    from telethon import TelegramClient
    from ailife.caller import telethon_proxy
    c = cfg.call
    if not c.api_id or not c.api_hash:
        print("先在 config.yaml 的 call 部分填好 api_id 和 api_hash。")
        return
    client = TelegramClient(str(cfg.root / c.session_name), int(c.api_id), c.api_hash,
                            proxy=telethon_proxy(cfg.proxy))
    await client.start()          # 按提示输入她的手机号、验证码（和两步验证密码）
    me = await client.get_me()
    print(f"登录成功：{me.first_name}（id {me.id}）。现在用你自己的号给这个账号发一句话，再启动 run.py。")
    await client.disconnect()


async def console():
    cfg = load_config()
    from ailife.brain import Brain
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
            tag = "🔊" if b.voice else ""
            line = f"[贴纸:{b.sticker}]" if b.sticker else b.text
            print(f"{brain.persona.name}：{tag}{line}")
            brain.db.add_message("assistant", line)
        if p.call:
            print("（她想给你打电话）")
        await brain.memory.maybe_extract()


async def bench():
    import time
    cfg = load_config()
    from ailife.llm import LLM
    llm = LLM(cfg)
    msgs = [{"role": "system", "content": "你是一个说话自然的女生，用口语回复，两三句话。"},
            {"role": "user", "content": "今天好累啊，下班路上还下雨了。"}]
    print(f"{'接口':<14}{'首字延迟':>8}{'总用时':>8}{'字/秒':>7}  回复")
    for name, p in llm.profiles.items():
        t0 = time.time()
        first = None
        text = ""
        try:
            resp = await p.client.chat.completions.create(
                messages=msgs, stream=True, **llm._kwargs(p, 0.8, 200))
            async for ch in resp:
                d = ch.choices[0].delta.content if ch.choices else None
                if d:
                    first = first or time.time()
                    text += d
            total = time.time() - t0
            from ailife.llm import strip_think
            text = strip_think(text).replace("\n", " ")
            speed = len(text) / max(0.01, total - (first - t0)) if first else 0
            print(f"{name:<14}{(first - t0) if first else 0:>7.2f}s{total:>7.2f}s{speed:>7.0f}  {text[:40]}")
        except Exception as e:  # noqa: BLE001
            print(f"{name:<14}  失败：{str(e)[:120]}")
    print("通话（call）建议把首字延迟最低、又稳定的放第一位。")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = args[0] if args else "run"
    if mode == "persona":                          # persona 后面的参数原样交给它
        sys.argv = [sys.argv[0], "persona"] + sys.argv[sys.argv.index("persona") + 1:]
    if mode == "pet":
        pet()
        sys.exit(0)
    if mode == "setup":
        from ailife.cli import setup
        setup()
        sys.exit(0)
    if mode == "persona":
        from ailife.cli import persona_main
        persona_main(sys.argv[2:])
        sys.exit(0)
    if mode == "selftest":
        from ailife.selftest import run as selftest_run
        logging.getLogger().setLevel(logging.WARNING)
        sys.exit(0 if selftest_run(load_config()) else 1)
    if mode in ("export", "import"):
        from ailife import move
        try:
            if mode == "export":
                move.export(load_config())
            elif len(sys.argv) < 3:
                print("用法：python run.py import 包.zip")
                sys.exit(1)
            else:
                move.import_(load_config(), sys.argv[2])
        except RuntimeError as e:
            print(f"没做成：{e}")
            sys.exit(1)
        sys.exit(0)
    if mode == "eval":
        from ailife.evaluate import main as eval_main
        eval_main(load_config(), judge="--judge" in sys.argv, quick="--quick" in sys.argv)
        sys.exit(0)
    fn = {"run": main, "login": login, "console": console, "bench": bench}.get(mode)
    if not fn:
        print(__doc__)
        sys.exit(1)
    try:
        res = asyncio.run(fn())
    except KeyboardInterrupt:
        res = None
    if res == "restart" and not os.environ.get("AILIFE_LAUNCHER"):
        # 不是用 start.bat / start.sh 启动的（没人负责重开）：自己重新启动一遍
        os.execv(sys.executable, [sys.executable] + sys.argv)
