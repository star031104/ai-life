"""在一起的日常：一起打卡、晨间小报、细心的关怀、每月纪念册。"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta

log = logging.getLogger("ailife.together")

NEG = ("累", "烦", "难过", "难受", "崩溃", "压力", "焦虑", "失眠", "哭", "不开心", "不高兴", "不舒服", "不顺利", "emo",
       "讨厌自己", "没意思", "撑不住", "孤独", "寂寞", "委屈", "生病", "头疼", "胃疼", "郁闷", "心累", "好丧", "丧",
       "自卑", "绝望", "害怕")
POS = ("开心", "高兴", "哈哈", "好耶", "太好了", "爽", "舒服", "喜欢", "笑死", "嘿嘿", "期待", "顺利", "成功")
CRISIS = ("想死", "不想活", "活着没意思", "自杀", "轻生", "伤害自己", "割腕", "结束自己", "活不下去")


def _take(text: str, words) -> tuple[str, list[str]]:
    """最长的词先匹配，匹配到就从句子里挖掉（“不开心”不会再被算成“开心”，“心累”不会再算一次“累”）。"""
    found = []
    for w in sorted(words, key=len, reverse=True):
        if w in text:
            found.append(w)
            text = text.replace(w, "|")
    return text, found


def norm_hhmm(t: str | None) -> str | None:
    """“7:5”“23：30” → “07:05”“23:30”；不合法（25:00、9:99）返回 None。"""
    m = re.fullmatch(r"\s*(\d{1,2})[:：](\d{1,2})\s*", t or "")
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    return f"{hh:02d}:{mm:02d}" if hh < 24 and mm < 60 else None


def habit_day(now: datetime) -> str:
    """凌晨 5 点前打卡算前一天（早睡打卡常常过了零点）。"""
    return (now - timedelta(hours=5)).date().isoformat()


class Together:
    def __init__(self, cfg, brain):
        self.cfg, self.brain, self.db = cfg, brain, brain.db

    # ================= 打卡 =================
    def find_habit(self, name: str):
        name = (name or "").strip()
        for h in self.db.habits():
            if h["name"] == name or name in h["name"] or h["name"] in name:
                return h
        return None

    def do_checkin(self, hid: int) -> tuple[bool, int]:
        day = habit_day(self.brain.now())
        new = self.db.checkin(hid, day)
        return new, self.db.streak(hid, day)

    def habits_block(self) -> str:
        hs = self.db.habits()
        if not hs:
            return ""
        day = habit_day(self.brain.now())
        parts = []
        for h in hs:
            st = self.db.streak(h["id"], day)
            done = self.db.checked(h["id"], day)
            parts.append(f"{h['name']}（{('每天 ' + h['remind_time'] + '，') if h['remind_time'] else ''}"
                         f"已连续 {st} 天，今天{'已打卡' if done else '还没打卡'}）")
        return "他和你约好的打卡目标（你负责监督、坚持了要夸）：" + "；".join(parts)

    def habits_for_web(self) -> list[dict]:
        day = habit_day(self.brain.now())
        out = []
        for h in self.db.habits():
            days = set(self.db.checkin_days(h["id"], 30))
            d0 = date.fromisoformat(day)
            week = [(d0 - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
            out.append({"id": h["id"], "name": h["name"], "time": h["remind_time"], "streak": self.db.streak(h["id"], day),
                        "today": day in days, "week": [{"day": d, "done": d in days} for d in week],
                        "total": len(self.db.checkin_days(h["id"], 1000))})
        return out

    # ================= 他的情绪 =================
    def observe(self, text: str, voice_emotion: str | None = None):
        t = (text or "").lower()
        crisis = any(w in t for w in CRISIS)
        t, neg_w = _take(t, NEG)
        _, pos_w = _take(t, POS)
        pos_w = [w for w in pos_w if not re.search(f"(不|没|别|没有)(太|怎么|那么|很)?{re.escape(w)}", text or "")]
        neg, pos = len(neg_w), len(pos_w)
        if voice_emotion in ("难过", "生气", "害怕"):
            neg += 1
            neg_w.append(f"语气{voice_emotion}")
        elif voice_emotion == "开心":
            pos += 1
        if neg or pos or crisis:
            self.db.add_his_mood(habit_day(self.brain.now()), neg, pos, neg_w, crisis)

    def care_state(self) -> dict:
        rows = self.db.his_moods(4)
        today = habit_day(self.brain.now())
        recent = [r for r in rows if r["day"] >= (date.fromisoformat(today) - timedelta(days=3)).isoformat()]
        low_days = [r for r in recent if r["neg"] >= 2 and r["neg"] > r["pos"]]
        crisis = any(r["crisis"] for r in recent if r["day"] >= (date.fromisoformat(today) - timedelta(days=1)).isoformat())
        words = list(dict.fromkeys(w for r in low_days for w in (r["words"] or "").split("、") if w))[:6]
        return {"care": len(low_days) >= 2 or crisis, "crisis": crisis, "days": len(low_days), "words": words}

    def care_block(self) -> str:
        st = self.care_state()
        if st["crisis"]:
            return ("# 他最近的状态（非常重要）\n他最近说过很消极、甚至想伤害自己的话。现在收起所有玩闹和小脾气，认真、温柔地陪着他，"
                    "先听他说，告诉他你很在乎他；不要说教、不要轻描淡写。温和地鼓励他联系身边信任的人，"
                    "或者找专业的心理援助；如果他有危险，请他马上联系身边的人或当地的紧急求助电话。")
        if st["care"]:
            return (f"# 他这几天的状态\n他这几天情绪一直不太好（{st['days']} 天，提到过：{'、'.join(st['words']) or '心情不好'}）。"
                    "收起捉弄和小脾气，多陪他、多听他说，问问他今天怎么样；别说教、别急着给建议，"
                    "可以分享点轻松的小事让他放松。他愿意的话，也可以鼓励他和身边的朋友聊聊。")
        return ""

    # ================= 晨间小报 =================
    async def morning_brief(self) -> str:
        b = self.brain
        now = b.now()
        items = []
        try:
            w = await b.weather.fetch()
            if w:
                items.append(f"天气：{w['today']}" + ("，要下雨，提醒带伞" if w.get("rain_soon") or "雨" in w["today"] else ""))
        except Exception:  # noqa: BLE001
            pass
        end = now.replace(hour=23, minute=59).timestamp()
        rs = [r for r in self.db.pending_reminders() if r["due_ts"] <= end]
        if rs:
            items.append("今天的提醒：" + "；".join(f"{datetime.fromtimestamp(r['due_ts'], self.cfg.tz):%H:%M} {r['content']}"
                                                for r in rs[:3]))
        from .brain import next_occurrence
        for d in self.db.all_dates():
            nxt = next_occurrence(d["date"], now.date())
            if nxt == now.date():
                items.append(f"今天是{d['name']}")
            elif nxt and (nxt - now.date()).days == 1:
                items.append(f"明天是{d['name']}")
        shows = [s["name"] for s in self.db.active_shows() if s["weekday"] == now.weekday()]
        if shows:
            items.append("今天更新的番：" + "、".join(f"《{x}》" for x in shows))
        hs = [h["name"] for h in self.db.habits() if not self.db.checked(h["id"], habit_day(now))]
        if hs:
            items.append("今天的打卡：" + "、".join(hs))
        threads = [t["topic"] for t in self.db.open_threads(3)]
        if threads:
            items.append("还挂着的事：" + threads[0])
        return "；".join(items)

    # ================= 每月纪念册 =================
    def month_stats(self, y: int, m: int) -> dict:
        t0 = datetime(y, m, 1, tzinfo=self.cfg.tz).timestamp()
        t1 = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=self.cfg.tz).timestamp()
        rows = self.db.messages_between(t0, t1)
        days = {datetime.fromtimestamp(r["ts"], self.cfg.tz).date() for r in rows if r["role"] == "user"}
        q = lambda sql, *a: self.db.conn.execute(sql, a).fetchone()[0]  # noqa: E731
        return {
            "他发的消息": sum(1 for r in rows if r["role"] == "user"),
            "她发的消息": sum(1 for r in rows if r["role"] == "assistant"),
            "聊天的天数": len(days),
            "语音条": sum(1 for r in rows if r["kind"] == "voice"),
            "照片": sum(1 for r in rows if r["kind"] == "photo"),
            "新记住的事": q("SELECT COUNT(*) FROM memories WHERE ts>=? AND ts<?", t0, t1),
            "一起听的歌": q("SELECT COUNT(*) FROM music_log WHERE ts>=? AND ts<?", t0, t1),
            "打卡": q("SELECT COUNT(*) FROM checkins WHERE ts>=? AND ts<?", t0, t1),
            "完成的愿望": q("SELECT COUNT(*) FROM wishes WHERE done_ts>=? AND done_ts<?", t0, t1),
        }

    async def monthly_recap(self, y: int, m: int, save: bool = True) -> tuple[str, dict] | None:
        b = self.brain
        stats = self.month_stats(y, m)
        if stats["他发的消息"] + stats["她发的消息"] == 0:
            return None
        mm = f"{y}-{m:02d}"
        diaries = self.db.diary_between(f"{mm}-01", f"{mm}-31")
        dtxt = "\n".join(f"{d['day'][5:]}：{d['content'][:120]}" for d in diaries[-20:]) or "（这个月没写日记）"
        t0 = datetime(y, m, 1, tzinfo=self.cfg.tz).timestamp()
        t1 = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=self.cfg.tz).timestamp()
        mems = [x["content"] for x in self.db.all_memories() if t0 <= x["ts"] < t1][:15]
        arcs = [a["title"] + "：" + (a.get("outcome") or "进行中") for a in self.db.arcs(None, 10)
                if a["start_day"][:7] == mm or a["end_day"][:7] == mm]
        songs = [f"《{x['title']}》" for x in self.db.music(60) if t0 <= x["ts"] < t1][:6]
        prompt = f"""{b.persona.body}

你是{b.persona.name}。给{b.persona.call_name}写一份“我们的{m}月”纪念册（350 字以内，你自己的口吻，可以分几段）：
回顾这个月你们之间印象最深的几件事、你自己经历的事、你们的小默契，最后说说下个月想和他一起做什么。
不要用 markdown 标题和列表，真诚一点，最后可以有一点你自己的小性格。
这个月的数据：{stats}
你这个月的日记：
{dtxt}
这个月新记住的关于他的事：{'；'.join(mems) or '无'}
你自己的故事线：{'；'.join(arcs) or '无'}
他这个月听的歌：{'、'.join(songs) or '不清楚'}
这个月一起实现的愿望：{'；'.join(w['text'] + ('（' + w['note'] + '）' if w['note'] else '') for w in self.db.wishes_done_between(t0, t1)) or '无'}
你自己的长期目标：{'；'.join(g['title'] + '：' + (g.get('outcome') or '进行中') for g in self.db.goals(None, 4)) or '无'}"""
        try:
            text = (await b.llm.chat([{"role": "user", "content": prompt}], role="night", max_tokens=800)).strip()
        except Exception as e:  # noqa: BLE001
            log.warning("写纪念册失败：%s", e)
            return None
        if save:
            self.db.set_recap(mm, text, stats)
        return text, stats
