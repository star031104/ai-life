"""Telegram 聊天 Bot：收消息（文字/贴纸/图片）、合并连发、拟人化地分条回复。"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler,
                          MessageReactionHandler, filters)

from ailife.mind.brain import Brain
from ailife.mind.games import Games
from ailife.mind.output import Parsed
from ailife.channels.telegram.commands import CommandsMixin
from ailife.channels.telegram.delivery import DeliveryMixin
from ailife.channels.telegram.life import LifeCommandsMixin
from ailife.channels.telegram.media import MediaMixin
from ailife.media.links import find_links as links_in
from ailife.channels.telegram.common import (EMOTION_LABELS, HELP, HELP_TEXT, REACTION_ALIAS, REACTIONS, TYPOS,  # noqa: F401
                         stats_line, to_reaction)

log = logging.getLogger("ailife.bot")

VOICE_IN = "[发了一条语音，但你这边听不了语音：让他打字说]"


def tg_configured(cfg) -> bool:
    tok = str((cfg.get("telegram") or {}).get("bot_token") or "").strip()
    return bool(tok) and ":" in tok and not tok.startswith("123456:ABC")

class ChatBot(CommandsMixin, LifeCommandsMixin, MediaMixin, DeliveryMixin):
    def __init__(self, cfg, brain: Brain):
        self.cfg, self.brain = cfg, brain
        self.db = brain.db
        brain.away.bot = self
        self.restart_event = asyncio.Event()     # 设上就让 run.py 正常退出（外面的启动脚本会自动重开）
        from ailife.channels.telegram.common import install_ui_pronoun
        install_ui_pronoun(getattr(brain.persona, "ta", "她"))      # 男性角色：系统提示里的“她”改成“他”
        self.qq = None                         # 由 main 注入（QQ 私聊 / 群聊，config 里 qq.enabled 打开时）
        self.tg = tg_configured(cfg) or not (cfg.get("qq") or {}).get("enabled")
        self.app: Application | None = None
        if self.tg:           # 没填 Telegram token、又开了 QQ：只用 QQ（Telegram 那一套都不启动）
            b = ApplicationBuilder().token(cfg.telegram.bot_token)
            if cfg.proxy:
                b = b.proxy(cfg.proxy).get_updates_proxy(cfg.proxy)
            b = b.connect_timeout(30).read_timeout(30).write_timeout(60)
            self.app = b.build()
        self._pending: asyncio.Task | None = None
        self._waiting = False                  # 还在“等他说完”阶段（可以被新消息打断重新计时）
        self._lock = asyncio.Lock()
        self._bg: set[asyncio.Task] = set()
        self._answered_upto = 0
        self._last_user_msg_id: int | None = None
        self.games = Games(self.db, float((cfg.get("games") or {}).get("mistake_rate", 0.25)))
        self.sched = None                      # 由 main 注入
        self.pc = None
        self.phone = None
        self.miniapp_url: str | None = None
        self.health = None                     # 由 main 注入
        self.dash = None
        self._label_lock = asyncio.Lock()
        self._register()

    # ---------------- 基础 ----------------
    @property
    def owner_id(self) -> int:
        """他的 ID：有 Telegram 时是 TG ID；只用 QQ 时是他的 QQ 号。（各处拿它判断“有没有主人”）"""
        if not self.tg:
            return int(getattr(self.qq, "owner_id", 0) or 0)
        return int(self.cfg.telegram.get("owner_id") or self.db.get("owner_id", 0) or 0)

    def _is_owner(self, update: Update) -> bool:
        if getattr(update, "trusted_owner", False):     # QQ 私聊转过来的命令（已经核对过是他）
            return True
        uid = update.effective_user.id if update.effective_user else 0
        return bool(self.owner_id) and uid == self.owner_id

    def _register(self):
        a = self.app
        if a is None:
            return
        a.add_handler(CommandHandler("start", self.cmd_start))
        a.add_handler(CommandHandler("memory", self.cmd_memory))
        a.add_handler(CommandHandler("forget", self.cmd_forget))
        a.add_handler(CommandHandler("diary", self.cmd_diary))
        a.add_handler(CommandHandler("status", self.cmd_status))
        a.add_handler(CommandHandler("city", self.cmd_city))
        a.add_handler(CommandHandler("schedule", self.cmd_schedule))
        a.add_handler(CommandHandler("dates", self.cmd_dates))
        a.add_handler(CommandHandler("shows", self.cmd_shows))
        a.add_handler(CommandHandler("game", self.cmd_game))
        a.add_handler(CommandHandler("photo", self.cmd_photo))
        a.add_handler(CommandHandler("help", self.cmd_help))
        a.add_handler(CommandHandler("home", self.cmd_home))
        a.add_handler(CommandHandler("sense", self.cmd_sense))
        a.add_handler(CommandHandler("letter", self.cmd_letter))
        a.add_handler(CommandHandler("note", self.cmd_note))
        a.add_handler(CommandHandler("watched", self.cmd_watched))
        a.add_handler(CommandHandler("diag", self.cmd_diag))
        a.add_handler(CommandHandler("autostart", self.cmd_autostart))
        a.add_handler(CommandHandler("song", self.cmd_song))
        a.add_handler(CommandHandler("profile", self.cmd_profile))
        a.add_handler(CommandHandler("story", self.cmd_story))
        a.add_handler(CommandHandler("mind", self.cmd_mind))
        a.add_handler(CallbackQueryHandler(self.on_callback, pattern=r"^g:"))
        a.add_handler(CallbackQueryHandler(self.on_habit_button, pattern=r"^h:"))
        a.add_handler(CommandHandler("habit", self.cmd_habit))
        a.add_handler(CommandHandler("recap", self.cmd_recap))
        a.add_handler(CommandHandler("wish", self.cmd_wish))
        a.add_handler(CommandHandler("capsule", self.cmd_capsule))
        a.add_handler(CommandHandler("goals", self.cmd_goals))
        a.add_handler(CommandHandler("people", self.cmd_people))
        a.add_handler(CommandHandler("quality", self.cmd_quality))
        a.add_handler(CommandHandler("persona", self.cmd_persona))
        a.add_handler(CallbackQueryHandler(self.on_wish_button, pattern=r"^w:"))
        a.add_handler(MessageReactionHandler(self.on_reaction))
        new = filters.UpdateType.MESSAGE          # 忽略“编辑消息”
        a.add_handler(MessageHandler(new & filters.TEXT & ~filters.COMMAND, self.on_text))
        a.add_handler(MessageHandler(new & filters.Sticker.ALL, self.on_sticker))
        a.add_handler(MessageHandler(new & (filters.VOICE | filters.AUDIO), self.on_voice))
        a.add_handler(MessageHandler(new & filters.PHOTO, self.on_photo))
        a.add_handler(MessageHandler(new & filters.ANIMATION, self.on_animation))       # 要排在文件前面（动图也算文件）
        a.add_handler(MessageHandler(new & (filters.VIDEO | filters.VIDEO_NOTE), self.on_video))
        a.add_handler(MessageHandler(new & filters.Document.ALL, self.on_document))
        a.add_handler(MessageHandler(new & (filters.LOCATION | filters.VENUE), self.on_location))
        a.add_handler(MessageHandler(new & filters.Dice.ALL, self.on_dice))
        a.add_handler(MessageHandler(new & ~filters.COMMAND & ~filters.StatusUpdate.ALL, self.on_other))

    async def start(self):
        if not self.tg:
            log.info("没填 Telegram bot token：只用 QQ")
            return
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
        me = await self.app.bot.get_me()
        log.info("Bot 已上线：@%s", me.username)
        try:
            from telegram import BotCommand
            from ailife.channels.telegram.common import ui
            await self.app.bot.set_my_commands([BotCommand(c, ui(d)) for c, d in HELP])
        except Exception as e:  # noqa: BLE001
            log.warning("设置命令菜单失败：%s", e)
        await self._load_sticker_sets()

    async def stop(self):
        if not self.tg:
            return
        try:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        except Exception:  # noqa: BLE001
            pass

    async def _load_sticker_sets(self):
        for name in self.cfg.telegram.get("sticker_sets") or []:
            try:
                s = await self.app.bot.get_sticker_set(name)
                for st in s.stickers:
                    self.db.add_sticker(st.file_id, st.file_unique_id, st.emoji, name, "set",
                                        st.thumbnail.file_id if st.thumbnail else None)
                log.info("已载入贴纸包 %s（%d 张）", name, len(s.stickers))
            except Exception as e:  # noqa: BLE001
                log.warning("载入贴纸包 %s 失败：%s", name, e)

    async def desk_chat(self, text: str) -> dict:
        """桌宠（或别的程序通过 OpenAI 兼容接口）发来的话：同一个大脑、同一份聊天记录。"""
        async with self._lock:
            self.db.add_message("user", text, "desk")
            self._user_active()
            self.brain.together.observe(text)
            last = self.db.last_message("user")
            if last:
                self._answered_upto = max(self._answered_upto, last["id"])
            p = await self.brain.reply()
        out = []
        for b in p.bubbles:
            if b.text:
                self.db.add_message("assistant", b.text, "desk")
                out.append({"text": b.text})
            elif b.sticker:
                out.append({"emotion": b.sticker})
        for title, artist in p.songs[:1]:
            try:
                info = await self.brain.media.recommend_link(title, artist)
            except Exception:  # noqa: BLE001
                info = None
            line = f"🎵 《{title}》{('-' + artist) if artist else ''}" + (f"\n{info['url']}" if info else "")
            self.db.add_message("assistant", line, "desk")
            out.append({"text": line})
        self.spawn(self._after_turn(p))
        last = self.db.conn.execute("SELECT MAX(id) m FROM messages").fetchone()["m"] or 0
        return {"bubbles": out, "style": self.brain.emotion.mood_style(), "mood": self.brain.mood, "last_id": last}

    async def on_note(self, text: str):
        if self.sched:
            from ailife.proactive.scheduler import in_quiet
            if not in_quiet(self.brain.now(), self.cfg.proactive.quiet_hours):
                await self.sched._send_proactive(f"他刚在你们小屋的留言板写了：“{text[:200]}”，回应他", note_reply=True)

    async def on_phone(self, ev: str):
        if self.sched:
            await self.sched.on_phone(ev)

    async def setup_miniapp(self, url: str):
        """把“小屋”按钮放到聊天输入框左边的菜单里。"""
        self.miniapp_url = url
        if not self.tg:
            return
        try:
            from telegram import MenuButtonWebApp, WebAppInfo
            await self.app.bot.set_chat_menu_button(
                chat_id=self.owner_id or None,
                menu_button=MenuButtonWebApp(text="小屋", web_app=WebAppInfo(url)))
            log.info("手机小屋已就绪：%s（聊天框左边的“小屋”按钮）", url)
        except Exception as e:  # noqa: BLE001
            log.warning("设置小屋按钮失败：%s", e)

    async def send_letter(self, text: str):
        if not self.owner_id:
            return
        m, ch = await self.owner_text(f"💌 这周的信\n\n{text}")
        if m:
            self.db.add_message("assistant", f"[这周写给他的信] {text}", "letter", m.message_id, channel=ch)

    # ---------------- 他给她的消息点了表情 ----------------
    async def on_reaction(self, update: Update, ctx):
        r = update.message_reaction
        if not r or not r.user or r.user.id != self.owner_id:
            return
        new = [getattr(x, "emoji", None) or "表情" for x in (r.new_reaction or [])]
        if not new:
            return
        row = self.db.conn.execute("SELECT * FROM messages WHERE tg_msg_id=? AND role='assistant'",
                                   (r.message_id,)).fetchone()
        what = f"「{row['content'][:30]}」" if row else "一条消息"
        from ailife.mind.feedback import classify_reaction
        mood = classify_reaction(new)
        note = {"pos": "", "neg": "（看起来他不喜欢这句）", "sad": "（他被这句说得有点难过 / 心疼）"}[mood]
        self.db.add_message("event", f"他给你的{what}点了 {''.join(new)}{note}")
        if mood == "pos":
            self.brain.emotion.nudge(joy=+5)
        elif mood == "neg":
            self.brain.emotion.nudge(joy=-3, sulk=+4)
            if row:
                self.brain.quality.dislike(row["content"], "".join(new))
        if row:
            for e in self.db.expressions("approved"):
                if e["phrase"] in row["content"]:
                    w = e["weight"] + (0.3 if mood == "pos" else -0.6 if mood == "neg" else 0)
                    self.db.set_expression(e["id"], weight=max(0.1, min(3.0, w)))

    # ---------------- 收消息 ----------------
    def _quote_ctx(self, msg) -> str:
        """他引用（回复）了某条消息：告诉她引的是哪句。"""
        rt = getattr(msg, "reply_to_message", None)
        q = getattr(msg, "quote", None)                     # 只引用了一部分时
        if not rt and not q:
            return ""
        text = (getattr(q, "text", None) or "") or ((rt.text or rt.caption or "") if rt else "")
        if rt and not text:
            if rt.sticker:
                text = f"一个表情{rt.sticker.emoji or ''}"
            elif rt.voice:
                text = "一条语音"
            elif rt.photo:
                text = "一张照片"
            else:
                text = "一条消息"
        mine = bool(rt and rt.from_user and self.owner_id and rt.from_user.id == self.owner_id)
        who = "他自己之前说的" if mine else "你说的"
        text = text.replace("\n", " ").strip()
        return f"[引用了{who}「{text[:60]}{'…' if len(text) > 60 else ''}」] "

    def _user_in(self, update: Update, content: str, kind: str):
        try:
            content = self._quote_ctx(update.message) + content
        except Exception as e:  # noqa: BLE001
            log.debug("引用解析失败：%s", e)
        mid = self.db.add_message("user", content, kind, update.message.message_id, channel="tg")
        self.db.set("last_channel", "tg")             # 他在 Telegram 说话了：之后回复、主动消息都发 Telegram
        self._last_user_msg_id = update.message.message_id
        self._user_active()
        return mid

    def _user_active(self):
        """他来说话了：清未回复计数、更新情绪和回复统计。Telegram 和桌宠都走这里。"""
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

    async def on_text(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        text = update.message.text
        st = self.games.state
        if st and st.get("kind") == "guess" and time.time() - st.get("ts", 0) > 1800:
            self.games.stop()                    # 猜数字放着半小时没动，就当结束了
            st = None
        if st and st.get("kind") == "guess" and text.strip().isdigit():
            msg, ended = self.games.guess(int(text.strip()))
            self._user_in(update, f"[猜数字] {text.strip()}", "game")
            await update.message.reply_text(msg)
            if ended:
                self.db.add_message("event", ended)
                await self.schedule_reply(1.0)
            return
        self.brain.together.observe(text)
        self.brain.rhythm.note_birthday_wish(text)
        links = self.brain.links
        if links.enabled and links_in(text):
            async def work():                     # 他发了链接：后台点开看看，看完再回
                extra = await links.enrich(text)
                return text + ("\n" + extra if extra else "")
            self._later(update, "text", text + "\n[他发了个链接，你正在点开看]", work)
            return
        self._user_in(update, text, "text")
        await self.schedule_reply()

    async def on_sticker(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        st = update.message.sticker
        self.db.add_sticker(st.file_id, st.file_unique_id, st.emoji, st.set_name, "user",  # “偷”表情包
                           st.thumbnail.file_id if st.thumbnail else None)
        row = self.db.conn.execute("SELECT * FROM stickers WHERE unique_id=?", (st.file_unique_id,)).fetchone()
        meaning = row["emotion"] if row and row["emotion"] else ""
        self._user_in(update, f"[发了一个表情 {st.emoji or ''}{('（' + meaning + '）') if meaning else ''}]", "sticker")
        if row and not row["emotion"] and row["label_tried"] < 3:
            self.spawn(self._label_one(row))
        await self.schedule_reply()

    async def on_voice(self, update: Update, ctx):
        """语音功能已经去掉：她听不了语音，只知道他发了一条。"""
        if not self._is_owner(update):
            return
        self._user_in(update, VOICE_IN, "text")
        await self.schedule_reply()

    async def on_photo(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        cap = update.message.caption or ""
        desc = ""
        if self.brain.llm.vision:
            try:
                f = await ctx.bot.get_file(update.message.photo[-1].file_id)
                b64 = base64.b64encode(bytes(await f.download_as_bytearray())).decode()
                desc = await self.brain.llm.describe_image(b64, cap)
            except Exception as e:  # noqa: BLE001
                log.warning("看图失败：%s", e)
        if desc:
            content = f"[发了一张图片，图里是：{desc}] {cap}".strip()
        else:
            content = f"[发了一张图片，你看不清内容] {cap}".strip()
        self._user_in(update, content, "photo")
        await self.schedule_reply()

    # ---------------- 回复 ----------------
    def spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    async def schedule_reply(self, delay: float | None = None):
        """等他说完（连发合并）再回复。正在发送中的回复不会被打断，新消息留到下一轮。"""
        if self._pending and not self._pending.done() and self._waiting:
            self._pending.cancel()
        wait = float(self.cfg.style.merge_wait_seconds) if delay is None else delay
        self._waiting = True
        self._pending = self.spawn(self._reply_after(wait))

    async def _reply_after(self, wait: float):
        try:
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            return
        self._waiting = False
        async with self._lock:
            # 排队期间上一轮已经看到了他最新的消息，就不用再回一次
            last_user = self.db.last_message("user")
            if last_user and last_user["id"] <= self._answered_upto:
                return
            prev_upto = self._answered_upto
            if last_user:
                self._answered_upto = last_user["id"]
            try:
                await self.send_action(ChatAction.TYPING)
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

    from ailife.channels.telegram.common import HOLD_LINES
    RETRY_AFTER = (60, 180, 600)

    async def _hold_on(self, last_user):
        """大模型全部失败：先用一句话稳住他（一段时间只说一次），过一会儿自动重试，补上这条回复。"""
        n = getattr(self, "_retry_n", 0)
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
            await self.send_action(ChatAction.TYPING)
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
