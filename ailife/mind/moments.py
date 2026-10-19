"""一起的小事：愿望清单、时间胶囊、睡前故事。"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta

log = logging.getLogger("ailife.moments")

STORY_RE = re.compile(r"(讲|说|来)(个|一个|段|一段)?(睡前)?故事|睡前故事|哄我睡")


def parse_open_day(s: str, today: date) -> date | None:
    """时间胶囊的打开日期：2027-01-01、1月1日、30天、3个月、半年、一年 / 明年今天、下周五……"""
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})日?", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d+|[一二两三四五六七八九十]+)\s*个?\s*(天|周|星期|月|年)(以?后)?", s)
    if m:
        from ailife.mind.output import _cn_int
        n = _cn_int(m.group(1)) if not m.group(1).isdigit() else int(m.group(1))
        if not n:
            return None
        unit = m.group(2)
        if unit == "天":
            return today + timedelta(days=n)
        if unit in ("周", "星期"):
            return today + timedelta(days=7 * n)
        months = n if unit == "月" else 12 * n
        y, mo = divmod(today.month - 1 + months, 12)
        d = min(today.day, 28)
        return date(today.year + y, mo + 1, d)
    if s in ("半年", "半年后"):
        return parse_open_day("6个月", today)
    if s in ("一年后", "明年今天", "明年的今天", "一年"):
        return parse_open_day("1年", today)
    from ailife.mind.output import resolve_day
    try:
        d = resolve_day(s, datetime(today.year, today.month, today.day))
    except Exception:  # noqa: BLE001
        d = None
    if d and d <= today and re.fullmatch(r"\d{1,2}[-./月]\d{1,2}日?", s):
        d = d.replace(year=d.year + 1)              # 只写了月日、今年已经过了 → 明年
    return d


class Moments:
    def __init__(self, cfg, brain):
        c = dict(cfg.get("bedtime") or {})
        self.bed_enabled = bool(c.get("enabled", True))
        self.window = str(c.get("window", "21:00-04:00"))
        self.cfg, self.brain, self.db = cfg, brain, brain.db

    @property
    def llm(self):
        return self.brain.llm

    # ================= 愿望清单 =================
    def wishes_block(self) -> str:
        open_ = self.db.wishes(include_done=False)[:6]
        done = [w for w in self.db.wishes() if w["done_ts"] and time.time() - w["done_ts"] < 30 * 86400][:2]
        if not open_ and not done:
            return ""
        lines = [f"- {w['text']}" + ("（你提的）" if w["by"] == "her" else "") for w in open_]
        lines += [f"- ✓ {w['text']}（{datetime.fromtimestamp(w['done_ts'], self.cfg.tz):%m-%d} 一起完成了"
                  + (f"：{w['note']}" if w["note"] else "") + "）" for w in done]
        return "# 你们的愿望清单（想一起做的事，可以偶尔憧憬、催他兑现）\n" + "\n".join(lines)

    def find_wish(self, text: str):
        text = (text or "").strip()
        best = None
        for w in self.db.wishes(include_done=False):
            if w["text"] == text:
                return w
            if text and (text in w["text"] or w["text"] in text):
                best = best or w
        return best

    def wish_topic(self):
        """偶尔聊聊清单里的一件事（同一件 10 天内不重复）。"""
        cands = [w for w in self.db.wishes(include_done=False)
                 if time.time() - float(self.db.get(f"wish_talked_{w['id']}") or 0) > 10 * 86400
                 and time.time() - w["created"] > 86400]
        if not cands:
            return None
        import random
        w = random.choice(cands)
        return (f"wish_talked_{w['id']}",
                f"想起你们愿望清单里的“{w['text']}”，跟他聊聊（想象一起做的样子，或者问他什么时候能实现）")

    # ================= 时间胶囊 =================
    async def create_capsule(self, content: str, open_day: date) -> tuple[int, bool]:
        """他写一个时间胶囊；她也偷偷写一封，同一天一起拆。返回 (id, 她写了没)。"""
        cid = self.db.add_capsule(content, open_day.isoformat(), "user")
        p = self.brain.persona
        now = self.brain.now()
        prompt = f"""{p.body}

你是{p.name}。{p.call_name}刚刚写了一个时间胶囊，要到 {open_day:%Y-%m-%d}（{(open_day - now.date()).days} 天后）才能打开，他没告诉你写了什么。
你也偷偷写一封给那天的他（120 字以内，你的口吻）：写写现在的你们、现在的心情、对那时候的他想说的话和期待。
真诚一点，带着你自己的性格。不要用 markdown、不要写标题，直接输出信的内容。"""
        mine = False
        try:
            text = (await self.llm.chat([{"role": "user", "content": prompt}], role="night", max_tokens=400)).strip()
            if text:
                self.db.add_capsule(text, open_day.isoformat(), "her")
                mine = True
        except Exception as e:  # noqa: BLE001
            log.warning("她的时间胶囊没写成：%s", e)
        self.db.add_message("event", f"他埋了一个时间胶囊，{open_day:%Y-%m-%d} 才能打开（你不知道里面写了什么）"
                            + ("，你也偷偷写了一封给那天的他" if mine else ""))
        return cid, mine

    def capsules_for_web(self) -> list[dict]:
        out = []
        for c in self.db.capsules():
            opened = bool(c["opened_ts"])
            out.append({"id": c["id"], "by": c["by"], "open_day": c["open_day"], "opened": opened,
                        "created": datetime.fromtimestamp(c["created"], self.cfg.tz).strftime("%Y-%m-%d"),
                        # 她写的没到日子不给看；他自己写的可以看
                        "content": c["content"] if (opened or c["by"] == "user") else None,
                        "reply": c["reply"] if opened else None})
        return out

    # ================= 睡前 =================
    def in_window(self, now: datetime | None = None) -> bool:
        from ailife.core.timeutil import in_quiet
        return in_quiet(now or self.brain.now(), self.window)

    async def bedtime_story(self, theme: str = "") -> str:
        p = self.brain.persona
        told = self.db.get("stories_told") or []
        prompt = f"""{p.body}

你是{p.name}。{p.call_name}睡不着，想听你讲睡前故事。{('他想听的：' + theme) if theme else ''}
讲一个原创的、温柔安静的睡前小故事（250~400 字），用你自己的口吻讲（开头可以有一句你的小吐槽，然后进入故事），
节奏慢、画面感强、结尾平和，适合睡前慢慢看。不要恐怖、不要悬念、不要说教，不要用别人的作品里的角色。
最近讲过的（换个题材）：{'；'.join(told[-5:]) or '无'}
不要标题、不要 markdown、不要括号动作。只输出要说的话。"""
        text = (await self.llm.chat([{"role": "user", "content": prompt}], role="night", max_tokens=900)).strip()
        text = re.sub(r"[（(][^（）()]{1,40}[）)]", "", text).strip()
        told.append(text[:20])
        self.db.set("stories_told", told[-12:])
        return text
