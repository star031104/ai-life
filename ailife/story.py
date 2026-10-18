"""她自己的故事线：一周到一个月的连续事件（期中考试周、社团文化祭彩排、室友过生日……）。

- 同时最多进行 max_active 条；一条结束几天后再开新的。
- 开一条时，模型一次写好每一步（哪天发生什么、怎么结局），存进数据库。
- 她只“知道”今天和以前发生的部分，后面的走向不会提前告诉她——就像真人不知道明天会怎样。
- 每天的日程、日记、聊天都会带上当天的进展；结束后结局写进她自己的记忆。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

log = logging.getLogger("ailife.story")


class Story:
    def __init__(self, cfg, brain):
        c = dict(cfg.get("story") or {})
        self.enabled = bool(c.get("enabled", True))
        self.max_active = int(c.get("max_active", 2))
        self.cfg, self.brain, self.db, self.persona = cfg, brain, brain.db, brain.persona
        self._lock = asyncio.Lock()             # 启动补全和半夜整理可能同时调用：别一下开两条

    @property
    def llm(self):
        return self.brain.llm

    # ---------------- 查询 ----------------
    @staticmethod
    def _split(arc: dict, today: date) -> tuple[list[dict], dict | None]:
        past = [b for b in arc["beats"] if b.get("day", "") < today.isoformat()]
        now = next((b for b in arc["beats"] if b.get("day") == today.isoformat()), None)
        return past, now

    def today_lines(self, today: date) -> list[str]:
        """给日程 / 日记 / 聊天用：每条进行中的故事线到今天为止的进展（不剧透后面）。"""
        out = []
        for arc in self.db.arcs("active"):
            if arc["start_day"] > today.isoformat():
                continue
            past, now = self._split(arc, today)
            seg = f"「{arc['title']}」"
            if past:
                seg += "之前：" + "；".join(b["text"] for b in past[-3:])
            if now:
                seg += f"。今天：{now['text']}"
            out.append(seg)
        return out

    def block(self, today: date) -> str:
        lines = self.today_lines(today)
        recent_done = [a for a in self.db.arcs("done", 3)
                       if a["end_day"] >= (today - timedelta(days=5)).isoformat()]
        for a in recent_done:
            lines.append(f"「{a['title']}」已经结束：{a.get('outcome') or ''}")
        if not lines:
            return ""
        return ("# 你最近自己在经历的事（真实发生在你身上，可以自然地提、分享进展和心情；"
                "以后会怎样你还不知道）\n" + "\n".join(f"- {x}" for x in lines))

    # ---------------- 维护 ----------------
    async def daily(self, today: date, festival: str | None = None):
        """每天整理时调用：结束到期的故事线，必要时开一条新的。"""
        if not self.enabled:
            return
        async with self._lock:
            await self._daily(today, festival)

    def _dedupe(self):
        """以前同时开出来的、几乎一样的两条故事线：留早的那条，取消后一条（不进记忆）。"""
        from .memory import similarity
        act = sorted(self.db.arcs("active"), key=lambda a: a["id"])
        for i, a in enumerate(act):
            for b in act[i + 1:]:
                if similarity(a["title"], b["title"]) >= 0.3:
                    self.db.finish_arc(b["id"], "（和「%s」重复，取消了）" % a["title"])
                    log.info("取消重复的故事线：%s", b["title"])

    async def _daily(self, today: date, festival: str | None = None):
        self._dedupe()
        for arc in self.db.arcs("active"):
            if arc["end_day"] < today.isoformat():
                outcome = arc["beats"][-1]["text"] if arc["beats"] else ""
                self.db.finish_arc(arc["id"], outcome)
                self.db.add_memory(f"我经历了「{arc['title']}」：{outcome}"[:300], 3, "self")
                log.info("故事线结束：%s", arc["title"])
        active = self.db.arcs("active")
        if len(active) >= self.max_active:
            return
        last = self.db.arcs(None, 1)
        if last and last[0]["start_day"] > (today - timedelta(days=3)).isoformat():
            return                          # 刚开过一条，过几天再开
        await self.new_arc(today, festival)

    async def new_arc(self, today: date, festival: str | None = None) -> int | None:
        selfs = "\n".join(f"- {m['content']}" for m in self.db.all_memories("self")[-20:]) or "（无）"
        recent = "；".join(a["title"] for a in self.db.arcs(None, 8)) or "（无）"
        goals = "；".join(g["title"] for g in self.db.goals("active")) if hasattr(self.db, "goals") else ""
        active = "；".join(a["title"] for a in self.db.arcs("active")) or "（无）"
        diaries = "\n".join(f"{d['day']}：{d['content']}" for d in self.db.recent_diary(3)) or "（无）"
        identity = getattr(self.persona, "identity", "") or "人设里的身份"
        prompt = f"""{self.persona.body}
{self.brain.herworld.bible() if hasattr(self.brain, "herworld") else ""}

你在给{self.persona.name}安排接下来一段时间她自己生活里的一条“故事线”。今天是 {today:%Y-%m-%d}（星期{"一二三四五六日"[today.weekday()]}）。{('今天是' + festival + '。') if festival else ''}
她自己的设定（要一致）：
{selfs}
最近的日记：
{diaries}
正在进行的故事线：{active}
以前有过的故事线（不要重复）：{recent}
她正在努力的长期目标（另有安排，故事线换个别的题材，别再写这件事）：{goals or '（无）'}

要求：
- 符合她的身份（{identity}）和季节、日期，比如工作学习里的事、家里的事、朋友的事、学一样新东西、一个小目标。
- 持续 5~21 天，有起伏：开头、几次进展、一两个小波折、一个结局（结局可好可坏，但别太狗血）。
- 每一步是那天具体发生的事（一句话，第一人称），4~8 步，日期从今天或之后开始，按时间顺序。
- 只写她自己的生活，可以有她的朋友、室友、家人出场（优先用上面“你身边的人”里的名字；需要新角色就起普通的名字）。
- 绝对不要写{self.persona.call_name}（她的恋人）做了什么、说了什么，也不要写她和他的互动——他的部分由真实聊天决定。
- 她有恋人：不要写她对别的男生心动、脸红、暧昧之类的情节。
- 标题概括这件事本身（例如“期中考试周”“文化祭彩排”），不要用恋爱相关的标题。
只输出 JSON：{{"title":"<短标题>","beats":[{{"day":"<YYYY-MM-DD>","text":"<那天发生的事>"}}]}}"""
        data = await self.llm.json([{"role": "user", "content": prompt}], default=None, role="night")
        if not isinstance(data, dict) or not data.get("title") or not isinstance(data.get("beats"), list):
            return None
        beats = []
        for b in data["beats"]:
            try:
                d = date.fromisoformat(str(b.get("day")))
            except (TypeError, ValueError, AttributeError):
                continue
            if d < today or d > today + timedelta(days=30) or not str(b.get("text") or "").strip():
                continue
            if self.persona.call_name and self.persona.call_name in str(b["text"]):
                continue                        # 不替他编剧情
            beats.append({"day": d.isoformat(), "text": str(b["text"]).strip()[:120]})
        beats.sort(key=lambda x: x["day"])
        if len(beats) < 2:
            return None
        aid = self.db.add_arc(str(data["title"])[:30], beats[0]["day"], beats[-1]["day"], beats)
        log.info("新的故事线：%s（%s ~ %s，%d 步）", data["title"], beats[0]["day"], beats[-1]["day"], len(beats))
        return aid

    def for_web(self, today: date) -> list[dict]:
        """网页上只显示到今天为止的部分（不剧透）。"""
        out = []
        for a in self.db.arcs(None, 8):
            past = [b for b in a["beats"] if b["day"] <= today.isoformat()]
            out.append({"id": a["id"], "title": a["title"], "status": a["status"], "start": a["start_day"],
                        "end": a["end_day"] if a["status"] == "done" else None,
                        "total": len(a["beats"]), "beats": past, "outcome": a.get("outcome")})
        return out


def now_date(cfg) -> date:
    return datetime.now(cfg.tz).date()
