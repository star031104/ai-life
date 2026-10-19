"""把她的回复拟人化地发出去：分条、打字停顿、偶尔打错字再补一句“*正确的字”、表情包、说完又撤回。

发送器是 QQ 私聊（channels/qq/onebot.py 的 QQOut）。它的方法名沿用通用的 send_message / send_photo /
delete_message / typing……以后接别的渠道，只要给一个同样方法的发送器就行。
"""
from __future__ import annotations

import asyncio
import logging
import random
import time

from ailife.chat.common import EMOTION_LABELS, TYPOS  # noqa: F401
from ailife.mind.output import Parsed, emotion_to_emojis

log = logging.getLogger("ailife.bot")

TYPING, UPLOAD_PHOTO = "typing", "upload_photo"


class DeliveryMixin:
    # ---------------- 发到哪 ----------------
    def channel(self) -> str:
        return "qq"

    def _out(self):
        """(发送器, 对方 ID, 渠道)。QQ 还没连上时对方 ID 是 0（这时什么都不发）。"""
        qq = getattr(self, "qq", None)
        if qq is not None and qq.private_ready():
            return qq.out, qq.owner_id, "qq"
        return None, 0, "qq"

    async def owner_text(self, text: str):
        """发一段普通文字给他（信、纪念册、提醒之类）。返回 (消息, 渠道)，没发出去是 (None, 渠道)。"""
        out, chat, ch = self._out()
        if not chat:
            return None, ch
        try:
            return await out.send_message(chat, text), ch
        except Exception as e:  # noqa: BLE001
            log.warning("给他发消息失败：%s", e)
            return None, ch

    async def send_action(self, action: str = TYPING):
        """对方那边显示“正在输入…”。"""
        out, chat, _ = self._out()
        if chat and action == TYPING:
            try:
                await out.typing(chat)
            except Exception:  # noqa: BLE001
                pass

    async def _typing_pause(self, text: str, first: bool):
        st = self.cfg.style
        secs = len(text) / max(1.0, float(st.typing_chars_per_sec))
        secs = min(float(st.max_typing_delay), secs) * random.uniform(0.7, 1.2)
        if first:
            secs *= 0.6
        await self.send_action(TYPING)
        while secs > 0:
            await asyncio.sleep(min(4.5, secs))
            secs -= 4.5
            if secs > 0:
                await self.send_action(TYPING)

    async def deliver(self, p: Parsed, quote_id: int | None = None, kind: str = "text") -> bool:
        """把解析后的回复一条条发出去，并写入聊天记录。"""
        out, chat, ch = self._out()
        if not chat or p.skip or not p.bubbles:
            return False
        sent_any = False
        rate = float(self.cfg.style.get("sticker_rate", 1.0))
        has_text = any(b.text for b in p.bubbles)
        if has_text and rate < 1:
            p.bubbles = [b for b in p.bubbles if not b.sticker or random.random() < rate]
        typo_rate = float(self.cfg.style.get("typo_rate", 0.03))
        for i, b in enumerate(p.bubbles):
            reply_to = quote_id if i == 0 else None
            try:
                if b.photo:
                    img = await self.brain.photos.make(b.photo)
                    if img:
                        try:      # 存一份进“相册”，小屋里能看
                            d = self.cfg.root / "data" / "photos"
                            d.mkdir(parents=True, exist_ok=True)
                            (d / f"{int(time.time())}.jpg").write_bytes(img)
                        except OSError:
                            pass
                        m = await out.send_photo(chat, img, reply_to_message_id=reply_to)
                        self.db.add_message("assistant", f"[照片:{b.photo}]", "photo", m.message_id, channel=ch)
                        sent_any = True
                    else:
                        self.db.add_message("event", f"你想发一张照片（{b.photo}），但没发出去")
                    continue
                if b.sticker:
                    await asyncio.sleep(random.uniform(0.6, 1.6))
                    await self._send_sticker(b.sticker, reply_to)
                    self.db.add_message("assistant", f"[表情:{b.sticker}]", "sticker", channel=ch)
                    sent_any = True
                    continue
                if b.voice and b.text and not b.recall:     # 11.2 这条用语音发（念不了就照样发文字）
                    vo = getattr(self.brain, "voice", None)
                    audio = await vo.speak(b.text) if vo is not None and hasattr(out, "send_voice") else None
                    if audio:
                        await asyncio.sleep(min(6.0, len(b.text) / 4) * random.uniform(0.8, 1.2))   # “按住说话”的时间
                        m = await out.send_voice(chat, audio)
                        self.db.add_message("assistant", b.text, "voice", m.message_id, channel=ch)
                        sent_any = True
                        continue
                await self._typing_pause(b.text, first=(i == 0))
                if b.recall:                        # 说出口又后悔了：发出去几秒后撤回
                    m = await out.send_message(chat, b.text, reply_to_message_id=reply_to)
                    await asyncio.sleep(random.uniform(2.5, 5))
                    try:
                        await out.delete_message(chat, m.message_id)
                        self.db.add_message("event", f"你发了「{b.text}」又马上撤回了（他可能看到了）")
                    except Exception as e:  # noqa: BLE001
                        log.info("撤回失败：%s", e)
                        self.db.add_message("assistant", b.text, kind, m.message_id, channel=ch)
                    sent_any = True
                    continue
                typo = self._make_typo(b.text) if random.random() < typo_rate else None
                m = await out.send_message(chat, typo or b.text, reply_to_message_id=reply_to)
                if typo:                            # 发出去的改不了：像真人一样补一句“*正确的字”
                    fix = self._typo_fix(b.text, typo)
                    if fix:
                        await asyncio.sleep(random.uniform(1.2, 3.0))
                        try:
                            await out.send_message(chat, fix)
                        except Exception:  # noqa: BLE001
                            pass
                self.db.add_message("assistant", b.text, kind, m.message_id, channel=ch)
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
                m = await out.send_message(chat, text)
                self.db.add_message("assistant", f"[推荐了一首歌] 《{title}》{('-' + artist) if artist else ''}", "song",
                                    m.message_id, channel=ch)
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

    @staticmethod
    def _typo_fix(text: str, typo: str) -> str | None:
        """打错的那个词 → “*正确的词”。"""
        for k, v in TYPOS.items():
            if k in text and typo == text.replace(k, v, 1):
                return "*" + k
        return None

    async def _send_sticker(self, emotion: str, reply_to: int | None):
        """先用从他那学来的表情包，没有就发 QQ 小黄脸，再没有发一个 emoji。"""
        out, chat, _ = self._out()
        if not chat:
            return
        from ailife.channels.qq.onebot import face_for
        row = self.qq.pick_sticker(emotion) if getattr(self, "qq", None) else None
        if row is not None:
            await out.send_sticker_image(chat, row, reply_to)
            return
        fid = face_for(emotion)
        if fid is not None:
            await out.send_face(chat, fid, reply_to)
            return
        emojis = emotion_to_emojis(emotion)
        if emojis:
            await out.send_message(chat, emojis[0])
