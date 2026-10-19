"""时间里的节奏：她的大学日历（学期、考试周、假期、年级、生日）+ 他的作息（从聊天和手机信号里学）。

她：以前永远是“大一的某一天”。现在按真实日期：
    开学 → 上课周（第几周）→ 国庆 / 五一 → 期中 → 考试周 → 寒暑假（回家，家就在他家隔壁）；
    第一次运行时记下“入学年份”和“现在几岁”，以后每过一个学年升一级、每过一次生日长一岁。
    日期都可以在 config.yaml 的 her_world.school 里改。
他：
    - 聊天里说过的固定安排（“周三晚上有课”“工作日九点上班”“周五常加班”）整理记忆时顺便记下；
    - 手机发来的“到公司 / 下班 / 到家”信号，算出他平时几点到、几点走；
    - 用处：她知道他这会儿多半在忙（少打扰、晚点再找），也会在对的时间关心他。
"""
from __future__ import annotations

import logging
import random
import re
import statistics
import time
from datetime import date, datetime

log = logging.getLogger("ailife.rhythm")

WEEK = "一二三四五六日"
GRADES = {1: "大一", 2: "大二", 3: "大三", 4: "大四"}


def norm_md(v) -> str | None:
    """'3-5' / '03-05' / '3月5日' / '2001-03-05' / date → 'MM-DD'；不合法返回 None。"""
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return f"{v.month:02d}-{v.day:02d}"
    nums = re.findall(r"\d+", str(v))
    if len(nums) >= 3 and len(nums[0]) == 4:
        nums = nums[1:]
    if len(nums) < 2:
        return None
    m, d = int(nums[0]), int(nums[1])
    try:
        date(2000, m, d)                           # 2000 是闰年：2 月 29 也算合法
    except ValueError:
        return None
    return f"{m:02d}-{d:02d}"


def _md(s: str, year: int) -> date:
    m, d = (int(x) for x in (norm_md(s) or "01-01").split("-"))
    try:
        return date(year, m, d)
    except ValueError:                             # 2 月 29 日遇到平年
        return date(year, m, 28)


class School:
    """中国大学的典型校历（可在 config 里改）。"""

    def __init__(self, cfg):
        c = dict((dict(cfg.get("her_world") or {})).get("school") or {})
        self.enabled = bool(c.get("enabled", True))
        self.fall_start = str(c.get("fall_start", "09-01"))
        self.fall_exams = str(c.get("fall_exams", "01-05"))          # 期末考试周开始
        self.winter = str(c.get("winter_break", "01-17~02-22"))
        self.spring_start = str(c.get("spring_start", "02-23"))
        self.spring_exams = str(c.get("spring_exams", "06-22"))
        self.summer = str(c.get("summer_break", "07-05~08-31"))

    def academic_year(self, d: date) -> int:
        """学年按开学那年算：2026-09 ~ 2027-08 是 2026 学年。"""
        return d.year if d >= _md(self.fall_start, d.year) else d.year - 1

    def phase(self, d: date) -> dict:
        ay = self.academic_year(d)
        fall0 = _md(self.fall_start, ay)
        w0 = self.winter.split("~")[0].strip()
        win0 = _md(w0, ay + 1)
        spr0 = _md(self.spring_start, ay + 1)
        s0, s1 = (x.strip() for x in self.summer.split("~"))
        sum0, sum1 = _md(s0, ay + 1), _md(s1, ay + 1)
        fex, sex = _md(self.fall_exams, ay + 1), _md(self.spring_exams, ay + 1)
        if fall0 <= d < win0:
            term, start, exams = "上学期", fall0, fex
        elif win0 <= d < spr0:
            return {"term": "寒假", "vacation": True, "days_left": (spr0 - d).days, "ay": ay}
        elif spr0 <= d < sum0:
            term, start, exams = "下学期", spr0, sex
        else:
            return {"term": "暑假", "vacation": True, "days_left": (sum1 - d).days + 1, "ay": ay}
        week = (d - start).days // 7 + 1
        notes = []
        if term == "上学期" and week <= 2:
            notes.append("刚开学")
        if d.month == 10 and d.day <= 7:
            notes.append("国庆假期")
        elif d.month == 5 and d.day <= 5:
            notes.append("五一假期")
        days_to_exam = (exams - d).days
        if 0 <= days_to_exam <= 14:
            notes.append(f"期末考试周快到了（还有 {days_to_exam} 天），在复习")
        elif days_to_exam < 0:
            notes.append("期末考试周，每天在考试或复习")
        elif 7 <= week <= 10:
            notes.append("期中前后，作业和小测变多")
        return {"term": term, "vacation": False, "week": week, "notes": notes, "ay": ay,
                "days_to_vacation": (win0 if term == "上学期" else sum0) - d}


class Work:
    """上班族的节奏：工作日上班、周末和法定假日休息（春节按农历，装了 lunar_python 才算得出来）。"""

    def __init__(self, c: dict):
        self.hours = str(c.get("hours") or "09:30-18:30")
        self.days = str(c.get("days") or "工作日")
        self.note = str(c.get("note") or "")

    def holiday(self, d: date) -> str:
        md = (d.month, d.day)
        if md == (1, 1):
            return "元旦"
        if d.month == 10 and d.day <= 7:
            return "国庆假期"
        if d.month == 5 and d.day <= 5:
            return "五一假期"
        try:
            from lunar_python import Solar
            lu = Solar.fromYmd(d.year, d.month, d.day).getLunar()
            if (lu.getMonth() == 1 and lu.getDay() <= 6) or (lu.getMonth() == 12 and lu.getDay() >= 29):
                return "春节假期"
        except Exception:  # noqa: BLE001
            pass
        return ""

    def today(self, d: date) -> str:
        h = self.holiday(d)
        p = parse_when_spec(f"{self.days} {self.hours}")
        workday = p and d.weekday() in p[0]
        if h:
            return f"今天{h}，不用上班"
        if not workday:
            return f"今天周{WEEK[d.weekday()]}，休息日"
        return f"今天周{WEEK[d.weekday()]}，要上班（{self.hours}）" + (f"，{self.note}" if self.note else "")


class Rhythm:
    """她的日子怎么过：人设里的 life（student / worker / free）决定。"""

    def __init__(self, cfg, brain):
        self.cfg, self.brain, self.db = cfg, brain, brain.db
        c = dict(cfg.get("her_world") or {})
        self.cfg_age = int(c.get("age", 19))
        self.birthday_cfg = norm_md(c.get("birthday")) or ""
        if c.get("birthday") and not self.birthday_cfg:
            log.warning("her_world.birthday 写得不对（要 MM-DD，比如 03-15），先忽略")
        self._school_cfg = dict(c.get("school") or {})

    # ---------- 跟着人设走（人设热更新后马上生效） ----------
    @property
    def persona(self):
        return self.brain.persona

    @property
    def life(self) -> str:
        return getattr(self.persona, "life", "student") or "student"

    @property
    def school(self) -> School:
        merged = {**self._school_cfg, **(getattr(self.persona, "school", None) or {})}
        key = repr(sorted(merged.items()))
        if getattr(self, "_school_key", None) != key:
            from ailife.core.config import Cfg
            self._school = School(Cfg({"her_world": {"school": merged}}))
            self._school_key = key
            self._school_on = self._school.enabled
        self._school.enabled = self._school_on and self.life == "student"    # 热更新改了 life 马上生效
        return self._school

    @property
    def work(self) -> Work:
        return Work(getattr(self.persona, "work", None) or {})

    @property
    def base_age(self) -> int:
        return int(getattr(self.persona, "age", None) or self.cfg_age)

    def home_line(self) -> str:
        h = getattr(self.persona, "home", "") or ""
        return f"（{h}）" if h else ""

    # ================= 她 =================
    def _anchor(self) -> dict:
        """第一次运行时记下：入学学年、当时的年龄。以后据此算年级和年龄。人设里改了年龄就重新记。"""
        a = self.db.get("her_anchor")
        if a and a.get("base", a.get("age")) == self.base_age:
            return a
        today = self.brain.now().date()
        # 改了年龄：只重记年龄，入学年份不动（不然大三会突然变回大一）
        enroll = a["enroll_ay"] if a and a.get("enroll_ay") else self.school.academic_year(today)
        a = {"enroll_ay": enroll, "age": self.base_age, "base": self.base_age, "since": today.isoformat()}
        self.db.set("her_anchor", a)
        return a

    def birthday(self) -> str:
        b = getattr(self.persona, "birthday", "") or self.birthday_cfg or norm_md(self.db.get("her_birthday"))
        if not b:                                  # 没设就挑一个（不在最近两个月，免得刚认识就过生日）
            today = self.brain.now().date()
            for _ in range(50):
                d = date(2001, random.randint(1, 12), random.randint(1, 28))
                nxt = date(today.year, d.month, d.day)
                nxt = nxt if nxt >= today else date(today.year + 1, d.month, d.day)
                if (nxt - today).days > 60:
                    break
            b = f"{d.month:02d}-{d.day:02d}"
            self.db.set("her_birthday", b)
        return b

    def grade(self, d: date | None = None) -> str:
        d = d or self.brain.now().date()
        n = self.school.academic_year(d) - self._anchor()["enroll_ay"] + 1
        return GRADES.get(n, "已经毕业" if n > 4 else "大一")

    def college(self) -> bool:
        """身份是大学生（才说大几）；其他学生只说学期。"""
        ident = (getattr(self.persona, "identity", "") or "") + (self.persona.body[:300] if self.persona else "")
        return bool(re.search(r"大学|大一|大二|大三|大四|本科", ident))

    def age(self, d: date | None = None) -> int:
        d = d or self.brain.now().date()
        a = self._anchor()
        since = date.fromisoformat(a["since"])
        bday = self.birthday()
        n = sum(1 for y in range(since.year, d.year + 1) if since < _md(bday, y) <= d)
        return a["age"] + n

    def summary(self, d: date | None = None) -> str:
        """一句话：“大一上学期第 7 周”/“工作日，要上班”/“”"""
        d = d or self.brain.now().date()
        if self.life == "student" and self.school.enabled:
            ph = self.school.phase(d)
            g = self.grade(d) if self.college() else ""
            return f"{g}{ph['term']}" + (f"第 {ph['week']} 周" if not ph["vacation"] else "")
        if self.life == "worker":
            return self.work.today(d)
        return ""

    def her_block(self) -> str:
        try:
            return self._her_block()
        except Exception as e:  # noqa: BLE001      # 日期设置写错也不能让她回不了话
            log.warning("算她的学期 / 生日出错：%s", e)
            return ""

    def _her_block(self) -> str:
        d = self.brain.now().date()
        age = self.age(d)
        lines = []
        if self.life == "student" and self.school.enabled:
            ph = self.school.phase(d)
            g = self.grade(d) if self.college() else ""
            lines.append(f"你现在 {age} 岁" + (f"，{g}" if g else "") + "（人设里写的年龄、年级以这里为准）")
            if ph["vacation"]:
                lines.append(f"现在是{ph['term']}，你回家了{self.home_line()}；离开学还有 {ph['days_left']} 天。"
                             "能不能见面看实际情况，别自己说已经见过面了")
            else:
                notes = list(ph["notes"])
                if g == "大一" and ph["term"] == "上学期" and ph["week"] <= 2:
                    notes.append("新生在军训")
                lines.append(f"现在是{g}{ph['term']}第 {ph['week']} 周" + ("：" + "，".join(notes) if notes else ""))
                dv = ph["days_to_vacation"].days
                if dv <= 21:
                    lines.append(f"还有 {dv} 天放{'寒' if ph['term'] == '上学期' else '暑'}假，你在盼着放假")
        elif self.life == "worker":
            lines.append(f"你现在 {age} 岁（人设里写的年龄以这里为准）")
            lines.append(self.work.today(d))
        else:
            lines.append(f"你现在 {age} 岁（人设里写的年龄以这里为准）")
        bd = self.birthday()
        this = _md(bd, d.year)
        if this < d:
            this = _md(bd, d.year + 1)
        diff = (this - d).days
        if diff == 0:
            said = self.db.get(f"bday_wished_{d.year}")
            lines.append("今天是你的生日！" + ("他已经祝你生日快乐了，你很开心" if said else
                                          "他还没说生日快乐——你不会直接提，但心里在等"))
        elif 0 < diff <= 7:
            lines.append(f"再过 {diff} 天是你的生日（{bd}），你有点期待，但不会直接说")
        return "# 你的生活节奏（按真实日期推算）\n" + "\n".join(f"- {x}" for x in lines)

    def plan_hint(self) -> str:
        """给每天的日程用的一句话。"""
        try:
            return self._plan_hint()
        except Exception as e:  # noqa: BLE001
            log.warning("算学期出错：%s", e)
            return ""

    def _plan_hint(self) -> str:
        d = self.brain.now().date()
        if self.life == "student" and self.school.enabled:
            ph = self.school.phase(d)
            if ph["vacation"]:
                return f"现在是{ph['term']}，你在家{self.home_line()}，不用上课。"
            g = self.grade(d) if self.college() else ""
            return f"现在是{g}{ph['term']}第 {ph['week']} 周" + ("，" + "，".join(ph["notes"]) if ph["notes"] else "") + "。"
        if self.life == "worker":
            return self.work.today(d) + "。"
        return ""

    def note_birthday_wish(self, text: str):
        """他说了生日快乐（在她生日那天）。"""
        d = self.brain.now().date()
        if _md(self.birthday(), d.year) == d and re.search(r"生日快乐|happy birthday|生快", text or "", re.I):
            self.db.set(f"bday_wished_{d.year}", True)

    # ================= 他 =================
    def routines(self) -> list[dict]:
        return list(self.db.get("his_routine") or [])

    def add_routine(self, when: str, what: str, source: str = "chat") -> bool:
        when, what = (when or "").strip()[:30], (what or "").strip()[:30]
        if not when or not what or not parse_when_spec(when):
            return False
        rs = [r for r in self.routines() if not (r["what"] == what or (r["when"] == when and r["what"] in what))]
        rs.append({"when": when, "what": what, "source": source, "ts": time.time()})
        self.db.set("his_routine", rs[-15:])
        return True

    def del_routine_match(self, when: str, what: str) -> bool:
        rs = self.routines()
        keep = [r for r in rs if not (r["when"] == when and r["what"] == what)]
        if len(keep) != len(rs):
            self.db.set("his_routine", keep)
            return True
        return False

    def del_routine(self, idx: int):
        rs = self.routines()
        if 0 <= idx < len(rs):
            rs.pop(idx)
            self.db.set("his_routine", rs)

    def busy_now(self, now: datetime | None = None) -> str | None:
        now = now or self.brain.now()
        for r in self.routines():
            if routine_active(r["when"], now):
                return r["what"]
        return None

    def learned_times(self) -> dict[str, str]:
        """从手机信号里学：他平时（工作日）几点到公司、几点下班、几点到家。"""
        out = {}
        log_ = self.db.get("phone_events") or []
        for ev in ("到公司/学校了", "下班了", "刚到家", "刚起床"):
            mins = [e["m"] for e in log_ if e["ev"] == ev and e.get("wd", 0) < 5 and time.time() - e["ts"] < 21 * 86400]
            if len(mins) >= 3:
                med = int(statistics.median(mins))
                out[ev] = f"{med // 60:02d}:{med % 60:02d}"
        return out

    def note_phone_event(self, ev: str):
        now = self.brain.now()
        log_ = [e for e in (self.db.get("phone_events") or []) if time.time() - e["ts"] < 30 * 86400]
        log_.append({"ev": ev, "m": now.hour * 60 + now.minute, "wd": now.weekday(), "ts": time.time()})
        self.db.set("phone_events", log_[-200:])

    def his_block(self) -> str:
        rs = self.routines()
        learned = self.learned_times()
        manual = (self.db.get("schedule") or "").strip()
        if not rs and not learned and not manual:
            return ""
        parts = []
        if manual:
            parts.append(f"他自己告诉你的作息：{manual[:120]}")
        if rs:
            parts.append("他的固定安排：" + "；".join(f"{r['when']} {r['what']}" for r in rs[:8]))
        if learned:
            parts.append("（从他手机信号看）工作日他一般" + "，".join(f"{v} {k.replace('了', '')}" for k, v in learned.items()))
        busy = self.busy_now()
        if busy:
            parts.append(f"按他的安排，他现在多半在{busy}，别指望他秒回")
        return "他的日常节奏：" + "；".join(parts)


# ---------------- “周三 19:00-21:00”这类说法 ----------------
DAYSETS = {"每天": set(range(7)), "天天": set(range(7)), "工作日": set(range(5)), "周末": {5, 6}}


DAYCH = "一二三四五六日天"
TIME_RE = re.compile(
    r"(上午|早上|早晨|中午|下午|傍晚|晚上|夜里)?\s*(\d{1,2})(?:\s*[:：点]\s*(\d{1,2}|半)?)?\s*[-~～到至]\s*"
    r"(上午|早上|早晨|中午|下午|傍晚|晚上|夜里|凌晨)?\s*(\d{1,2})(?:\s*[:：点]\s*(\d{1,2}|半)?)?")


def _day_idx(ch: str) -> int:
    return 6 if ch in "日天" else WEEK.index(ch)


def _hm(period: str | None, h: str, m: str | None, ref_pm: int = 0) -> int:
    hh = int(h)
    mm = 30 if m == "半" else int(m or 0)
    if period in ("下午", "傍晚", "晚上", "夜里") and hh < 12:
        hh += 12
    elif period is None and ref_pm and hh < 12 and (hh + 12) * 60 > ref_pm:
        hh += 12                                   # “晚上7点到9点”的 9 是 21 点；“23 点到 1 点”的 1 还是凌晨 1 点
    return hh * 60 + mm


def parse_when_spec(when: str) -> tuple[set[int], int, int] | None:
    """'周三 19:00-21:00' / '工作日 9-18' / '周二周四晚上7点到9点' / '周一到周五 09:00-18:00' / '周末'
    → (星期几集合, 开始分钟, 结束分钟)；只说了哪天没说几点时开始/结束是 -1。"""
    s = (when or "").replace("星期", "周").replace("礼拜", "周").replace("每周", "周").strip()
    days: set[int] = set()
    for k, v in DAYSETS.items():
        if k in s:
            days |= v
    for m in re.finditer(rf"周([{DAYCH}])\s*[到至~～\-]\s*周?([{DAYCH}])", s):      # 周一到周五
        a, b = _day_idx(m.group(1)), _day_idx(m.group(2))
        days |= set(range(a, b + 1)) if a <= b else set(range(a, 7)) | set(range(0, b + 1))
    s2 = re.sub(rf"周([{DAYCH}])\s*[到至~～\-]\s*周?([{DAYCH}])", " ", s)
    for m in re.finditer(rf"周((?:[{DAYCH}][、和,，/ ]?)+)", s2):                      # 周二周四 / 周一三五 / 周二、周四
        for ch in m.group(1):
            if ch in DAYCH:
                days.add(_day_idx(ch))
    if not days:
        return None
    t = TIME_RE.search(s.replace("：", ":"))
    if t:
        p1, h1, m1, p2, h2, m2 = t.groups()
        a = _hm(p1, h1, m1)
        b = _hm(p2 or p1 if p2 or (p1 and p1 not in ("下午", "傍晚", "晚上", "夜里")) else p2, h2, m2,
                ref_pm=a if a >= 12 * 60 else 0)
        if p2 is None and p1 is None and int(h2) < int(h1) and int(h2) + 12 > int(h1) and int(h2) < 12:
            b = _hm(None, str(int(h2) + 12), m2)                                        # “9-6” 理解成 9 点到 18 点
        if a < 24 * 60 and b <= 24 * 60:
            return days, a, b
        return None
    return days, -1, -1                          # 只说了哪天、没说几点（比如“周五加班”）


def routine_active(when: str, now: datetime) -> bool:
    p = parse_when_spec(when)
    if not p:
        return False
    days, a, b = p
    if a < 0:
        return False
    m = now.hour * 60 + now.minute
    if b <= a:                                    # 跨午夜：22:00-02:00
        return (now.weekday() in days and m >= a) or (((now.weekday() - 1) % 7) in days and m < b)
    return now.weekday() in days and a <= m < b
