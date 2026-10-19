"""她的世界：身边固定的人 + 她自己的长期目标。

为什么要有：故事线、日程、日记里会提到室友、朋友、老师，以前每次都临时起名字，
今天叫“小雨”的室友过几天可能变成“佳佳”。现在：
- 第一次运行时按人设生成 5~7 个常驻的人（名字、关系、一句话），之后所有地方都用这份名单；
- 每天整理时，从她前一天说过的话、日记、故事线里找出新出现的人，记进名单（不是常驻的那种）；
- 她自己有 1~2 个几个月的长期目标（钢琴考级、攒钱买手办、学做菜……），一开始就把每一步安排好，
  她只知道今天和之前的进展；有进展的那天会想跟你分享，你问起来她也答得上来。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta

log = logging.getLogger("ailife.herworld")


def _clean_name(n: str) -> str:
    return re.sub(r"[\s“”\"'（）()【】\[\]]", "", str(n or ""))[:12]


class HerWorld:
    def __init__(self, cfg, brain):
        c = dict(cfg.get("her_world") or {})
        self.enabled = bool(c.get("enabled", True))
        self.max_goals = int(c.get("max_goals", 2))
        self.cfg, self.brain, self.db = cfg, brain, brain.db
        self._lock = asyncio.Lock()             # 启动补全和半夜整理可能同时调用

    @property
    def llm(self):
        return self.brain.llm

    @property
    def persona(self):
        return self.brain.persona

    def _forbidden(self) -> set[str]:
        p = self.persona
        return {x for x in (p.call_name, p.name, self.cfg.telegram.get("owner_name"), "哥哥", "哥哥大人", "他") if x}

    # ================= 身边的人 =================
    def bible(self, query: str = "") -> str:
        """给提示词用：她身边的人（常驻的全给；后来出现的只给聊到的和最近常提的）。"""
        if not self.enabled:
            return ""
        people = self.db.cast_all()
        if not people:
            return ""
        core = [p for p in people if p["core"]][:8]
        others = [p for p in people if not p["core"]]
        shown = [p for p in others if p["name"] in (query or "")]
        recent = sorted((p for p in others if p not in shown and (p["last_ts"] or 0) > time.time() - 14 * 86400),
                        key=lambda p: -(p["mentions"] or 0))
        shown += recent[:max(0, 4 - len(shown))]
        lines = [f"- {p['name']}（{p['role']}）" + (f"：{p['descr']}" if p["descr"] else "") for p in core + shown]
        return ("# 你身边的人（名字和关系是固定的：提到他们时一直用这些名字，别给已有的人换名字，"
                "也别把不在这里的人说成很熟）\n" + "\n".join(lines))

    async def ensure_cast(self) -> int:
        async with self._lock:
            return await self._ensure_cast()

    async def _ensure_cast(self) -> int:
        if not self.enabled or self.db.cast_all(core_only=True):
            return 0
        selfs = "\n".join(f"- {m['content']}" for m in self.db.all_memories("self")[-20:]) or "（无）"
        names_seen = "、".join(sorted(set(re.findall(r"[一-龥]{2,3}", " ".join(
            b["text"] for a in self.db.arcs(None, 5) for b in a["beats"]))))[:40])
        prompt = f"""{self.persona.body}

给{self.persona.name}设定她生活里固定会出现的 5~7 个人（她的室友、最好的朋友、社团里的人、老师、家人……），
以后她的日程、日记、聊天里都会一直用这些名字，所以要符合她的设定、名字普通好记、彼此不重名。
她自己说过的事（要一致，里面出现过的人就用里面的名字）：
{selfs}
不要包括{self.persona.call_name}（她的恋人），不要用动漫/明星的名字。
{self._gender_rule()}
她有恋人：不要设定会和这段感情冲突的人（追求她的人、相亲对象、前任之类）。
只输出 JSON 数组：[{{"name":"<名字或称呼>","role":"<和她的关系，如 室友>","desc":"<一句话特点，20 字内>"}}]"""
        if names_seen:
            prompt += f"\n（以前的故事里出现过这些字，可以参考：{names_seen}）"
        data = await self.llm.json([{"role": "user", "content": prompt}], default=None, role="night")
        if not isinstance(data, list):
            return 0
        bad = self._forbidden()
        n = 0
        for d in data[:8]:
            if not isinstance(d, dict):
                continue
            name = _clean_name(d.get("name"))
            if not name or name in bad or len(name) < 1:
                continue
            if self.db.cast_add(name, str(d.get("role") or ""), str(d.get("desc") or ""), core=True):
                n += 1
        if n:
            log.info("她身边的人定好了：%s", "、".join(p["name"] for p in self.db.cast_all(core_only=True)))
        return n

    async def learn(self, day: date) -> list[str]:
        """从她那天说的话、日记、日程、故事线里找出新出现的人，记进名单。"""
        if not self.enabled:
            return []
        t0 = datetime(day.year, day.month, day.day, tzinfo=self.cfg.tz).timestamp()
        her = [r["content"] for r in self.db.messages_between(t0, t0 + 86400) if r["role"] == "assistant"]
        diary = self.db.get_diary(day.isoformat())
        texts = her + ([diary["content"]] if diary else []) + [self.db.get(f"life_{day.isoformat()}", "") or ""]
        texts += [b["text"] for a in self.db.arcs("active") for b in a["beats"] if b.get("day") == day.isoformat()]
        blob = "\n".join(t for t in texts if t)[-4000:]
        if not blob.strip():
            return []
        known = self.db.cast_all()
        self.db.cast_touch([p["name"] for p in known if p["name"] in blob])
        names = "、".join(p["name"] for p in known) or "（无）"
        prompt = f"""下面是{self.persona.name}（{getattr(self.persona, "identity", "") or "人设里的身份"}）某天自己说的话、日记和日程。
找出里面提到的、她现实生活里的具体的人（室友、同学、老师、家人、朋友、店主……），只要有名字或固定称呼的。
不要：{self.persona.call_name}（她的恋人）、名人、动漫/游戏角色、泛称（“同学们”“老师”这种没有具体指谁的）。
已经记下的人（不用再列）：{names}
文字：
{blob}
只输出 JSON 数组（没有就输出 []）：[{{"name":"<名字或称呼>","role":"<关系>","desc":"<从文字里看到的一句话，20 字内>"}}]"""
        data = await self.llm.json([{"role": "user", "content": prompt}], default=[], role="memory")
        bad = self._forbidden() | {p["name"] for p in known}
        added = []
        for d in (data if isinstance(data, list) else [])[:3]:
            if not isinstance(d, dict):
                continue
            name = _clean_name(d.get("name"))
            if not name or name in bad or name not in blob:       # 必须真的在文字里出现过
                continue
            if self.db.cast_add(name, str(d.get("role") or ""), str(d.get("desc") or "")):
                added.append(name)
        if added:
            log.info("她身边新出现的人：%s", "、".join(added))
        return added

    # ================= 长期目标 =================
    def _gender_rule(self) -> str:
        g = getattr(self.persona, "gender", "female")
        if g == "male":
            return "他是男生：同宿舍 / 合租的室友是男生。"
        if g == "female":
            return "她是女生：宿舍 / 合租的室友都是女生。"
        return ""

    @staticmethod
    def progress(goal: dict, today: date) -> tuple[int, list[dict], dict | None]:
        """(到今天为止的进度, 已经发生的步骤, 今天这一步)"""
        past = [s for s in goal["plan"] if s.get("day", "") <= today.isoformat()]
        now = next((s for s in goal["plan"] if s.get("day") == today.isoformat()), None)
        prog = int(past[-1].get("progress", 0)) if past else 0
        return prog, past, now

    def goals_block(self, today: date) -> str:
        if not self.enabled:
            return ""
        lines = []
        for g in self.db.goals("active"):
            if g["start_day"] > today.isoformat():
                continue
            prog, past, now = self.progress(g, today)
            seg = f"「{g['title']}」（{g['why'] or ''}；{g['start_day'][5:]} 开始，打算 {g['end_day'][5:]} 前完成，现在大概 {prog}%）"
            if past:
                seg += "。最近：" + "；".join(s["text"] for s in past[-2:])
            if now:
                seg += f"。今天：{now['text']}"
            lines.append(seg)
        for g in self.db.goals("done", 3):
            if g["end_day"] >= (today - timedelta(days=7)).isoformat():
                lines.append(f"「{g['title']}」已经结束：{g.get('outcome') or ''}")
        if not lines:
            return ""
        return ("# 你自己的长期目标（你在认真努力的事，他问起来要答得上；可以自然地跟他分享进展，"
                "但别每次都提；结果会怎样你还不知道）\n" + "\n".join(f"- {x}" for x in lines))

    async def new_goal(self, today: date) -> int | None:
        if self._lock.locked() and self.db.goals("active"):
            return None
        async with self._lock:
            if self.db.goals(None, 1) and (time.time() - (self.db.goals(None, 1)[0]["created"] or 0)) < 5 * 86400:
                return None                         # 刚定过（另一处同时在定）
            return await self._new_goal(today)

    async def _new_goal(self, today: date) -> int | None:
        past = "；".join(g["title"] for g in self.db.goals(None, 10)) or "（无）"
        selfs = "\n".join(f"- {m['content']}" for m in self.db.all_memories("self")[-15:]) or "（无）"
        people = "、".join(f"{p['name']}（{p['role']}）" for p in self.db.cast_all(core_only=True)) or "（无）"
        prompt = f"""{self.persona.body}

给{self.persona.name}定一个她自己想在接下来几个月完成的长期目标，并安排好过程。今天是 {today:%Y-%m-%d}。
她自己说过的事（要一致）：
{selfs}
她身边的人（需要时用这些名字）：{people}
以前定过的目标（不要重复）：{past}
她最近在经历的事（另有安排，目标换个别的方向）：{'；'.join(a["title"] for a in self.db.arcs("active")) or '（无）'}

要求：
- 符合她的身份（{getattr(self.persona, 'identity', '') or '见人设'}）和性格、是她自己真心想做的事（例如考一个证、学会做一道拿手菜、攒钱买想要的东西、坚持运动、学一门新技能……）；
- 持续 45~120 天；6~10 步，每一步是某一天发生的具体进展或波折（第一人称一句话），日期从今天或之后开始；
- 每一步带一个当时的进度 0~100，整体往上走，中间可以有一两次卡住或小退步；最后一步是结局（多半做到了，也可以只做到一部分）；
- 不要和{self.persona.call_name}（她的恋人）有关，不要写他做了什么。
- 她有恋人：不要写她对别的男生心动、脸红、暧昧之类的情节。
只输出 JSON：{{"title":"<目标，10 字内>","why":"<为什么想做，20 字内>","plan":[{{"day":"<YYYY-MM-DD>","text":"<那天的进展>","progress":<0-100>}}]}}"""
        data = await self.llm.json([{"role": "user", "content": prompt}], default=None, role="night")
        if not isinstance(data, dict) or not data.get("title") or not isinstance(data.get("plan"), list):
            return None
        plan = []
        for s in data["plan"]:
            try:
                d = date.fromisoformat(str(s.get("day")))
                pr = max(0, min(100, int(s.get("progress", 0))))
            except (TypeError, ValueError, AttributeError):
                continue
            text = str(s.get("text") or "").strip()
            if not text or d < today or d > today + timedelta(days=150):
                continue
            if self.persona.call_name and self.persona.call_name in text:
                continue
            plan.append({"day": d.isoformat(), "text": text[:120], "progress": pr})
        plan.sort(key=lambda x: x["day"])
        if len(plan) < 3:
            return None
        span = (date.fromisoformat(plan[-1]["day"]) - today).days
        if span < 40:                              # 模型常把“几个月”写成几周：按比例拉长到 45 天以上
            k = 50 / max(1, span)
            for st in plan:
                off = (date.fromisoformat(st["day"]) - today).days
                st["day"] = (today + timedelta(days=round(off * k))).isoformat()
        gid = self.db.add_goal(str(data["title"])[:20], str(data.get("why") or "")[:40],
                               plan[0]["day"], plan[-1]["day"], plan)
        log.info("她的新目标：%s（%s ~ %s，%d 步）", data["title"], plan[0]["day"], plan[-1]["day"], len(plan))
        return gid

    def milestone_topics(self, today: date) -> list[tuple[str, str]]:
        """今天有进展、还没跟他说过的目标：[(state key, 话题)]"""
        out = []
        for g in self.db.goals("active"):
            prog, past, now = self.progress(g, today)
            key = f"goal_shared_{g['id']}_{today.isoformat()}"
            if now and not self.db.get(key):
                out.append((key, f"你的目标「{g['title']}」今天有了进展：{now['text']}（现在大概 {prog}%）。"
                                 "想跟他分享一下（开心就炫耀、受挫就小小撒个娇）"))
        for g in self.db.goals("done", 2):
            key = f"goal_done_shared_{g['id']}"
            if g["end_day"] >= (today - timedelta(days=2)).isoformat() and not self.db.get(key):
                out.append((key, f"你坚持了好久的目标「{g['title']}」有结果了：{g.get('outcome') or ''}。告诉他"))
        return out

    # ================= 每天整理 =================
    async def daily(self, today: date):
        if not self.enabled:
            return
        for g in self.db.goals("active"):
            if g["end_day"] < today.isoformat():
                outcome = g["plan"][-1]["text"] if g["plan"] else ""
                self.db.finish_goal(g["id"], outcome)
                self.db.add_memory(f"我花了好久做「{g['title']}」：{outcome}"[:300], 4, "self")
                log.info("目标结束：%s", g["title"])
        for step, fn in (("身边的人", self.ensure_cast), ("新出现的人", lambda: self.learn(today - timedelta(days=1)))):
            try:
                await fn()
            except Exception as e:  # noqa: BLE001
                log.warning("整理%s失败：%s", step, e)
        active = self.db.goals("active")
        last = self.db.goals(None, 1)
        recent_new = last and (time.time() - (last[0]["created"] or 0)) < 5 * 86400
        # 至少有一个；第二个等第一个做了 20 天以后再开；两次新目标至少隔 5 天
        want = not active or (len(active) < self.max_goals and
                              time.time() - min(g["created"] or 0 for g in active) > 20 * 86400)
        if want and not recent_new:
            try:
                await self.new_goal(today)
            except Exception as e:  # noqa: BLE001
                log.warning("定新目标失败：%s", e)

    # ================= 网页 =================
    def for_web(self, today: date) -> dict:
        goals = []
        for g in self.db.goals(None, 6):
            prog, past, _ = self.progress(g, today)
            goals.append({"id": g["id"], "title": g["title"], "why": g["why"], "status": g["status"],
                          "start": g["start_day"], "end": g["end_day"], "progress": prog if g["status"] == "active" else
                          (g["plan"][-1]["progress"] if g["plan"] else 100),
                          "steps": past, "outcome": g.get("outcome") if g["status"] == "done" else None})
        people = [{"id": p["id"], "name": p["name"], "role": p["role"], "desc": p["descr"], "core": bool(p["core"])}
                  for p in self.db.cast_all()]
        return {"goals": goals, "people": people}
