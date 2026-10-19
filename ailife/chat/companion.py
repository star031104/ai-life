"""她和你之间的“中枢”：收到他的话以后等他说完、生成回复、拟人化地发出去，以及各种 /命令。

和具体渠道无关：QQ 私聊（channels/qq/bridge.py）把他的消息写进聊天记录，然后调 schedule_reply()；
回复由 DeliveryMixin 发回 QQ。/命令包成 chat/command.py 的 Command 交给 cmd_xxx。
"""
from __future__ import annotations

import asyncio
import logging
import random
import time

from ailife.chat.command import Command
from ailife.chat.commands import CommandsMixin
from ailife.chat.common import HOLD_LINES
from ailife.chat.delivery import TYPING, DeliveryMixin
from ailife.chat.life_cmds import LifeCommandsMixin
from ailife.mind.brain import Brain
from ailife.mind.games import Games
from ailife.mind.output import Parsed

log = logging.getLogger("ailife.bot")

VOICE_IN = "[发了一条语音，但你这边听不了语音：让他打字说]"


class Companion(CommandsMixin, LifeCommandsMixin, DeliveryMixin):
    RETRY_AFTER = (60, 180, 600)
    HOLD_LINES = HOLD_LINES

    def __init__(self, cfg, brain: Brain):
        self.cfg, self.brain = cfg, brain
        self.db = brain.db
        brain.away.bot = self
        self.restart_event = asyncio.Event()     # 设上就让程序正常退出（外面的启动脚本 / systemd 会自动重开）
        from ailife.chat.common import install_ui_pronoun
        install_ui_pronoun(getattr(brain.persona, "ta", "她"))      # 男性角色：系统文字里的“她”改成“他”
        self.qq = None                         # 由 app 注入（channels/qq/bridge.py）
        self._pending: asyncio.Task | None = None
        self._waiting = False                  # 还在“等他说完”阶段（可以被新消息打断重新计时）
        self._lock = asyncio.Lock()
        self._bg: set[asyncio.Task] = set()
        self._answered_upto = 0
        self._last_user_msg_id: int | None = None
        self._retry_n = 0
        self.games = Games(self.db, float((cfg.get("games") or {}).get("mistake_rate", 0.25)))
        self.sched = None                      # 由 app 注入
        self.phone = None
        self.health = None                     # 由 app 注入
        self.dash = None

    # ---------------- 基础 ----------------
    @property
    def owner_id(self) -> int:
        """他的 QQ 号（各处拿它判断“有没有主人”）。"""
        return int(getattr(self.qq, "owner_id", 0) or 0)

    async def start(self):
        return None

    async def stop(self):
        return None

    def spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    # ---------------- 命令 ----------------
    async def run_command(self, c: Command, name: str) -> bool:
        """执行 /命令。返回 False＝没有这个命令。"""
        handler = getattr(self, f"cmd_{name}", None)
        if handler is None:
            return False
        await handler(c)
        return True

    def user_said(self, c: Command | None, content: str, kind: str = "text"):
        """命令里替他写一句话进聊天记录（比如“我看完了第 3 集”），然后算他来说过话了。"""
        qq = getattr(self, "qq", None)
        if qq is not None:
            return qq._user_in(content, kind, getattr(c, "message_id", None))
        mid = self.db.add_message("user", content, kind, channel="qq")
        self._user_active()
        return mid

    # ---------------- 别的地方转来的事 ----------------
    async def on_note(self, text: str):
        if self.sched:
            from ailife.proactive.scheduler import in_quiet
            if not in_quiet(self.brain.now(), self.cfg.proactive.quiet_hours):
                await self.sched._send_proactive(f"他刚在你们小屋的留言板写了：“{text[:200]}”，回应他", note_reply=True)

    async def on_phone(self, ev: str):
        if self.sched:
            await self.sched.on_phone(ev)

    async def send_letter(self, text: str):
        if not self.owner_id:
            return
        m, ch = await self.owner_text(f"💌 这周的信\n\n{text}")
        if m:
            self.db.add_message("assistant", f"[这周写给他的信] {text}", "letter", m.message_id, channel=ch)

    def _user_active(self):
        """他来说话了：清未回复计数、更新情绪和回复统计。"""
        ignored = int(self.db.get("unanswered", 0))
        if ignored:
            self.db.set("ignored_before_reply", ignored)
        if time.time() - (self.db.get("last_user_ts", 0) or 0) > 600:
            self.brain.emotion.on_user_message(ignored)
        self.db.set("unanswered", 0)
        self.db.set("last_user_ts", time.time())
        self.db.mark_proactive_replied(time.time())
        pend = self.db.get("expr_pending")
        if pend and time.time() - pend.get("ts", 0) < 900:        # 她用了学来的说法，他很快回了
            for e in self.db.expressions("approved"):
                if e["id"] in pend.get("ids", []):
                    self.db.set_expression(e["id"], weight=min(3.0, e["weight"] + 0.1))
            self.db.set("expr_pending", None)

    # ---------------- 回复 ----------------
    async def schedule_reply(self, delay: float | None = None):
        """等他说完（连发合并）再回复。正在发送中的回复不会被打断，新消息留到下一轮。"""
        if self._pending and not self._pending.done() and self._waiting:
            self._pending.cancel()
        wait = float(self.cfg.style.merge_wait_seconds) if delay is None else delay
        self._waiting = True
        self._pending = self.spawn(self._reply_after(wait))

    async def _maybe_search(self):
        """他刚问到她可能不知道的事：先在手机上搜一下（写成一条事件给她看），搜到的要点记成她自己知道的事。"""
        if not (self.cfg.get("net") or {}).get("search", True):
            return
        rows = self.db.conn.execute("SELECT role, content FROM messages WHERE role IN ('user','assistant') "
                                    "ORDER BY id DESC LIMIT 6").fetchall()
        texts = []
        for r in rows:                                   # 只看她上次回复以后他说的
            if r["role"] != "user":
                break
            texts.insert(0, r["content"])
        joined = "\n".join(texts)[-600:]
        from ailife.media.search import ASK_RE, DECIDE_PROMPT, WebSearch
        if not joined or not ASK_RE.search(joined):
            return
        hour = int(time.time() // 3600)
        used = self.db.get("search_hour") or {}
        if used.get("h") == hour and int(used.get("n", 0)) >= int((self.cfg.get("net") or {}).get("per_hour", 12)):
            return
        d = await self.brain.llm.json([{"role": "user", "content": DECIDE_PROMPT + joined}], default={}, role="helper")
        if not isinstance(d, dict) or not d.get("need") or not str(d.get("query") or "").strip():
            return
        if not hasattr(self, "_web"):
            self._web = WebSearch(self.cfg)
        q = str(d["query"]).strip()[:40]
        res = await self._web.search(q)
        self.db.set("search_hour", {"h": hour, "n": (int(used.get("n", 0)) + 1) if used.get("h") == hour else 1})
        if not res:
            return
        self.db.add_message("event", self._web.block(q, res))
        topic = str(d.get("topic") or q)[:20]
        top = "；".join(r["snippet"][:80] for r in res[:2] if r.get("snippet"))
        if top:
            self.db.add_memory(f"我查过「{topic}」：{top}"[:200], 2, "self")

    async def _reply_after(self, wait: float):
        try:
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            return
        self._waiting = False
        try:
            await self._maybe_search()
        except Exception as e:  # noqa: BLE001
            log.info("搜一搜没成功：%s", e)
        async with self._lock:
            # 排队期间上一轮已经看到了他最新的消息，就不用再回一次
            last_user = self.db.last_message("user")
            if last_user and last_user["id"] <= self._answered_upto:
                return
            prev_upto = self._answered_upto
            if last_user:
                self._answered_upto = last_user["id"]
            try:
                await self.send_action(TYPING)
                p = await self.brain.reply()
            except Exception as e:  # noqa: BLE001
                log.exception("生成回复失败：%s", e)
                self._answered_upto = prev_upto          # 没回成：留着等重试
                p = None
            if p is not None:
                self._retry_n = 0
                await self.deliver(p, quote_id=self._last_user_msg_id if p.quote else None)
        if p is None:
            await self._hold_on(last_user)               # 在锁外面发“等我一下”，不挡住别的回复
            return
        if p.story is not None:
            self.spawn(self._bedtime(p, last_user["content"] if last_user else ""))
        self.spawn(self._after_turn(p))

    async def _hold_on(self, last_user):
        """大模型全部失败：先用一句话稳住他（一段时间只说一次），过一会儿自动重试，补上这条回复。"""
        n = self._retry_n
        if not last_user or n >= len(self.RETRY_AFTER):
            if last_user:
                log.warning("重试了 %d 次还是不行，等他下一条消息再回", n)
                self.db.add_message("event", "你刚才因为网络问题一直没能回他，等他再说话时，先简单说一句刚才网不好")
            self._retry_n = 0
            return
        out, chat, ch = self._out()
        if time.time() - float(self.db.get("hold_sent_ts") or 0) > 600 and chat:
            line = random.choice(getattr(self.brain.persona, "hold_lines", None) or self.HOLD_LINES)
            try:
                m = await out.send_message(chat, line)
                self.db.add_message("assistant", line, "text", m.message_id, channel=ch)
                self.db.set("hold_sent_ts", time.time())
            except Exception as e:  # noqa: BLE001
                log.warning("连“等一下”都没发出去：%s", e)
        self._retry_n = n + 1
        delay = self.RETRY_AFTER[n]
        log.info("%d 秒后重试回复（第 %d 次）", delay, n + 1)

        old = getattr(self, "_retry_task", None)
        if old and not old.done():
            old.cancel()                                 # 只留最新的一次重试

        async def later():
            await asyncio.sleep(delay)
            lu = self.db.last_message("user")
            if not lu or lu["id"] <= self._answered_upto:
                return                                   # 这期间已经回上了（他又发了消息、新一轮回复成功）
            self.db.add_message("event", "（刚才网络不好，现在恢复了：接着回他刚才的话，可以顺口说一句久等了）")
            await self.schedule_reply(0.5)
        self._retry_task = self.spawn(later())

    async def _bedtime(self, p: Parsed, user_text: str):
        """睡前：他想听故事就讲一个（文字）。"""
        from ailife.mind.output import Bubble, Parsed as P
        try:
            await self.send_action(TYPING)
            text = await self.brain.moments.bedtime_story(p.story)
            if text:
                async with self._lock:
                    await self.deliver(P(bubbles=[Bubble(text=text)]), kind="story")
        except Exception as e:  # noqa: BLE001
            log.warning("睡前故事没发出去：%s", e)

    async def _after_turn(self, p: Parsed):
        try:
            last_user = self.db.last_message("user")
            quick = bool(last_user) and self.brain.memory.needs_quick_extract(
                last_user["content"], tagged=bool(p.dates or p.shows))
            await self.brain.memory.maybe_extract(force=quick)
        except Exception as e:  # noqa: BLE001
            log.warning("整理记忆失败：%s", e)
