"""把她的回复拟人化地发出去：分条、打字停顿、偶尔打错字、贴纸、语音、表情回应。"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
import time

from telegram.constants import ChatAction

from .output import Parsed, emotion_to_emojis
from .voice import to_ogg_opus
from .bot_common import EMOTION_LABELS, TYPOS, to_reaction  # noqa: F401

log = logging.getLogger("ailife.bot")


class DeliveryMixin:
    async def send_action(self, action):
        if self.owner_id:
            try:
                await self.app.bot.send_chat_action(self.owner_id, action)
            except Exception:  # noqa: BLE001
                pass

    async def _typing_pause(self, text: str, first: bool):
        st = self.cfg.style
        secs = len(text) / max(1.0, float(st.typing_chars_per_sec))
        secs = min(float(st.max_typing_delay), secs) * random.uniform(0.7, 1.2)
        if first:
            secs *= 0.6
        await self.send_action(ChatAction.TYPING)
        while secs > 0:
            await asyncio.sleep(min(4.5, secs))
            secs -= 4.5
            if secs > 0:
                await self.send_action(ChatAction.TYPING)

    async def deliver(self, p: Parsed, quote_id: int | None = None, kind: str = "text") -> bool:
        """把解析后的回复一条条发出去，并写入聊天记录。"""
        chat = self.owner_id
        if not chat or p.skip or not p.bubbles:
            return False
        bot = self.app.bot
        voice_p = float(self.cfg.style.voice_probability)
        sent_any = False
        voices_sent = 0
        rate = float(self.cfg.style.get("sticker_rate", 1.0))
        has_text = any(b.text for b in p.bubbles)
        if has_text and rate < 1:
            p.bubbles = [b for b in p.bubbles if not b.sticker or random.random() < rate]
        if p.reaction and self._last_user_msg_id and kind != "proactive":
            await self._react(self._last_user_msg_id, p.reaction)
        style = self.brain.emotion.voice_style()
        typo_rate = float(self.cfg.style.get("typo_rate", 0.03))
        for i, b in enumerate(p.bubbles):
            reply_to = quote_id if i == 0 else None
            try:
                if b.photo:
                    await self.send_action(ChatAction.UPLOAD_PHOTO)
                    img = await self.brain.photos.make(b.photo)
                    if img:
                        try:      # 存一份进“相册”，小档案网页里能看
                            d = self.cfg.root / "data" / "photos"
                            d.mkdir(parents=True, exist_ok=True)
                            (d / f"{int(time.time())}.jpg").write_bytes(img)
                        except OSError:
                            pass
                        m = await bot.send_photo(chat, img, reply_to_message_id=reply_to)
                        self.db.add_message("assistant", f"[照片:{b.photo}]", "photo", m.message_id)
                        sent_any = True
                    else:
                        self.db.add_message("event", f"你想发一张照片（{b.photo}），但没发出去")
                    continue
                if b.sticker:
                    await asyncio.sleep(random.uniform(0.6, 1.6))
                    await self._send_sticker(b.sticker, reply_to)
                    self.db.add_message("assistant", f"[表情:{b.sticker}]", "sticker")
                    sent_any = True
                    continue
                as_voice = (b.voice or (voice_p > 0 and len(b.text) < 60 and random.random() < voice_p)) \
                    and voices_sent < 1

                if as_voice:
                    try:
                        await self.send_action(ChatAction.RECORD_VOICE)
                        audio = await to_ogg_opus(await self.tts.synth(b.text, style=style))
                        await asyncio.sleep(min(4.0, len(b.text) / 8))
                        m = await bot.send_voice(chat, audio, reply_to_message_id=reply_to)
                        self.db.add_message("assistant", f"[语音] {b.text}", "voice", m.message_id)
                        sent_any = True
                        voices_sent += 1
                        continue
                    except Exception as e:  # noqa: BLE001
                        log.warning("语音发送失败，改发文字：%s", e)
                await self._typing_pause(b.text, first=(i == 0))
                if b.recall:                        # 说出口又后悔了：发出去几秒后撤回
                    m = await bot.send_message(chat, b.text, reply_to_message_id=reply_to)
                    await asyncio.sleep(random.uniform(2.5, 5))
                    try:
                        await bot.delete_message(chat, m.message_id)
                        self.db.add_message("event", f"你发了「{b.text}」又马上撤回了（他可能看到了）")
                    except Exception as e:  # noqa: BLE001
                        log.info("撤回失败：%s", e)
                        self.db.add_message("assistant", b.text, kind, m.message_id)
                    sent_any = True
                    continue
                typo = self._make_typo(b.text) if random.random() < typo_rate else None
                m = await bot.send_message(chat, typo or b.text, reply_to_message_id=reply_to)
                if typo:                            # 打错字，过两秒改过来
                    await asyncio.sleep(random.uniform(1.5, 3.5))
                    try:
                        await bot.edit_message_text(b.text, chat_id=chat, message_id=m.message_id)
                    except Exception:  # noqa: BLE001
                        pass
                self.db.add_message("assistant", b.text, kind, m.message_id)
                sent_any = True
            except Exception as e:  # noqa: BLE001
                log.exception("发送失败：%s", e)
        for title, artist in p.songs[:2]:           # 她推荐的歌：附上网易云链接
            try:
                info = await self.brain.media.recommend_link(title, artist)
                if info:
                    text = f"🎵 《{info['title']}》- {info['artist']}\n{info['url']}"
                else:
                    text = f"🎵 《{title}》" + (f"- {artist}" if artist else "")
                m = await bot.send_message(chat, text, disable_web_page_preview=False)
                self.db.add_message("assistant", f"[推荐了一首歌] 《{title}》{('-' + artist) if artist else ''}", "song",
                                    m.message_id)
                sent_any = True
            except Exception as e:  # noqa: BLE001
                log.info("推歌失败：%s", e)
        return sent_any

    @staticmethod
    def _make_typo(text: str) -> str | None:
        if len(text) < 6:
            return None
        cands = [(k, v) for k, v in TYPOS.items() if k in text]
        if not cands:
            return None
        k, v = random.choice(cands)
        return text.replace(k, v, 1)

    async def _react(self, msg_id: int, emoji: str):
        try:
            await self.app.bot.set_message_reaction(self.owner_id, msg_id, to_reaction(emoji))
            self.db.add_message("event", f"你给他刚才的消息点了 {to_reaction(emoji)}")
        except Exception as e:  # noqa: BLE001
            log.info("表情回应失败：%s", e)

    # ---------------- 贴纸按情绪分类 ----------------
    async def _label_one(self, row) -> str | None:
        """让看图模型看一眼贴纸的缩略图，给它打一个情绪标签。"""
        if not self.brain.llm.vision:
            return None
        async with self._label_lock:
            try:
                f = await self.app.bot.get_file(row["thumb"] or row["file_id"])     # 缩略图（动图贴纸也能看）
                raw = bytes(await f.download_as_bytearray())
                from .voice import ffmpeg
                png = await ffmpeg(["-i", "pipe:0", "-frames:v", "1", "-vf", "scale=256:-1", "-f", "image2",
                                    "-vcodec", "png", "pipe:1"], raw)
                b64 = base64.b64encode(png).decode()
                ans = await self.brain.llm.label_image(
                    b64, "这是一个聊天表情包。它表达的情绪最接近下面哪一个？只回答一个词：" + "、".join(EMOTION_LABELS))
                label = next((w for w in EMOTION_LABELS if w in (ans or "")), None)
                self.db.set_sticker_emotion(row["file_id"], label)
                if label:
                    log.info("贴纸 %s 标记为「%s」", row["emoji"], label)
                await asyncio.sleep(3)
                return label
            except Exception as e:  # noqa: BLE001
                self.db.set_sticker_emotion(row["file_id"], None)
                log.info("贴纸打标签失败：%s", str(e)[:120])
                return None

    async def label_stickers(self, n: int = 20):
        for row in self.db.unlabeled_stickers(n):
            await self._label_one(row)

    async def _send_sticker(self, emotion: str, reply_to: int | None):
        emojis = emotion_to_emojis(emotion)
        rows = self.db.stickers_with_emotion(emotion) or self.db.stickers_with_emoji(emojis)
        if rows:
            st = random.choice(rows)
            await self.app.bot.send_sticker(self.owner_id, st["file_id"], reply_to_message_id=reply_to)
        elif emojis:
            # 没有对应贴纸时发单个 emoji（TG 会显示成大号动画表情）
            await self.app.bot.send_message(self.owner_id, emojis[0], reply_to_message_id=reply_to)
