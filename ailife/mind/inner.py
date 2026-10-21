"""她的“心”：内心独白，和每周整理一次的“对你的了解”。

内心独白：清醒的时候每隔几小时，她在后台想一想——今天的事、你最近怎么样、要不要找你、找你说什么。
想法会记下来（小屋里能看到），下一次聊天时她也记得自己刚才在想什么；
如果她决定找你，主动消息就是从这个想法出发的，而不是随机抽话题。

对你的了解：每周一次，把关于你的零散记忆、这周的聊天、她的日记整理成一段整体印象
（近况、性格习惯、最近的情绪、你在乎的事、和你相处要注意什么），聊天时作为背景。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

log = logging.getLogger("ailife.mind")


class Mind:
    def __init__(self, cfg, brain):
        c = dict(cfg.get("mind") or {})
        self.enabled = bool(c.get("enabled", True))
        self.every_h = float(c.get("think_every_hours", 3))
        self.cfg, self.brain, self.db = cfg, brain, brain.db

    # ================= 内心独白 =================
    def due(self) -> bool:
        last = self.db.get("think_last_ts", 0) or 0
        return self.enabled and time.time() - last >= self.every_h * 3600

    def recent_block(self) -> str:
        ths = [t for t in self.db.thoughts(3) if time.time() - t["ts"] < 6 * 3600]
        if not ths:
            return ""
        t = ths[-1]
        when = datetime.fromtimestamp(t["ts"], self.cfg.tz)
        return f"# 你 {when:%H:%M} 时心里在想（只是你的想法，别直接念出来）\n{t['thought']}"

    async def think(self) -> dict | None:
        b = self.brain
        now = b.now()
        self.db.set("think_last_ts", time.time())
        hist = b.history(16)
        convo = "\n".join(f"{'他' if m['role'] == 'user' else '我'}：{m['content'][:120]}" for m in hist[-12:]) or "（最近没聊）"
        last_user = self.db.get("last_user_ts", 0) or 0
        gap = (time.time() - last_user) / 3600 if last_user else 99
        prof = self.db.latest_profile()
        prev = "\n".join(f"[{datetime.fromtimestamp(t['ts'], self.cfg.tz):%m-%d %H:%M}] {t['thought']}"
                         for t in self.db.thoughts(3)) or "（无）"
        story = b.story.block(now.date()) if getattr(b, "story", None) else ""
        prompt = f"""{b.persona.body}
{b.herworld.bible() if hasattr(b, "herworld") else ""}

你是{b.persona.name}。现在是 {now:%m-%d %H:%M}（星期{"一二三四五六日"[now.weekday()]}）。一个人的时候，你在心里想一想。
你今天的安排：{self.db.get(f"life_{now:%Y-%m-%d}", "") or '普通的一天'}
{story}
你的心情：{b.mood}；{b.emotion.describe()}
他上次找你是 {gap:.1f} 小时前；你主动找他还没回的次数：{self.db.get("unanswered", 0)}
{b._now_block()}
你对他的整体印象：{prof['content'][:400] if prof else '（还没整理）'}
你们没聊完的事：{"；".join(t["topic"] for t in self.db.open_threads(4)) or '无'}
你们最近的聊天：
{convo}
你之前的想法：
{prev}

先用第一人称写 2~4 句此刻真实的内心独白（具体、有情绪，可以想自己的事，也可以想他，不要重复之前的想法）。
然后决定现在要不要主动找他：考虑他此刻方不方便（在忙、在睡、刚聊过就别打扰）、你有没有真的想说的话。大多数时候不用找。
只输出 JSON：{{"thought":"<内心独白>","mood":"<几个字的心情>","contact":<true 或 false>,"why":"<为什么找/不找>","say":"<如果找他，想说的大意>"}}"""
        data = await b.llm.json([{"role": "user", "content": prompt}], default=None, role="night")
        if not isinstance(data, dict) or not str(data.get("thought") or "").strip():
            return None
        contact = data.get("contact") in (True, "true", "True", 1, "是")
        data["contact"] = contact
        data["id"] = self.db.add_thought(str(data["thought"]).strip()[:400], str(data.get("mood") or "")[:20],
                                         contact, str(data.get("why") or "")[:200])
        if data.get("mood"):
            b.emotion.on_mood_text(str(data["mood"]))
        log.info("她在想：%s%s", str(data["thought"])[:60], "（想找你）" if contact else "")
        return data

    # ================= 对他的了解 =================
    def profile_block(self) -> str:
        p = self.db.latest_profile()
        return f"# 你对他的整体了解（{datetime.fromtimestamp(p['ts'], self.cfg.tz):%m-%d} 整理，作为背景）\n{p['content']}" if p else ""

    def profile_due(self, days: float = 7) -> bool:
        p = self.db.latest_profile()
        enough = len(self.db.all_memories("user")) >= 3
        return enough and (not p or time.time() - p["ts"] >= days * 86400)

    async def update_profile(self) -> str | None:
        b = self.brain
        mems = "\n".join(f"- {m['content']}" for m in self.db.all_memories("user")[-60:])
        week = self.db.messages_between(time.time() - 7 * 86400, time.time())
        his = "\n".join(r["content"][:100] for r in week if r["role"] == "user")[-4000:] or "（这周他说得不多）"
        diaries = "\n".join(f"{d['day']}：{d['content']}" for d in self.db.recent_diary(7)) or "（无）"
        old = self.db.latest_profile()
        artists = "、".join(a for a, _ in self.db.top_artists()) or "（不清楚）"
        prompt = f"""你是{b.persona.name}。整理一下你对{b.persona.call_name}（他）的整体了解，用你自己的口吻（第三人称称呼他），300 字以内，分这几块，每块一两句：
近况：他最近在忙什么、经历了什么
性格和习惯：作息、说话方式、爱好（他常听：{artists}）
最近的情绪：这一周他的状态
他在乎的事：人、目标、担心的事
他身边的人：他最近提到过的家人、朋友、同学、同事（称呼就行）和他们最近怎么样；这周几乎没提到身边的人，也照实写一句（20.0）
和他相处要注意：什么话题他喜欢、什么会让他不开心

上一次的整理（有变化就更新，没变化的保留）：
{old['content'] if old else '（第一次整理）'}
你记得的关于他的事：
{mems or '（无）'}
这一周他说过的话：
{his}
你这周的日记：
{diaries}

只输出整理好的内容，不要标题以外的多余话。"""
        try:
            text = (await b.llm.chat([{"role": "user", "content": prompt}], role="night", max_tokens=700)).strip()
        except Exception as e:  # noqa: BLE001
            log.warning("整理对他的了解失败：%s", e)
            return None
        if text:
            self.db.add_profile(text)
            log.info("已更新对他的了解")
        return text
