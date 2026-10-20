"""把 QQ 接到她身上。

- 私聊：只认 qq.owner_id（你自己的 QQ 号）。他的话写进聊天记录，再交给 chat/companion.py 回复；
  “/命令”包成 chat/command.py 的 Command 交给 Companion
- 群聊不在这里：QQ 群里的角色是单独的程序 ai-life-group（https://github.com/star031104/ai-life-group），
  用另一个 QQ 小号。这里收到的群消息一律不理
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import random
import time
from pathlib import Path

from ailife.channels.qq.onebot import OneBot, QQOut, fetch_bytes, flatten

log = logging.getLogger("ailife.qq")


class QQ:
    def __init__(self, cfg, bot):
        self.cfg, self.bot = cfg, bot
        self.brain, self.db = bot.brain, bot.db
        c = cfg.get("qq") or {}
        self.c = c
        self.owner_id = int(c.get("owner_id") or 0)
        self.private_on = bool(c.get("private", True)) and bool(self.owner_id)
        self.ob = OneBot(str(c.get("ws_url") or "ws://127.0.0.1:3001"), str(c.get("access_token") or ""))
        self.out = QQOut(self.ob)
        self.ob.handlers.append(self.on_event)
        self.ob.on_connect.append(self._check_friend)
        self._strangers_warned = False
        self.last_event_ts = 0.0
        self.is_friend: bool | None = None

    def private_ready(self) -> bool:
        return self.private_on and self.ob.ready

    def start(self):
        if not self.owner_id:
            log.warning("config 里 qq.owner_id 没填：QQ 私聊不会理任何人（群聊不受影响）")
        self.ob.start()
        self._sig_task = asyncio.create_task(self._signature_loop())
        from ailife.channels.qq.napcat_launcher import ensure_napcat
        self._nc_task = asyncio.create_task(ensure_napcat(self.cfg, self.ob))     # NapCat 没开就帮你开

    async def stop(self):
        t = getattr(self, "_sig_task", None)
        if t:
            t.cancel()
        await self.ob.stop()

    async def _check_friend(self):
        """QQ 不能主动给非好友发私聊：她的小号必须加你为好友，不然她只能回、不能主动找你。"""
        if not self.private_on:
            return
        try:
            friends = await self.ob.call("get_friend_list")
        except Exception as e:  # noqa: BLE001
            log.info("取好友列表失败：%s", e)
            return
        ids = {int(x.get("user_id") or 0) for x in (friends if isinstance(friends, list) else [])}
        self.is_friend = self.owner_id in ids
        if not self.is_friend:
            log.warning("她的 QQ 小号还没加你（%s）为好友：她能回你，但不能主动找你。用你的 QQ 加她，会自动同意",
                        self.owner_id)

    # ---------------- 个性签名：跟着她的一天和心情变 ----------------
    async def _signature_loop(self):
        if not self.c.get("signature", True):
            return
        while True:
            try:
                await asyncio.sleep(600)
                if self.ob.ready:
                    await self.maybe_update_signature()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.info("更新签名出错：%s", e)

    async def maybe_update_signature(self, force: bool = False) -> str | None:
        b = self.brain
        now = b.now()
        st = self.db.get("qq_signature") or {}
        from ailife.core.timeutil import in_quiet
        if not force:
            if now.hour < 8 or in_quiet(now, (self.cfg.get("proactive") or {}).get("quiet_hours")):
                return None
            same_day = st.get("day") == f"{now:%Y-%m-%d}"
            mood_changed = st.get("mood") != b.mood and time.time() - float(st.get("ts", 0)) > 4 * 3600
            if same_day and not mood_changed:
                return None
        life = self.db.get(f"life_{now:%Y-%m-%d}", "")
        p = b.persona
        prompt = (f"你是{p.name}。根据你今天的安排和此刻的心情，写一句 QQ 个性签名。\n"
                  f"今天的安排：{life or '普通的一天'}\n此刻心情：{b.mood}；{b.emotion.describe()}\n"
                  "要求：像真人随手写的签名，15 个字以内；可以是心情、在做的事、一句碎碎念；"
                  "不要提到他，不要引号、标签、表情符号以外的符号说明。只输出签名本身。")
        line = (await b.llm.chat([{"role": "user", "content": prompt}], role="helper", background=True)).strip()
        line = line.strip("“”\"' ").splitlines()[0][:30] if line else ""
        if not line:
            return None
        await self.ob.call("set_self_longnick", longNick=line)
        self.db.set("qq_signature", {"day": f"{now:%Y-%m-%d}", "mood": b.mood, "ts": time.time(), "text": line})
        self.db.add_message("event", f"你把 QQ 个性签名改成了：「{line}」（他问起来就自然地说）")
        log.info("她的 QQ 签名：%s", line)
        return line

    def status_line(self) -> str:
        """给 /diag 用。"""
        if not self.ob.ready:
            return f"QQ ❌ 没连上 NapCat（{self.ob.url}）"
        parts = [f"QQ ✅ 小号 {self.ob.self_id}"]
        if self.private_on and self.is_friend is False:
            parts.append(f"⚠️ 还不是 {self.owner_id} 的好友（她不能主动找你）")
        if self.private_on:
            gap = time.time() - self.last_event_ts if self.last_event_ts else None
            parts.append(f"私聊认 {self.owner_id}" + (f"，你上次在 QQ 说话是 {int(gap // 60)} 分钟前" if gap else ""))
        else:
            parts.append("私聊没开（qq.owner_id 没填？）")
        return "，".join(parts)

    async def on_event(self, ev: dict):
        pt = ev.get("post_type")
        if pt == "notice":
            await self.on_notice(ev)
            return
        if pt == "request":
            await self.on_request(ev)
            return
        if pt != "message":
            return
        if ev.get("message_type") == "group":
            return                                   # 群聊交给 ai-life-group
        if ev.get("message_type") == "private":
            await self.on_private(ev)

    # ---------------- 私聊 ----------------
    async def _quote(self, reply_id: int | None) -> str:
        if not reply_id:
            return ""
        row = self.db.conn.execute(
            "SELECT role, content FROM messages WHERE tg_msg_id=? AND channel='qq' ORDER BY id DESC LIMIT 1",
            (int(reply_id),)).fetchone()
        if row:
            who = "你说的" if row["role"] == "assistant" else "他自己之前说的"
            text = row["content"]
        else:                                          # 很早以前的消息：问 NapCat 要原文
            try:
                d = await self.ob.call("get_msg", message_id=int(reply_id))
                text = flatten(d.get("message"), self.ob.self_id).text
                who = "你说的" if int(d.get("user_id") or d.get("sender", {}).get("user_id") or 0) == self.ob.self_id \
                    else "他自己之前说的"
            except Exception:  # noqa: BLE001
                return "[引用了一条消息] "
        text = (text or "").replace("\n", " ").strip()
        return f"[引用了{who}「{text[:60]}{'…' if len(text) > 60 else ''}」] "

    def _user_in(self, content: str, kind: str, qq_mid) -> int:
        bot = self.bot
        mid = self.db.add_message("user", content, kind, qq_mid, channel="qq")
        self.db.set("last_channel", "qq")           # 他在 QQ 说话了：之后回复、主动消息都发 QQ
        bot._last_user_msg_id = qq_mid
        bot._user_active()
        return mid

    def _is_owner_private(self, uid: int) -> bool:
        if self.private_on and uid == self.owner_id:
            return True
        if not self._strangers_warned and uid and uid != self.owner_id:
            log.info("QQ 私聊：%s 不是 qq.owner_id，不理（以后同样的情况不再提示）", uid)
            self._strangers_warned = True
        return False

    async def on_private(self, ev: dict):
        uid = int(ev.get("user_id") or 0)
        if not self._is_owner_private(uid):
            return
        self.last_event_ts = time.time()
        f = flatten(ev.get("message"), self.ob.self_id)
        qmid = ev.get("message_id")
        prefix = await self._quote(f.reply_id)
        bot, brain = self.bot, self.brain
        text = f.text
        if text.startswith(("/", "／")):
            await self.command(text, qmid)
            return
        if f.reply_id and text.strip() in self.DISLIKE:      # 引用她的话回一个 👎：这句不对味，记下来以后避开
            row = self.db.conn.execute(
                "SELECT content FROM messages WHERE tg_msg_id=? AND channel='qq' AND role='assistant' "
                "ORDER BY id DESC LIMIT 1", (int(f.reply_id),)).fetchone()
            if row:
                brain.quality.dislike(row["content"], "👎")
                brain.emotion.nudge(joy=-3, sulk=+4)
                self.db.add_message("event", f"他引用了你说的「{row['content'][:30]}」回了个 👎（看起来他不喜欢这句）",
                                    channel="qq")
                return

        if f.record:                                  # 语音功能已去掉：她听不了，只知道他发了一条
            from ailife.chat.companion import VOICE_IN
            self._user_in(prefix + VOICE_IN, "text", qmid)
            await bot.schedule_reply()
            return

        if f.stickers and not f.images:               # 表情包：学下来以后自己也用；看懂它是什么情绪
            async def feel():
                meaning = await self.learn_sticker(f.stickers[0])
                rest = text.replace("[表情包]", "").strip()
                return (f"[发了一个表情包{('：' + meaning) if meaning else ''}] {rest}").strip()
            self._later(prefix, "sticker", text.replace("[表情包]", "[发了一个表情包]"), feel, qmid)
            return

        if f.images and brain.llm.vision:            # 图片：后台让看图模型看
            cap = text.replace("[图片]", "").replace("[表情包]", "").strip()

            async def look():
                data = await fetch_bytes(f.images[0])
                if not data:
                    raise RuntimeError("下载不了")
                desc = await brain.llm.describe_image(base64.b64encode(data).decode(), cap)
                return f"[发了一张图片，图里是：{desc}] {cap}".strip()
            self._later(prefix, "photo", f"[发了一张图片，你正在看] {cap}".strip(), look, qmid)
            return

        if f.forward_id:                              # 合并转发：把里面的聊天记录读出来
            async def read_fwd():
                return await self.read_forward(f.forward_id)
            self._later(prefix, "text", "[转发了一段聊天记录，你正在看]", read_fwd, qmid)
            return

        if not text:
            return
        brain.together.observe(text)
        brain.rhythm.note_birthday_wish(text)
        links = brain.links
        from ailife.media.links import find_links
        if links.enabled and (find_links(text) or f.links):
            async def read():
                extra = await links.enrich(text)
                for v in links.take_videos():                   # 11.4 B 站视频：点开看（字幕、弹幕、热评），看完跟他说两句
                    bot.spawn(bot.watch_video(v))
                return text + ("\n" + extra if extra else "")
            self._later(prefix, "text", text + "\n[他发了个链接，你正在点开看]", read, qmid)
            return
        self._user_in(prefix + text, "poke" if f.poke else "text", qmid)
        await bot.schedule_reply()

    async def read_forward(self, fid: str, limit: int = 20) -> str:
        d = await self.ob.call("get_forward_msg", id=fid)
        msgs = d.get("messages") or d.get("message") or []
        lines = []
        for m in msgs[:limit]:
            sender = m.get("sender") or {}
            name = sender.get("card") or sender.get("nickname") or "某人"
            content = m.get("message") or m.get("content") or []
            t = flatten(content, None).text
            if t:
                lines.append(f"{name}：{t[:100]}")
        more = f"\n……（后面还有 {len(msgs) - limit} 条）" if len(msgs) > limit else ""
        if not lines:
            return "[转发了一段聊天记录，但你没打开]"
        return "[转发了一段聊天记录给你看]\n" + "\n".join(lines) + more

    # ---------------- 戳一戳、撤回、加好友 ----------------
    async def on_notice(self, ev: dict):
        nt, sub = ev.get("notice_type"), ev.get("sub_type")
        if ev.get("group_id"):
            return
        uid = int(ev.get("user_id") or ev.get("sender_id") or 0)
        if nt == "notify" and sub == "poke":
            target = int(ev.get("target_id") or 0)
            if uid != self.owner_id or (self.ob.self_id and target != self.ob.self_id) or not self.private_on:
                return
            self._user_in("[戳了戳你]", "poke", None)
            if random.random() < float(self.c.get("poke_back", 0.4)):    # 戳回去
                async def back():
                    await asyncio.sleep(random.uniform(1.5, 4))
                    try:
                        await self.out.poke(self.owner_id)
                        self.db.add_message("event", "你也戳了戳他", channel="qq")
                    except Exception as e:  # noqa: BLE001
                        log.debug("戳回去失败：%s", e)
                self.bot.spawn(back())
            await self.bot.schedule_reply()
            return
        if nt == "friend_recall" and uid == self.owner_id and self.private_on:
            mid = ev.get("message_id")
            row = self.db.conn.execute(
                "SELECT id, content FROM messages WHERE tg_msg_id=? AND channel='qq' AND role='user' "
                "ORDER BY id DESC LIMIT 1", (int(mid or 0),)).fetchone()
            if not row:
                return
            self.db.add_message("event", f"他撤回了一条消息，你已经看到了，原话是：「{row['content'][:100]}」"
                                         "（想逗他就提一句，觉得他不想让你看到就装没看见）", channel="qq")
            if random.random() < 0.5:
                await self.bot.schedule_reply(3.0)

    async def on_request(self, ev: dict):
        """他加她（小号）为好友：自动同意。别人加：不理。"""
        if ev.get("request_type") != "friend":
            return
        uid = int(ev.get("user_id") or 0)
        if uid != self.owner_id:
            log.info("有人（%s）加她的 QQ 好友，不是你，没理", uid)
            return
        try:
            await self.ob.call("set_friend_add_request", flag=ev.get("flag"), approve=True)
            log.info("你加她的 QQ 好友：已自动同意")
        except Exception as e:  # noqa: BLE001
            log.warning("自动同意好友申请失败：%s", e)

    # ---------------- 表情包：从他那学，自己也用 ----------------
    def _sticker_dir(self) -> Path:
        d = Path(self.cfg.root) / "data" / "qq_stickers"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def learn_sticker(self, url: str) -> str:
        """下载他发的表情包存起来，让看图模型说出它是什么情绪。返回情绪（看不出来就空）。"""
        data = await fetch_bytes(url, limit=3 * 1024 * 1024)
        if not data:
            return ""
        key = hashlib.sha1(data).hexdigest()[:20]
        fid = f"qq:{key}"
        path = self._sticker_dir() / key
        if not path.exists():
            path.write_bytes(data)
        self.db.add_sticker(fid, fid, None, "qq", "user")
        row = self.db.conn.execute("SELECT * FROM stickers WHERE file_id=?", (fid,)).fetchone()
        if row and row["emotion"]:
            return row["emotion"]
        if not self.brain.llm.vision or (row and row["label_tried"] >= 3):
            return ""
        from ailife.chat.common import EMOTION_LABELS
        try:
            from ailife.media.ffmpeg import ffmpeg
            png = await ffmpeg(["-i", "pipe:0", "-frames:v", "1", "-vf", "scale=256:-1", "-f", "image2",
                                "-vcodec", "png", "pipe:1"], data)        # 动图取第一帧
        except Exception:  # noqa: BLE001
            png = data
        try:
            ans = await self.brain.llm.label_image(
                base64.b64encode(png).decode(),
                "这是一个聊天表情包。它表达的情绪最接近下面哪一个？只回答一个词：" + "、".join(EMOTION_LABELS))
        except Exception as e:  # noqa: BLE001
            log.info("表情包没看懂：%s", e)
            self.db.set_sticker_emotion(fid, None)
            return ""
        label = next((w for w in EMOTION_LABELS if w in (ans or "")), None)
        self.db.set_sticker_emotion(fid, label)
        return label or ""

    def pick_sticker(self, emotion: str) -> bytes | None:
        rows = self.db.stickers_with_emotion(emotion, qq=True)
        random.shuffle(rows)
        for r in rows:
            p = self._sticker_dir() / r["file_id"][3:]
            if p.is_file():
                try:
                    return p.read_bytes()
                except OSError:
                    continue
        return None

    DISLIKE = {"👎", "踩", "[表情:踩]", "这句不对味", "不像你", "出戏"}

    # ---------------- 命令（/diary /memory …）：交给 Companion 的 cmd_xxx ----------------
    ALIASES = {"日记": "diary", "记忆": "memory", "状态": "status", "心情": "status", "小屋": "home", "帮助": "help",
               "照片": "photo", "信": "letter", "愿望": "wish", "打卡": "habit", "纪念册": "recap", "自检": "diag",
               "她在想什么": "mind", "故事": "story", "身边的人": "people", "目标": "goals", "推歌": "song"}

    async def command(self, text: str, qmid=None):
        from ailife.chat.command import Command
        name, c = Command.parse(text, reply_fn=self._say, message_id=qmid)
        if not name:
            return
        name = self.ALIASES.get(name, name)
        if name == "help":
            from ailife.chat.common import HELP
            lines = [f"/{n}  {d}" for n, d in HELP]
            await self._say("命令（也可以用中文：/日记 /记忆 /小屋 /心情 /打卡 …）\n" + "\n".join(lines))
            return
        try:
            if not await self.bot.run_command(c, name):
                await self._say(f"没有 /{name} 这个命令，发 /help 看看有哪些")
        except Exception as e:  # noqa: BLE001
            log.warning("命令 /%s 出错：%s", name, e)
            await self._say(f"/{name} 没执行成功（{str(e)[:60]}）")

    async def _say(self, text: str):
        try:
            await self.out.send_message(self.owner_id, text)
        except Exception as e:  # noqa: BLE001
            log.info("QQ 回复命令失败：%s", e)

    def _later(self, prefix: str, kind: str, placeholder: str, work, qmid=None):
        """同 bot_media._later：先记占位，后台处理完改成真正内容再回。"""
        mid = self._user_in(prefix + placeholder, kind, qmid)

        async def job():
            try:
                content = await work()
            except Exception as e:  # noqa: BLE001
                log.warning("处理他 QQ 发的%s失败：%s", kind, e)
                content = placeholder.replace("正在看", "你没看清").replace("正在听", "你没听清")
            self.db.update_message(mid, prefix + content)
            await self.bot.schedule_reply()
        self.bot.spawn(job())
        return mid


def setup(cfg, bot) -> QQ | None:
    """config 里 qq.enabled 打开时启动 QQ。"""
    c = cfg.get("qq") or {}
    if not c.get("enabled"):
        return None
    try:
        import aiohttp  # noqa: F401  （只检查装没装）
    except ImportError:
        log.error("QQ 需要 aiohttp（pip install aiohttp）")
        return None
    q = QQ(cfg, bot)
    bot.qq = q
    q.start()
    return q


async def wait_ready(q: QQ, timeout: float = 5.0) -> bool:
    try:
        await asyncio.wait_for(q.ob.connected.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False
