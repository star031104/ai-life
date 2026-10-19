"""手机信号：手机上的快捷指令（iPhone）或 MacroDroid（安卓）在“闹钟关掉 / 到家 / 开始充电”时
往 ntfy.sh 的一个私密频道发一句话，这里订阅那个频道，把状态告诉她（起床了、到家了、准备睡了）。
频道名在 /sense 命令和小屋“设置”里能看到。config.yaml 的 sense.phone 里可以关掉。
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time

import httpx

log = logging.getLogger("ailife.sense")


PHONE_WORDS = [
    (("起床", "闹钟", "醒"), "刚起床"),
    (("到家", "回家"), "刚到家"),
    (("出门", "离开家"), "刚出门"),
    (("下班",), "下班了"),
    (("到公司", "到学校", "上班"), "到公司/学校了"),
    (("充电", "睡觉", "晚安"), "准备睡了（手机开始充电）"),
    (("电量低", "没电"), "手机快没电了"),
    (("健身", "运动"), "去运动了"),
]


def normalize_phone(text: str) -> str:
    t = (text or "").strip()
    for words, ev in PHONE_WORDS:
        if any(w in t for w in words):
            return ev
    return t[:40]


class PhoneSense:
    """订阅 ntfy 频道，手机发来的状态交给 on_event(event)。"""

    def __init__(self, cfg, db, on_event):
        c = dict((cfg.get("sense") or {}).get("phone") or {})
        self.enabled = bool(c.get("enabled", True))
        self.server = (c.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
        self.topic = c.get("ntfy_topic") or db.get("ntfy_topic")
        if not self.topic:
            self.topic = "ailife-" + secrets.token_hex(8)
            db.set("ntfy_topic", self.topic)
        self.proxy = cfg.proxy or None
        self.cfg_root = cfg.get("root")
        self.tz = cfg.get("tz")
        self.db, self.on_event = db, on_event
        self._task: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"{self.server}/{self.topic}"

    def start(self):
        if self.enabled:
            self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        delay = 5
        while True:
            for proxy in ([self.proxy, None] if self.proxy else [None]):
                try:
                    async with httpx.AsyncClient(proxy=proxy, timeout=httpx.Timeout(10, read=None)) as cli:
                        since = self.db.get("ntfy_since") or "10m"
                        async with cli.stream("GET", f"{self.url}/json", params={"since": since}) as r:
                            r.raise_for_status()
                            delay = 5
                            async for line in r.aiter_lines():
                                if not line.strip():
                                    continue
                                try:
                                    j = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                if j.get("event") == "message":
                                    self.db.set("ntfy_since", j.get("id") or "10m")
                                    await self.handle(j.get("message") or "")
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.debug("ntfy 连接断开：%s", e)
            await asyncio.sleep(delay)
            delay = min(300, delay * 2)

    async def handle(self, text: str):
        ev = normalize_phone(text)
        if not ev:
            return
        self.db.set("sense_phone", {"event": ev, "raw": text[:60], "ts": time.time()})
        log.info("手机传来：%s", ev)
        try:
            await self.on_event(ev)
        except Exception as e:  # noqa: BLE001
            log.warning("处理手机事件出错：%s", e)
