"""健康检查：日志存文件、出错时告诉你、/diag 一键自检、开机自启、睡眠醒来后的恢复。"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

log = logging.getLogger("ailife.health")


# ---------------- 日志 ----------------
def setup_file_logging(root: Path) -> Path:
    """日志写进 data/logs/ailife.log，每个 2MB，留 5 个（满了自动删最旧的）。"""
    d = root / "data" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "ailife.log"
    h = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    h.setLevel(logging.INFO)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s", "%m-%d %H:%M:%S"))
    logging.getLogger().addHandler(h)
    return path


class Recorder(logging.Handler):
    """把警告和错误记在内存里（最近 300 条），给 /diag 和出错通知用。"""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.items: deque = deque(maxlen=300)

    def emit(self, record: logging.LogRecord):
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        self.items.append((record.created, record.levelno, record.name, msg[:300]))

    def recent(self, minutes: float = 60, level: int = logging.WARNING) -> list[tuple]:
        t0 = time.time() - minutes * 60
        return [x for x in self.items if x[0] >= t0 and x[1] >= level]


def _key(name: str, msg: str) -> str:
    return name + "|" + re.sub(r"[\d.:]+", "#", msg)[:50]


# 这些错误一出现就值得告诉你（不用等重复 3 次）
URGENT = ("所有 chat 接口都失败了", "生成回复失败", "发送失败", "Conflict")


class Health:
    def __init__(self, cfg, bot):
        self.cfg, self.bot = cfg, bot
        self.rec = Recorder()
        logging.getLogger().addHandler(self.rec)
        self._task: asyncio.Task | None = None
        self.started = time.time()

    def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        await asyncio.sleep(120)
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(300)

    async def check(self):
        """最近 30 分钟里同一个错误出现 3 次以上（或很严重），就发消息告诉你；同一个问题 3 小时只说一次。"""
        groups: dict[str, list] = {}
        for item in self.rec.recent(30, logging.WARNING):
            if item[1] < logging.ERROR and not any(u in item[3] for u in URGENT):
                continue
            groups.setdefault(_key(item[2], item[3]), []).append(item)
        db = self.bot.db
        sent = db.get("health_notified") or {}
        now = time.time()
        msgs = []
        for k, items in groups.items():
            urgent = any(u in items[-1][3] for u in URGENT)
            if (len(items) >= 3 or urgent) and now - float(sent.get(k, 0)) > 3 * 3600:
                sent[k] = now
                msgs.append(f"· {friendly(items[-1][2], items[-1][3])}（30 分钟内 {len(items)} 次）")
        if msgs and self.bot.owner_id:
            db.set("health_notified", {k: v for k, v in sent.items() if now - v < 86400})
            text = "⚠️ 程序这边出了点问题（不是她在说话）：\n" + "\n".join(msgs[:5]) + \
                   "\n\n发 /diag 看详细情况；日志在 data/logs/ailife.log。"
            from .bot_common import ui
            try:
                await self.bot.app.bot.send_message(self.bot.owner_id, ui(text))
            except Exception:  # noqa: BLE001
                pass


def friendly(name: str, msg: str) -> str:
    m = msg
    if "所有" in m and "接口都失败了" in m:
        return "大模型接口全都连不上（限流、断网或 key 失效）"
    if "1305" in m or "429" in m:
        return "大模型限流（访问量过大），已自动换用备用模型"
    if "发送失败" in m:
        return "发 Telegram 消息失败（代理断了？）"
    if "Conflict" in m:
        return "有另一个程序在用同一个 bot（是不是开了两个 start.bat？）"
    return f"{name.replace('ailife.', '')}：{m[:80]}"


def usage_line(llm, day: str | None = None) -> str:
    """今天大模型用了多少次、限流几次、哪个接口平均多快。"""
    st = llm.stats(day) if hasattr(llm, "stats") else {}
    if not st or not st.get("calls"):
        return ""
    prof = st.get("profiles") or {}
    speed = "，".join(f"{n} {v['ok']}次/{(v['ms'] / max(1, v['ok']) / 1000):.1f}s" for n, v in
                     sorted(prof.items(), key=lambda kv: -kv[1]["ok"]) if v.get("ok"))
    return (f"今天调用大模型 {st['calls']} 次（聊天 {st.get('fg', 0)}，后台 {st.get('bg', 0)}），"
            f"成功 {st.get('ok', 0)}，限流 {st.get('limited', 0)}，用了备用 {st.get('fallback', 0)} 次"
            + (f"\n  {speed}" if speed else ""))


# ---------------- /diag ----------------
async def diagnose(cfg, brain, bot, dash=None) -> str:
    out: list[str] = []
    ok, warn, bad = "✅", "⚠️", "❌"

    async def timed(coro, timeout=25):
        t0 = time.time()
        try:
            r = await asyncio.wait_for(coro, timeout)
            return True, time.time() - t0, r
        except Exception as e:  # noqa: BLE001
            return False, time.time() - t0, e

    # 大模型：每个用到的接口问一句
    llm = brain.llm
    names = []
    for role in ("chat", "memory", "night", "helper", "vision"):
        for n in llm.routes.get(role) or []:
            if n not in names:
                names.append(n)
    async def ping(n):
        p = llm.profiles[n]
        if n in (llm.routes.get("vision") or []) and n not in (llm.routes.get("chat") or []):
            return n, None
        r = await timed(p.client.chat.completions.create(
            messages=[{"role": "user", "content": "回一个字：好"}], **llm._kwargs(p, 0.2, 8)), 30)
        return n, r
    res = await asyncio.gather(*(ping(n) for n in names))
    lines = []
    for n, r in res:
        if r is None:
            lines.append(f"  {ok} {n}（看图，略过）")
        elif r[0]:
            lines.append(f"  {ok} {n} {r[1]:.1f}s")
        else:
            e = str(r[2])
            why = "限流" if ("1305" in e or "429" in e) else "超时" if "timed out" in e.lower() or isinstance(r[2], asyncio.TimeoutError) \
                else "key 无效" if "401" in e or "auth" in e.lower() else e[:40]
            lines.append(f"  {bad if n == (llm.routes.get('chat') or [''])[0] else warn} {n}：{why}")
    out.append("大模型\n" + "\n".join(lines))
    u = usage_line(llm)
    if u:
        out.append(u)

    # 向量检索
    emb = brain.memory.embedder
    if emb.enabled:
        r = await timed(emb.embed(["测试"]))
        out.append(f"记忆向量检索 {ok if r[0] and r[2] else warn} " + (f"{r[1]:.1f}s" if r[0] and r[2] else "不可用，先只用关键词"))
    else:
        out.append(f"记忆向量检索 {warn} 没开（只用关键词）")

    # 语音
    tts = getattr(bot, "tts", None)
    if tts:
        # 显式传声音：自检失败不计入“连续失败 3 次自动换声音”
        r = await timed(tts.synth("你好呀", voice=tts.override), 40)
        voice = next((v[0] for v in __import__("ailife.voice", fromlist=["VOICES"]).VOICES
                      if tts.override and tuple(v[1:]) == tuple(tts.override)), "配置里的默认声音")
        out.append(f"语音合成 {ok if r[0] else bad} {voice}" + (f" {r[1]:.1f}s" if r[0] else f"：{str(r[2])[:60]}"))
    asr = getattr(bot, "asr", None)
    if asr and asr.local:
        out.append(f"语音识别 {ok if asr.local.ready_on_disk() else warn} 本地 SenseVoice：{asr.local.status}")

    # 外部服务
    async def _none():
        return True, 0, None
    w, b, n = await asyncio.gather(timed(brain.weather.fetch(force=True)) if brain.weather.city else _none(),
                                   timed(brain.media.bgm_search("葬送的芙莉莲")),
                                   timed(brain.media.ne_search("晴天", "周杰伦")))
    out.append(f"天气 {ok if w[0] and (w[2] or not brain.weather.city) else warn} "
               + (brain.weather.city or "没设城市"))
    out.append(f"Bangumi {ok if b[0] and b[2] else warn}　网易云 {ok if n[0] and n[2] else warn}")

    # 感知 / 小屋 / 桌宠
    db = brain.db
    pc = db.get("sense_pc") or {}
    pc_ok = pc and time.time() - pc.get("ts", 0) < 120
    out.append(f"电脑感知 {ok if pc_ok else warn} " + (pc.get("desc", "") if pc_ok else "没在更新（不是 Windows 或被关了）"))
    phone = getattr(bot, "phone", None)
    if phone and phone.enabled:
        alive = phone._task is not None and not phone._task.done()
        out.append(f"手机信号 {ok if alive else warn} 频道 {phone.url}")
    if dash is not None:
        out.append(f"小屋网页 {ok if dash.runner else bad} http://127.0.0.1:{dash.port}")
        out.append(f"手机小屋 {ok if dash.public_url else warn} " + (dash.public_url or "隧道没开起来"))
    # 数据
    dbp = Path(db.path)
    size = dbp.stat().st_size / 1024 if dbp.exists() else 0
    bk = sorted((cfg.root / "data" / "backup").glob(f"{dbp.stem}-2*.db")) if (cfg.root / "data" / "backup").exists() else []
    extra = db.get("backup_extra_last") or ""
    out.append(f"数据库 {size:.0f}KB · 记忆 {len(db.all_memories())} 条 · 本地备份 {len(bk)} 份"
               + (f"（最近 {bk[-1].stem[-8:]}）" if bk else "") + (f" · 异地备份 {extra}" if extra else ""))
    # 最近的错误
    errs = bot.health.rec.recent(24 * 60, logging.ERROR) if getattr(bot, "health", None) else []
    if errs:
        last = errs[-3:]
        from datetime import datetime
        out.append("最近 24 小时的错误：\n" + "\n".join(f"  · {datetime.fromtimestamp(e[0], cfg.tz):%H:%M} "
                                                 f"{friendly(e[2], e[3])}" for e in last))
    else:
        out.append(f"最近 24 小时没有错误 {ok}")
    up = (time.time() - (bot.health.started if getattr(bot, "health", None) else time.time())) / 3600
    out.append(f"已经运行 {up:.1f} 小时")
    from .deps import report, version_line
    import platform
    out.insert(0, f"运行在：这台电脑（{platform.node() or '电脑'}）")
    out.append(report(cfg))
    from .secrets import env_path
    out.append("密钥：存在 .env 里 ✅" if env_path(cfg.root).exists() else "密钥：还写在 config.yaml 里（重启一次会自动挪进 .env）")
    return f"🩺 自检结果（{version_line()}）\n\n" + "\n".join(out)


# ---------------- 开机自启（Windows 启动文件夹，不需要管理员） ----------------
def startup_file() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata or sys.platform != "win32":
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "ai-life.cmd"


# ---------------- 运行时不让电脑自动睡眠（Windows） ----------------
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def keep_awake(on: bool) -> bool:
    """她在运行时告诉 Windows“别自动睡眠”（屏幕照常熄灭；合盖、手动点睡眠、关机不受影响）。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:  # noqa: BLE001
        return False


def autostart(root: Path, on: bool) -> str:
    f = startup_file()
    if not f:
        return "只有 Windows 支持开机自启"
    if on:
        # cmd 按控制台代码页（中文 Windows 是 GBK/936）读 .cmd：路径里有中文也要能找到
        try:
            import ctypes
            enc = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        except Exception:  # noqa: BLE001
            enc = "gbk"
        body = f'@echo off\r\ncd /d "{root}"\r\nstart "ai-life" /min "{root / "start.bat"}"\r\n'
        try:
            data = body.encode(enc)
        except (UnicodeEncodeError, LookupError):
            return "程序所在的路径里有当前系统编码写不出的字符，请把 ai-life 文件夹放到纯英文路径，或者双击 autostart_on.bat"
        f.write_bytes(data)
        return f"已开启开机自启（{f}）"
    if f.exists():
        f.unlink()
    return "已关闭开机自启"


def autostart_on() -> bool:
    f = startup_file()
    return bool(f and f.exists())


# ---------------- 异地备份 ----------------
def extra_backup_dir(cfg) -> Path | None:
    c = dict(cfg.get("backup") or {})
    target = str(c.get("extra_dir", "auto") or "").strip()
    if not target or target.lower() in ("off", "none", "false"):
        return None
    if target.lower() == "auto":
        od = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
        if not od or not Path(od).exists():
            return None
        return Path(od) / "ai-life-backup"
    return Path(target)
