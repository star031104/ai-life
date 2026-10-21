"""“大脑”：拼提示词（人设 + 时间感 + 记忆 + 心情 + 最近聊天），生成回复，更新状态。"""
from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime

from ailife.core.config import resolve
from ailife.core.db import DB
from ailife.llm.client import LLM
from ailife.mind.emotion import AFFECTION, Emotion
from ailife.mind.memory import Memory
from ailife.mind.output import Parsed, parse_reply, parse_when
from ailife.persona.loader import load_persona

log = logging.getLogger("ailife.brain")

WEEK = "一二三四五六日"


def next_occurrence(datestr: str, today):
    """'MM-DD' 每年重复；'YYYY-MM-DD' 一次性（过去了返回 None）。"""
    from datetime import date
    try:
        if len(datestr) == 5:
            m, d = map(int, datestr.split("-"))
            for y in (today.year, today.year + 1):
                try:
                    cand = date(y, m, d)
                except ValueError:          # 2 月 29 日
                    cand = date(y, m, 28)
                if cand >= today:
                    return cand
        else:
            cand = date.fromisoformat(datestr)
            return cand if cand >= today else None
    except ValueError:
        return None


def human_gap(seconds: float) -> str:
    m = seconds / 60
    if m < 60:
        return f"{int(m)} 分钟"
    h = m / 60
    if h < 24:
        return f"{h:.0f} 个小时" if h >= 2 else "一个多小时"
    d = h / 24
    return f"{d:.0f} 天" if d >= 1.5 else "一天多"


def period_of_day(h: int) -> str:
    if h < 5:
        return "深夜"
    if h < 8:
        return "清晨"
    if h < 11:
        return "上午"
    if h < 13:
        return "中午"
    if h < 18:
        return "下午"
    if h < 23:
        return "晚上"
    return "深夜"


# 回复格式：常用的标签每次都给；不常用的（照片、纪念日、追番、推歌、打卡…）只在聊到相关话题时才出现，
# 提示词更短、回复更快，模型也不容易乱打标签。
PROTO_HEAD = """
# 回复格式（非常重要）
- 你是在手机上发消息。想分成几条就用 ||| 隔开，每条一两句话。平时回 1~2 条；他认真讲事、问你问题、情绪低落时可以多几条（最多 4 条）。
- 标签不会被他看到，只会被执行，偶尔用就好：
  [表情:情绪] 发贴纸（开心/大笑/喜欢/害羞/难过/委屈/生气/哼/惊讶/无语/困/抱抱/想你/得意/哭），三四轮才发一次
  [引用] 引用他刚才那句话回复（只写标签，不复述原话）
  [回应:❤] 给他刚才那条消息点个表情（❤ 🥰 😁 😢 🤔 😱 👍 🔥 🎉 😘 🙈 😴 🤗 👀 之一）
  [撤回] 放在某条开头：发出去几秒后撤回（说漏真心话后悔时，很少用）
  [心情:…] 心情明显变化时更新
  [提醒:时间|内容] 他让你到点提醒、或你答应到点找他，如 [提醒:明天 08:00|叫他起床]（日期程序会算）"""
PROTO_TAIL = """- 不要用 markdown、列表、括号里的动作描写（比如“（抱住你）”），不要把整句话放在引号里当心里话，不要输出时间戳。
- 聊到真实的番、游戏、歌、明星时，拿不准的细节（谁做的、剧情、结局）别编，说感受就好。"""
PROTO_OPT = {
    "photo": "  [照片:画面描述] 发一张你拍的照片（他想看、或你想分享生活时；今天还能发 {photos_left} 张）",
    "date": "  [纪念日:日期|名称] 他提到具体日期的重要日子时记下：每年的写 MM-DD，一次性的可以照他的说法写“下周二”",
    "show": "  [追番:番名|周几 HH:MM] 他明确说要和你一起追某部番时记下（不知道时间就只写番名）",
    "song": "  [推歌:歌名|歌手] 想给他推荐一首真实存在的歌时用，程序会附上链接",
    "habit": "  [打卡:目标名] 他说自己完成了某个打卡目标（{habits}）时，帮他打卡",
    "wish": "  [愿望:想一起做的事] 他说“以后想和你一起…”这类愿望时记进你们的愿望清单；真的一起做到了就用 [完成愿望:那件事]",
    "story": "  [睡前故事:主题] 他想听睡前故事时：这次只回一两句应答（比如“好啦好啦，就讲一个”），再加这个标签，故事会单独发",
    "game": "- 无聊或气氛合适时可以提议玩个小游戏（成语接龙、猜谜、真心话、二选一…），玩的时候认真按规则、按你的性格在意输赢。",
}
TOPIC_RE = {
    "photo": re.compile(r"照片|自拍|看看你|发张|拍|长什么样|穿什么"),
    "date": re.compile(r"\d{1,2}\s*[月/.-]\s*\d{1,2}|明天|后天|下周|下个月|周[一二三四五六日天]|生日|纪念|考试|面试|出差|答辩|号"),
    "show": re.compile(r"番|动画|追|更新|一集|新番|第.{1,3}集"),
    "song": re.compile(r"歌|音乐|听|推荐|旋律|专辑|歌手"),
    "game": re.compile(r"无聊|玩|游戏|好闲|没事干"),
    "wish": re.compile(r"一起|愿望|以后|总有一天|有一天|下次见面|见面的时候|想去|带你|陪你去|做到了|实现"),
}




def append_user(messages: list[dict], text: str):
    """追加一条 user 消息；如果最后一条已经是 user，就合并，保证角色交替。"""
    if messages and messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], str):
        messages[-1] = {"role": "user", "content": messages[-1]["content"] + "\n" + text}
    else:
        messages.append({"role": "user", "content": text})


class Brain:
    def __init__(self, cfg):
        self.cfg = cfg
        from ailife.persona.library import db_path
        self.db = DB(db_path(cfg))                  # 每个角色一份自己的记忆（第一个角色用 data/ailife.db）
        self.llm = LLM(cfg)
        self.llm.db = self.db                       # 记每天的大模型调用统计（/diag 里看）
        self.persona = load_persona(resolve(cfg, cfg.persona_file))
        from ailife.persona.loader import pronoun_note
        self.llm.pronoun_note = pronoun_note(self.persona)
        self.memory = Memory(self.db, self.llm, self.persona, cfg)
        self.emotion = Emotion(self.db, cfg.tz)
        from ailife.media.catalog import Media
        from ailife.mind.inner import Mind
        from ailife.mind.story import Story
        self.story = Story(cfg, self)
        self.mind = Mind(cfg, self)
        self.media = Media(cfg, self.db)
        self.memory.on_show_added = self._enrich_later
        from ailife.mind.quality import Quality
        self.quality = Quality(cfg, self.db, self.persona)
        self._last_examples: list[str] = []
        from ailife.mind.together import Together
        self.together = Together(cfg, self)
        from ailife.mind.healthy import Healthy
        self.healthy = Healthy(cfg, self.db)           # 20.0 关系健康底线：告别不挽留、不拿愧疚感拉他、依恋时往身边的人引
        self._replying = False
        from ailife.media.links import LinkReader
        self.links = LinkReader(cfg, self)
        from ailife.mind.rhythm import Rhythm
        self.rhythm = Rhythm(cfg, self)
        self.memory.on_routine = self.rhythm.add_routine
        from ailife.mind.moments import Moments
        self.moments = Moments(cfg, self)
        from ailife.mind.herworld import HerWorld
        self.herworld = HerWorld(cfg, self)
        from ailife.mind.away import Away
        self.away = Away(cfg, self)            # 电脑关机 / 睡眠前后：道别、回来补上
        self.memory.bible = self.herworld.bible
        from ailife.media.photo import PhotoMaker
        from ailife.mind.world import Weather
        self.weather = Weather(cfg, self.db)
        self.photos = PhotoMaker(cfg, self.db)
        self.photos.persona = self.persona
        self.photos.llm = self.llm
        from ailife.mind.memoir import Memoir
        self.memoir = Memoir(self)                              # 11.4 季度回忆录
        from ailife.mind.selfgrow import SelfGrow
        self.selfgrow = SelfGrow(self)                          # 11.2 喜好不改口、每月整理自己、被纠正了就改
        self.weather_info: dict | None = None
        self.db.log_mood(self.mood, self.affection, self.emotion.snapshot())   # 每次启动记一个点

    async def refresh_world(self):
        try:
            self.weather_info = await self.weather.fetch()
        except Exception as e:  # noqa: BLE001
            log.warning("天气刷新失败：%s", e)

    # ---------------- 状态 ----------------
    def now(self) -> datetime:
        return datetime.now(self.cfg.tz)

    @property
    def mood(self) -> str:
        return self.db.get("mood", "平静")

    @property
    def affection(self) -> int:
        return AFFECTION     # 好感度永远是满的，不会变

    def apply_state(self, p: Parsed):
        if p.mood:
            self.db.set("mood", p.mood[:30])
            self.emotion.on_mood_text(p.mood)
        from ailife.mind.output import normalize_date, parse_show_time
        for d, name in p.dates:
            nd = normalize_date(d, self.now())
            if nd and len(nd) == 10 and ("生日" in name or "纪念" in name):
                nd = nd[5:]                       # 生日、纪念日每年都过
            if nd:
                kind = "birthday" if "生日" in name else "anniversary" if ("纪念" in name or len(nd) == 5) else "event"
                self.db.add_date(nd, name[:40], kind, 3 if kind != "event" else 1)
                log.info("记下日子 %s：%s", nd, name)
        for name, when in p.shows:
            st = parse_show_time(when) or (None if when else (-1, "23:00"))
            if st and st[0] == -1 and any(name in s["name"] or s["name"] in name for s in self.db.active_shows()):
                continue                              # 没说时间、又已经在追了：别把原来的时间改掉
            if st and name:
                sid = self.db.add_show(name[:40], max(0, st[0]), st[1])
                log.info("记下追番 %s：%s", name, when or "（时间待查）")
                self._enrich_later(sid, guess_weekday=(st[0] == -1))
        self.db.log_mood(self.mood, self.affection, self.emotion.snapshot())
        self._track_expressions(p)
        for name in p.checkins:
            h = self.together.find_habit(name)
            if h:
                new, streak = self.together.do_checkin(h["id"])
                if new:
                    log.info("帮他打卡：%s（连续 %d 天）", h["name"], streak)
        for w in p.wishes:
            if self.db.add_wish(w, "her"):
                log.info("记进愿望清单：%s", w)
        for w in p.wishes_done:
            hit = self.moments.find_wish(w)
            if hit and self.db.done_wish(hit["id"]):
                log.info("愿望实现：%s", hit["text"])
        for when, content in p.reminders:
            t = parse_when(when, self.now())
            if t:
                self.db.add_reminder(t.timestamp(), content)
                log.info("记下提醒 %s：%s", t, content)

    # ---------------- 人设热更新 ----------------
    def reload_persona(self, path: str | None = None) -> list[str]:
        """重新读人设文件（改了内容后不用重启）。返回警告。换成别的角色要重启（记忆是分开的）。"""
        from ailife.persona.loader import load_persona, pronoun_note
        new = load_persona(resolve(self.cfg, path or self.cfg.persona_file))
        self.persona.update_from(new)
        self.llm.pronoun_note = pronoun_note(self.persona)
        self._last_examples = []
        log.info("人设已更新：%s", self.persona.name)
        return new.warnings

    # ---------------- 提示词 ----------------
    def _time_block(self) -> str:
        now = self.now()
        from ailife.mind.memory import calendar_hint
        lines = [f"现在是 {now:%Y-%m-%d %H:%M}，星期{WEEK[now.weekday()]}，{period_of_day(now.hour)}。",
                 calendar_hint(now)]
        users = self.db.conn.execute(
            "SELECT ts FROM messages WHERE role='user' ORDER BY id DESC LIMIT 1").fetchone()
        if users:
            gap_now = time.time() - users["ts"]
            if gap_now > 600:
                if gap_now > 3600:
                    lines.append(f"他上一次给你发消息是 {human_gap(gap_now)}前。")
            else:
                # 他刚发了消息：看这一轮之前隔了多久（只算他自己的消息，她单方面的主动消息不算）
                ts = [r["ts"] for r in self.db.recent_messages(60) if r["role"] == "user"]
                i = len(ts) - 1
                while i > 0 and ts[i] - ts[i - 1] < 600:
                    i -= 1
                if i > 0 and ts[i] - ts[i - 1] > 6 * 3600:
                    lines.append(f"他上一次找你说话是 {human_gap(ts[i] - ts[i - 1])}前，"
                                 "这是隔了这么久之后他刚发来的消息。")
        unanswered = int(self.db.get("unanswered", 0))
        if unanswered:
            lines.append(f"你最近主动找了他 {unanswered} 次，他都还没回。")
        ignored = int(self.db.get("ignored_before_reply", 0))
        if ignored:
            lines.append(f"在他刚发来这条消息之前，你连着主动找了他 {ignored} 次他都没回。"
                         "按你的性格对此有反应（小小抱怨、闹点小别扭，或者松了口气），但别揪着不放，说一两句就好。")
        return "\n".join(lines)

    def _world_block(self) -> str:
        lines = []
        if self.weather_info:
            lines.append(f"他那边的天气：{self.weather_info['text']}（自然地关心，别每句都提）")
        sched = self.db.get("schedule") or (self.cfg.get("world") or {}).get("schedule") or ""
        if sched and not getattr(self, "rhythm", None):
            lines.append(f"他的作息/课表：{sched}（他在上课、上班、睡觉时别打扰，主动找他前想想他现在方不方便）")
        hb_r = self.rhythm.his_block() if getattr(self, "rhythm", None) else ""
        if hb_r:
            lines.append(hb_r + "（他在上课、上班、睡觉时别打扰，主动找他前想想他现在方不方便）")
        today = self.now().date()
        soon = []
        for d in self.db.all_dates():
            nxt = next_occurrence(d["date"], today)
            if nxt is not None and 0 <= (nxt - today).days <= 30:
                left = (nxt - today).days
                soon.append(f"{d['name']}（{nxt:%m-%d}，{'就是今天' if left == 0 else f'还有 {left} 天'}）")
        if soon:
            lines.append("快到的重要日子：" + "；".join(soon))
        shows = self.db.active_shows()
        for s in shows:
            tot = f"/共 {s['total_eps']} 集" if s["total_eps"] else ""
            line = f"你们一起追《{s['name']}》：每周{WEEK[s['weekday']]} {s['time']} 更新，已经出到第 {s['episode']} 集{tot}"
            eps = self.media.recent_eps_cached(s, 2)
            if eps:
                line += "。最近几集：" + "；".join(
                    f"第{e['ep']}集「{e['title']}」{('：' + e['desc'][:90]) if e['desc'] else ''}" for e in eps)
            lines.append(line)
        hb = self.together.habits_block()
        if hb:
            lines.append(hb)
        arts = self.db.top_artists()
        if arts:
            lines.append("他常听的歌手：" + "、".join(a for a, _ in arts))
        return "# 你们的生活\n" + ("\n".join(lines) if lines else "（暂时没有特别的）")

    def _enrich_later(self, show_id: int, guess_weekday: bool = False):
        """后台去 Bangumi 查这部番（总集数、每集标题简介；没给时间时按开播日推星期几）。"""
        import asyncio

        async def run():
            row = self.db.conn.execute("SELECT * FROM shows WHERE id=?", (show_id,)).fetchone()
            if not row:
                return
            info = await self.media.enrich_show(row)
            if guess_weekday and info and info.get("eps"):
                from datetime import date as _d
                first = next((e["airdate"] for e in info["eps"] if e["airdate"]), None)
                if first:
                    wd = _d.fromisoformat(first).weekday()
                    self.db.conn.execute("UPDATE shows SET weekday=? WHERE id=?", (wd, show_id))
                    self.db.conn.commit()
        async def safe():
            try:
                await run()
            except Exception as e:  # noqa: BLE001
                log.info("查番剧信息失败：%s", e)
        try:
            t = asyncio.get_running_loop().create_task(safe())
            self._bg = getattr(self, "_bg", set())
            self._bg.add(t)
            t.add_done_callback(self._bg.discard)
        except RuntimeError:
            pass

    def protocol(self, query: str = "") -> str:
        """按聊到的话题挑要给的标签说明。"""
        recent = query or ""
        head = PROTO_HEAD
        if self.db.get("last_channel") == "qq":    # QQ 私聊没有“给消息点表情”；贴纸会发成表情包 / 小黄脸
            head = "\n".join(x for x in head.splitlines() if "[回应:" not in x)
            head = head.replace("发贴纸（", "发表情包（")
        lines = [head]
        photos_left = self.photos.remaining_today()
        if photos_left > 0 and (TOPIC_RE["photo"].search(recent) or random.random() < 0.25):
            lines.append(PROTO_OPT["photo"].format(photos_left=photos_left))
        if TOPIC_RE["date"].search(recent):
            lines.append(PROTO_OPT["date"])
        if TOPIC_RE["show"].search(recent):
            lines.append(PROTO_OPT["show"])
        if TOPIC_RE["song"].search(recent):
            lines.append(PROTO_OPT["song"])
        habits = [h["name"] for h in self.db.habits()] if hasattr(self.db, "habits") else []
        if habits:
            lines.append(PROTO_OPT["habit"].format(habits="、".join(habits)))
        if TOPIC_RE["wish"].search(recent):
            lines.append(PROTO_OPT["wish"])
        from ailife.mind.moments import STORY_RE
        if STORY_RE.search(recent):
            lines.append(PROTO_OPT["story"])
        if TOPIC_RE["game"].search(recent):
            lines.append(PROTO_OPT["game"])
        lines.append(PROTO_TAIL)
        return "\n".join(lines)

    def _track_expressions(self, p: Parsed):
        """她用了学来的说法：记一次使用，等看他的反应来调权重。"""
        text = p.plain_text
        used = [e["id"] for e in self.db.expressions("approved") if e["phrase"] in text]
        for eid in used:
            row = next(e for e in self.db.expressions("approved") if e["id"] == eid)
            self.db.set_expression(eid, uses=row["uses"] + 1, last_used=time.time())
        if used:
            self.db.set("expr_pending", {"ids": used, "ts": time.time()})

    def _us_block(self) -> str:
        lines = []
        threads = self.db.open_threads(4)
        if threads:
            lines.append("还没聊完 / 在等结果的事（合适时自然地接上或追问）：" + "；".join(t["topic"] for t in threads))
        exprs = [e for e in self.db.expressions("approved") if e["weight"] >= 0.3][:6]
        if exprs:
            lines.append("你们之间的说法和梗（偶尔自然地用，别硬塞）：" + "；".join(
                f"“{e['phrase']}”" + (f"（{e['meaning']}）" if e["meaning"] else "") for e in exprs))
        notes = self.db.unseen_notes()
        if notes:
            lines.append("他在你们的小屋留言板上给你写了：" + "；".join(f"“{n['content']}”" for n in notes[-3:])
                         + "（你看到了，找机会回应）")
        return "# 你们之间\n" + "\n".join(lines) if lines else ""

    def _now_block(self) -> str:
        """他此刻在干什么（手机信号），有就写，过时的不写。"""
        lines = []
        ph = self.db.get("sense_phone") or {}
        if ph and time.time() - ph.get("ts", 0) < 3 * 3600:
            t = datetime.fromtimestamp(ph["ts"], self.cfg.tz)
            lines.append(f"他手机 {t:%H:%M} 传来的状态：{ph.get('event')}")
        return ("# 他此刻（你“感觉”到的，别说是程序告诉你的，也别每次都提）\n" + "\n".join(lines)) if lines else ""

    async def build_prompt(self, query: str, mode: str = "chat") -> str:
        mems = await self.memory.recall(query, "user", 12)
        selfs = await self.memory.recall(query, "self", 6)
        return self.system_prompt(query, mode, mems=mems, selfs=selfs)

    def system_prompt(self, query: str, mode: str = "chat", mems=None, selfs=None) -> str:
        p = self.persona
        if mems is None:
            mems = self.memory.relevant(query)
        if selfs is None:
            selfs = self.memory.relevant(query, 6, owner="self")
        mem_txt = "\n".join(f"- {m['content']}" for m in mems) or "（还没有）"
        self_txt = "\n".join(f"- {m['content']}" for m in selfs)
        diaries = self.db.recent_diary(2)
        diary_txt = "\n".join(f"{d['day']}：{d['content']}" for d in diaries) or "（还没有）"
        life = self.db.get(f"life_{self.now():%Y-%m-%d}", "")
        owner = p.user_name or (self.cfg.get("qq") or {}).get("owner_name") or p.call_name or "他"
        app = "QQ"
        from ailife.persona import examples as ex
        lu_row = self.db.last_message("user")
        chosen = ex.pick(p, query, (lu_row["content"] if lu_row else ""), avoid=self._last_examples)
        self._last_examples = [e.scene for e in chosen]
        self.quality.ref_lines = ex.her_lines(p, chosen)
        parts = [
            f"你就是{p.name}，正在和你在意的人（{owner}）用 {app} 聊天。始终以{p.name}的身份说话，"
            f"不要提到自己是 AI、模型或程序。称呼他时用“{p.call_name}”或你们自然的叫法。",
            p.body,
            ex.block(p, chosen),
            f"# 你今天自己的安排\n{life or '（普通的一天，自由发挥但要前后一致）'}",
            self.together.care_block(),
            self.healthy.block((lu_row["content"] if lu_row else "") or "", replying=self._replying),
            f"# 你此刻的心情\n{self.mood}；{self.emotion.describe()}\n"
            "对他的好感：永远是满的，不会因为任何事减少（你最喜欢他了，只是嘴上不承认）。",
            f"# 你记得的关于他的事\n{mem_txt}",
            (f"# 你自己说过的事（要和这些保持一致，别自相矛盾）\n{self_txt}" if self_txt else ""),
            self.selfgrow.prefs_block() if getattr(self, "selfgrow", None) else "",
            f"# 你最近的日记\n{diary_txt}",
            self._world_block(),
            self.mind.profile_block(),
            self.rhythm.her_block(),
            self.herworld.bible(query),
            self.story.block(self.now().date()),
            self.herworld.goals_block(self.now().date()),
            self.moments.wishes_block(),
            self._us_block(),
            self._now_block(),
            self.mind.recent_block(),
            self.quality.block(),
            "# 此刻（说话要符合这个时间，比如下午不要说早上好）\n" + self._time_block(),
        ]
        parts = [x for x in parts if x]
        parts.append(self.protocol(query))
        return "\n\n".join(parts)

    def history(self, n: int | None = None) -> list[dict]:
        n = n or int(self.cfg.style.context_messages)
        rows = self.db.recent_messages(n)
        msgs: list[dict] = []
        prev_ts = None
        for r in rows:
            content = r["content"]
            if r["role"] == "event":
                role, content = "user", f"（系统记录：{content}）"
            else:
                role = r["role"]
            if prev_ts and r["ts"] - prev_ts > 3 * 3600:
                t = datetime.fromtimestamp(r["ts"], self.cfg.tz)
                note = f"（{human_gap(r['ts'] - prev_ts)}后，{t:%m-%d %H:%M}）"
                content = f"{note}\n{content}" if role == "user" else content
                if role == "assistant":
                    msgs.append({"role": "user", "content": note})
            prev_ts = r["ts"]
            if msgs and msgs[-1]["role"] == role:
                msgs[-1]["content"] += "\n" + content
            else:
                msgs.append({"role": role, "content": content})
        # 部分接口要求第一条是 user
        while msgs and msgs[0]["role"] == "assistant":
            msgs.pop(0)
        return msgs

    # ---------------- 生成 ----------------
    def _query(self, hist: list[dict], extra: str = "") -> str:
        """检索记忆用的查询：以他最新的一轮话为主，带一点上文。"""
        last_user = next((m["content"] for m in reversed(hist) if m["role"] == "user"), "")
        ctx = "\n".join(m["content"] for m in hist[-3:])
        return (last_user + "\n" + ctx[-300:] + extra).strip()

    async def reply(self) -> Parsed:
        await self.refresh_world()
        hist = self.history()
        query = self._query(hist)
        self._replying = True
        try:
            sys = await self.build_prompt(query)
        finally:
            self._replying = False
        messages = [{"role": "system", "content": sys}] + hist
        if len(messages) == 1:
            messages.append({"role": "user", "content": "（他刚刚打开了和你的聊天）"})
        last_user = self.db.last_message("user")
        lu = (last_user["content"] if last_user else "") or ""
        serious = len(lu) > 30 or re.search(r"难过|累|烦|崩溃|压力|哭|不开心|失眠|焦虑|怎么办|为什么|吗？|吗\?", lu)
        away = self.away.hint_for_reply() if messages[-1].get("role") == "user" else ""
        if away:                                  # 电脑关着的时候他发过消息：回他时顺便说说刚才在干嘛
            messages[-1] = {**messages[-1], "content": f"{messages[-1]['content']}\n（{away}）"}
        p = await self._generate(messages, proactive=False, max_bubbles=4 if serious else 3)
        self.db.set("ignored_before_reply", 0)
        if self.db.unseen_notes():
            self.db.mark_notes_seen()
        last_user = self.db.last_message("user")
        if last_user:
            said = last_user["content"]
            p.bubbles = [b for b in p.bubbles
                         if not (b.text and len(b.text) >= 6 and b.text.strip("“”\"' ") in said)]
        self.apply_state(p)
        return p

    async def proactive(self, reason: str, must: bool = False) -> Parsed:
        """主动找他。reason 描述为什么现在想找他（早安/想他/提醒…）。must：一定要说（他让她到点提醒的），不给“跳过”。"""
        await self.refresh_world()
        hist = self.history()
        query = self._query(hist, "\n" + reason)
        sys = await self.build_prompt(query)
        unanswered = int(self.db.get("unanswered", 0))
        max_b = 1 if unanswered >= 1 else 2
        length = ("只发 1 条短消息（他还没回你上一条，比上次更短、换个话题，别追问他为什么不回）"
                  if unanswered >= 1 else "只发 1~2 条短消息，像随手发的一句，不要一口气说好几件事")
        instruction = (
            f"（系统：现在不是在回复他，而是你主动给他发消息。触发原因：{reason}。"
            f"结合时间、你今天在做的事、他最近的情况和你们上次聊到哪，自然地开个头。{length}。"
            "不要重复你之前说过的话和用过的开头。"
            + ("这条一定要发，不能跳过。）" if must else "如果觉得此刻不适合打扰他，只输出 [跳过]。）")
        )
        messages = [{"role": "system", "content": sys}] + hist
        append_user(messages, instruction)
        if messages[1]["role"] != "user":
            messages.insert(1, {"role": "user", "content": "（聊天开始）"})
        p = await self._generate(messages, proactive=True, max_bubbles=max_b, tag=reason)
        if must and p.skip:
            p.skip = False                       # 模型还是写了 [跳过]：交给调用方重试 / 发固定的话
        if not p.skip:
            self.apply_state(p)
        return p

    @staticmethod
    def _cap_bubbles(p: Parsed, n: int):
        idx = [i for i, b in enumerate(p.bubbles) if b.text]
        if len(idx) <= n:
            return
        keep, extra = idx[n - 1], idx[n:]
        p.bubbles[keep].text += " " + " ".join(p.bubbles[i].text for i in extra)
        drop = set(extra)
        p.bubbles = [b for i, b in enumerate(p.bubbles) if i not in drop]

    async def _generate(self, messages: list[dict], proactive: bool, max_bubbles: int, tag: str = "") -> Parsed:
        """生成 → 检查有没有重复/太长/称呼超额 → 有就带着具体问题重写一次 → 还不行就删掉有问题的气泡。"""
        raw = await self.llm.chat(messages)
        log.info("%s原文%s：%s", "主动消息" if proactive else "回复", f"（{tag}）" if tag else "", raw)
        p = parse_reply(raw, self.persona.name)
        if p.skip:
            return p
        if not proactive:
            self._cap_bubbles(p, max_bubbles)     # 回复太多条：把多的并进最后一条，不用重写
        texts = [b.text for b in p.bubbles if b.text]
        lu_row = self.db.last_message("user")
        lu = (lu_row["content"] if lu_row else "") or ""
        issues = self.quality.problems(texts, proactive, max_bubbles)
        h_issues = self.healthy.issues(texts, lu, proactive)
        if issues and not proactive:
            # 回复他的话：只要还有没问题的句子，就直接删掉重复的那几句（换话题重写容易答非所问）
            flags = self.quality.dup_flags(texts)
            if not all(flags) and len(texts) > 1:
                self.quality.prune(p, False, max_bubbles)
                issues = self.quality.problems([b.text for b in p.bubbles if b.text], False, max_bubbles)
        issues = h_issues + [x for x in issues if x not in h_issues]
        if issues:
            log.info("重写（%s）", "；".join(issues)[:200])
            how = ("换个说法、换个话题重新写" if proactive else
                   "继续回应他刚才说的话（意思和态度不变），只把有问题的句子换个说法，其余照旧")
            retry = messages + [{"role": "assistant", "content": raw},
                                {"role": "user", "content": "（系统：你刚才写的有这些问题：" + "；".join(issues[:5]) +
                                 f"。请{how}（最多 {max_bubbles} 条），只输出重写后的消息，别解释。）"}]
            try:
                raw2 = await self.llm.chat(retry)
                p2 = parse_reply(raw2, self.persona.name)
                n_old = sum(1 for b in p.bubbles if b.text)
                n_new = sum(1 for b in p2.bubbles if b.text)
                if not p2.skip and n_new and (proactive or h_issues or n_new >= min(2, n_old)):
                    log.info("重写后：%s", raw2)
                    # 重写只为换说法：原来打的标签（提醒、纪念日、追番、打卡、心情……）重写时常常漏掉，补回来
                    for k in ("reminders", "dates", "shows", "songs", "checkins", "wishes", "wishes_done"):
                        if not getattr(p2, k):
                            setattr(p2, k, getattr(p, k))
                    for k in ("mood", "reaction", "story"):
                        if getattr(p2, k) is None:
                            setattr(p2, k, getattr(p, k))
                    p2.quote = p2.quote or p.quote
                    p = p2
                    if not proactive:
                        self._cap_bubbles(p, max_bubbles)
            except Exception as e:  # noqa: BLE001
                log.info("重写失败，用原来的：%s", e)
        if self.quality.problems([b.text for b in p.bubbles if b.text], proactive, max_bubbles):
            p = self.quality.prune(p, proactive, max_bubbles)
        txt = [b for b in p.bubbles if b.text]
        bad = self.healthy.bad_flags([b.text for b in txt], lu, proactive)
        if any(bad) and not all(bad):                 # 20.0 重写了还在挽留 / 埋怨：删掉那几句（至少留一句）
            drop = {id(b) for b, f in zip(txt, bad) if f}
            log.info("删掉挽留 / 埋怨的话：%s", [b.text[:20] for b in txt if id(b) in drop])
            p.bubbles = [b for b in p.bubbles if id(b) not in drop]
        if proactive:
            first = next((b.text for b in p.bubbles if b.text), "")
            if first:
                self.quality.remember_shared(first)
        return p
