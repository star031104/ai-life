"""4.2 人物慢慢长：和每个人越来越熟、说出口的喜好不改口、每月整理一次“我是怎样的人”、被纠正了就改、
记着群里约好的事到点提醒 / 事后问结果、偶尔把自己刷到的东西分享进群（有时候是一张照片）。

都挂在已有的流程上，尽量不多花钱：
- 喜好、约好的事：和“整理印象”（digest）同一次调用里顺带写出来（digest_extra_prompt / digest_extra_apply）
- 熟悉度、每月整理：每晚的活（nightly）里做
- 提醒、分享：空闲时（idle_tick）检查，每个群每天有上限
- 纠错：有人回她“不对 / 错了 / 其实是……”时才去对一下她记的知识
这个文件 QQ 版和微信版一样（群设置用 _gk(gid) 取：微信版有按群的设置）。
"""
from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime, timedelta

from aigroup.core.safety import looks_like_order

log = logging.getLogger("aigroup.group")

SCHEMA = """
CREATE TABLE IF NOT EXISTS qq_closeness (
    user_id INTEGER PRIMARY KEY,
    score REAL NOT NULL DEFAULT 0,
    updated TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS qq_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 0,
    who TEXT NOT NULL DEFAULT '',
    what TEXT NOT NULL,
    due REAL NOT NULL,
    all_day INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'ask',
    status TEXT NOT NULL DEFAULT 'open',
    created REAL NOT NULL,
    fired REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS qq_plans_g ON qq_plans(group_id, status, due);
"""

# 熟悉度：分数 → 档位，和每一档该怎么相处（写进“群里的人”）
TIERS = [(0, "刚认识"), (8, "混熟了"), (20, "挺熟"), (40, "很熟"), (70, "老朋友")]
TIER_HINT = ("（熟悉度怎么相处：刚认识→客气点、多问两句；混熟了→可以开点小玩笑；挺熟→可以互损接梗；"
             "很熟→随便损，也会主动关心；老朋友→最随便，也最在意他）")
OWNER_SCORE = 90.0

CORRECT_RE = re.compile(r"(不对|错了|不是这样|不是的|搞错|记错|说错|其实是|应该是|明明是|早就|不是吧|瞎说|乱说|胡说|你记反了)")

DIGEST_EXTRA = """7. 好|话题|你的态度：你（“你”）在聊天里亲口说过的喜好和看法（喜欢 / 讨厌什么、支持哪个队、最爱哪首歌、觉得某个东西怎么样），
   一条一个话题，态度写你的原话意思，12 字以内；只写这段聊天里新说的，最多 3 条，没有就不写。
8. 约|那个人的编号（和第 1 条一样）或“群”|什么事|时间|提醒或问：聊天里约好的、某人要去做的、有确定时间的事（周四晚上开黑、明天考试、下周二面试、周六聚餐），
   时间写成 YYYY-MM-DD HH:MM（只知道哪天就写 YYYY-MM-DD）；大家一起参加的活动写“提醒”（到点前叫一下大家），
   某个人自己的事（考试、面试、比赛、看病）写“问”（过后问他结果）。现在是 {now}，“明天”“周四”按这个算。最多 3 条，只写新的、确定的。
9. 在|那个人的编号|城市|到哪天|为什么：聊天里说到某人现在在哪个城市、或者住在哪（出差、旅游、回老家、住、上学、工作），
   城市写中文名（国外的写英文名，比如 Tokyo）；到哪天写 YYYY-MM-DD，不知道写 -；为什么写 出差 / 旅游 / 回家 / 住 / 上学 / 工作 之一。
   只写确定的、新的，最多 3 条。"""

MONTHLY_PROMPT = """你是%s。下面是你的人设、你这几周回想自己写下的话、你说过的喜好、你在群里说过的自己的事。
请把它们整理成“我是怎样的人”：经历让你慢慢形成的、稳定的性格和口味（不是这周发生了什么）。
- 4~6 行，每行以“- ”开头，每行 30 字以内，用“我”的口吻。
- 前后矛盾的以最近的为准；只出现过一次、不重要的小事不写；不能和人设冲突（名字、身份、年龄、经历不能改）。
- 不写任何群友的隐私，不写“AI、模型、程序”。
只输出这几行。

# 人设
%s

# 原来整理过的（上个月）
%s

# 这几周的回想（从早到晚）
%s

# 你说过的喜好
%s

# 你在群里说过的自己的事
%s"""

FIX_PROMPT = """群友好像在纠正你刚才说的话。下面是聊天和你记得的几条知识，判断：群友说的对不对、要不要改你记的那条。
只有群友给出了具体、可信的更正（日期、名字、数字、结论）才改；只是开玩笑、抬杠、或者你记的那条和这事无关，就不改。
只输出 JSON：{"fix": true 或 false, "id": 要改的那条的编号, "summary": "改好的一两句话（不超过 90 字）"}

# 你记得的知识（编号|话题|内容）
%s

# 最近的聊天
%s"""

SHARE_PICK = """你是%s，一个在群里和朋友们聊天的人。你想刷一刷手机，看看你和群里大家关心的东西有没有新消息。
从下面挑一个最可能有新鲜事的，写一个搜索词（像在搜索框里打的，6~16 个字，带“最新”“新消息”“更新”之类的更好）。
最近已经分享过的话题别再挑。
只输出 JSON：{"query": "搜索词", "topic": "话题（2~8 个字）"}

# 你感兴趣的 / 群里常聊的
%s

# 群里最近的事
%s

# 最近分享过的
%s"""

SHARE_JUDGE = """你是%s。你刚刷到下面这些，想想值不值得随手转给群里的朋友看：
要是新鲜的（最近的更新、比赛结果、新消息）、和大家聊的东西有关、有点意思，就分享；
旧闻、广告、和大家没关系、或者你已经知道的（下面“你知道的”里有），就不分享。
只输出 JSON：{"share": true 或 false, "gist": "你看到了什么，一句话（不超过 50 字）", "topic": "话题（2~8 个字）", "summary": "要点（不超过 90 字）"}

# 你知道的
%s

# 刚刷到的（搜的是：%s）
%s"""

PHOTO_PROMPT = """你是%s。人设：%s
你今天的安排：%s
你想随手拍一张照片发进群里（像朋友在群里晒日常：吃的、桌面、窗外、宠物、路上看到的）。照片里不出现你的脸。
只输出 JSON：{"scene": "照片拍的是什么（具体的画面，20~40 字，别写文字和人脸）", "say": "发图时配的一句话（口语，15 字以内）"}"""


def tier(score: float) -> str:
    name = TIERS[0][1]
    for lo, n in TIERS:
        if score >= lo:
            name = n
    return name


def parse_due(s: str, now: datetime) -> tuple[datetime | None, bool]:
    s = (s or "").strip().replace("：", ":").replace("/", "-")
    for fmt, all_day in (("%Y-%m-%d %H:%M", False), ("%Y-%m-%d", True)):
        try:
            d = datetime.strptime(s[:16] if not all_day else s[:10], fmt).replace(tzinfo=now.tzinfo)
            return d, all_day
        except ValueError:
            continue
    return None, False


class GrowMixin:
    def _gk(self, gid: int):
        fn = getattr(self, "gk", None)
        return fn(gid) if fn else self.g

    def _grow_init(self):
        self.db.conn.executescript(SCHEMA)
        self.db.conn.commit()

    # ================= 熟悉度 =================
    def closeness(self, uid: int) -> float:
        if self.owner_id and int(uid) == self.owner_id:
            return OWNER_SCORE
        r = self.db.conn.execute("SELECT score FROM qq_closeness WHERE user_id=?", (int(uid),)).fetchone()
        if r is not None:
            return float(r["score"])
        p = self.db.conn.execute("SELECT msgs, mentions FROM qq_people WHERE user_id=?", (int(uid),)).fetchone()
        if not p:
            return 0.0
        return min(45.0, 3 * int(p["mentions"] or 0) + min(int(p["msgs"] or 0), 300) / 10)    # 没算过：按老办法估一个起点

    def closeness_label(self, uid: int) -> str:
        return tier(self.closeness(uid))

    def update_closeness(self, day: datetime | None = None) -> int:
        """每晚一次：昨天和她说过话的人熟一点（找她说话最算数，在群里一起聊也算一点）；很久没来往的慢慢淡一点。"""
        day = day or (self.brain.now() - timedelta(days=1))
        start = day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        end = start + 86400
        tag = f"{day:%Y-%m-%d}"
        rows = self.db.conn.execute(
            "SELECT user_id, MAX(name) name, COUNT(*) n, SUM(CASE WHEN to_her=1 THEN 1 ELSE 0 END) th "
            "FROM qq_group_messages WHERE role='user' AND ts>=? AND ts<? GROUP BY user_id", (start, end)).fetchall()
        seen = set()
        events = list(self.db.get("qq_close_events") or [])
        for r in rows:
            uid = int(r["user_id"])
            if self.owner_id and uid == self.owner_id:
                continue
            seen.add(uid)
            old = self.closeness(uid)
            gain = min(6.0, 0.8 * int(r["th"] or 0) + 0.08 * int(r["n"] or 0))
            new = min(100.0, old + gain * (1.0 - old / 130))           # 越熟涨得越慢
            self._set_close(uid, new, tag)
            if tier(new) != tier(old):
                events.append({"ts": time.time(), "uid": uid, "name": r["name"], "from": tier(old), "to": tier(new)})
                log.info("和 %s 的熟悉度：%s → %s", r["name"], tier(old), tier(new))
        for r in self.db.conn.execute("SELECT user_id, score, updated FROM qq_closeness").fetchall():
            if int(r["user_id"]) in seen or r["updated"] == tag:
                continue
            last = self.db.conn.execute("SELECT MAX(ts) t FROM qq_group_messages WHERE user_id=? AND role='user'",
                                        (r["user_id"],)).fetchone()["t"] or 0
            if time.time() - float(last) > 14 * 86400:                 # 两周没来往：每天淡一点（不会掉回刚认识以下太多）
                self._set_close(int(r["user_id"]), max(4.0, float(r["score"]) * 0.99), tag)
        self.db.set("qq_close_events", events[-60:] or None)
        self.db.conn.commit()
        return len(seen)

    def _set_close(self, uid: int, score: float, tag: str):
        self.db.conn.execute("INSERT INTO qq_closeness(user_id, score, updated) VALUES (?,?,?) "
                             "ON CONFLICT(user_id) DO UPDATE SET score=excluded.score, updated=excluded.updated",
                             (uid, round(score, 2), tag))

    # ================= 说出口的喜好 =================
    def prefs(self) -> dict:
        d = self.db.get("qq_prefs") or {}
        return d if isinstance(d, dict) else {}

    def add_pref(self, topic: str, stance: str, gid: int = 0, force: bool = False) -> bool:
        topic, stance = (topic or "").strip()[:12], (stance or "").strip()[:24]
        if not topic or not stance or looks_like_order(stance):
            return False
        d = self.prefs()
        if topic in d and not force:                  # 说出口的就不改口（要改在小屋里改）
            return False
        d[topic] = {"v": stance, "ts": time.time(), "gid": gid}
        if len(d) > 60:
            for k in sorted(d, key=lambda k: d[k].get("ts", 0))[: len(d) - 60]:
                d.pop(k, None)
        self.db.set("qq_prefs", d)
        return True

    def prefs_block(self) -> str:
        d = self.prefs()
        if not d:
            return ""
        items = sorted(d.items(), key=lambda kv: kv[1].get("ts", 0))[-30:]
        return ("# 你的喜好和看法（你亲口说过的，一直是这样，别改口；被问到就照这个说）\n"
                + "\n".join(f"- {k}：{v.get('v')}" for k, v in items))

    # ================= 约好的事 =================
    def digest_extra_prompt(self) -> str:
        from aigroup.core.util import WEEK
        now = self.brain.now()
        return DIGEST_EXTRA.replace("{now}", f"{now:%Y-%m-%d %H:%M} 星期{WEEK[now.weekday()]}")

    def digest_extra_apply(self, gid: int, lines: list[str], ids: list[int]) -> tuple[int, int]:
        """digest 的输出里 “好|” 和 “约|” 两种行。返回（新喜好数，新约定数）。"""
        np_, nplan = 0, 0
        names = self.names(gid)
        for ln in lines:
            m = re.match(r"^\s*(?:\d\.\s*)?好\s*[|｜]\s*([^|｜]+)[|｜]\s*(.+)$", ln)
            if m and self.add_pref(m.group(1), m.group(2), gid):
                np_ += 1
                continue
            m = re.match(r"^\s*(?:\d\.\s*)?在\s*[|｜](.+)$", ln)
            if m and getattr(self, "where", None) is not None and self._gk(gid).get("weather", True):
                parts = [x.strip() for x in re.split(r"[|｜]", m.group(1))]
                if len(parts) >= 2 and parts[0].isdigit() and int(parts[0]) in ids:
                    self._spawn(self.where.set(int(parts[0]), parts[1], parts[3] if len(parts) > 3 else "",
                                               parts[2] if len(parts) > 2 else "", "digest"))
                continue
            m = re.match(r"^\s*(?:\d\.\s*)?约\s*[|｜](.+)$", ln)
            if not m:
                continue
            parts = [x.strip() for x in re.split(r"[|｜]", m.group(1))]
            if len(parts) < 3:
                continue
            who, what, when = parts[0], parts[1][:40], parts[2]
            kind = "remind" if len(parts) > 3 and "提醒" in parts[3] else "ask"
            if not what or looks_like_order(what):
                continue
            uid = int(who) if who.isdigit() and int(who) in ids else 0
            if not uid and who not in ("群", "大家"):
                continue
            if self.add_plan(gid, uid, names.get(str(uid), "") if uid else "大家", what, when, kind):
                nplan += 1
        return np_, nplan

    def add_plan(self, gid: int, uid: int, who: str, what: str, when: str, kind: str = "ask") -> int:
        now = self.brain.now()
        due, all_day = parse_due(when, now)
        if due is None:
            return 0
        if all_day:
            due = due.replace(hour=21 if kind == "ask" else 10)      # 只知道哪天：问结果在晚上，提醒在上午
        ts = due.timestamp()
        if ts < time.time() - 3600 or ts > time.time() + 45 * 86400:
            return 0
        for r in self.db.conn.execute("SELECT what, due FROM qq_plans WHERE group_id=? AND status='open'",
                                      (gid,)).fetchall():
            if abs(float(r["due"]) - ts) < 6 * 3600 and (r["what"] in what or what in r["what"]
                                                       or len(set(r["what"]) & set(what)) >= min(4, len(what))):
                return 0
        pid = self.db.conn.execute(
            "INSERT INTO qq_plans(group_id, user_id, who, what, due, all_day, kind, created) VALUES (?,?,?,?,?,?,?,?)",
            (gid, uid, who or "", what, ts, int(all_day), kind, time.time())).lastrowid
        self.db.conn.commit()
        log.info("群 %s：记下了约定「%s」（%s，%s）", gid, what, f"{due:%m-%d %H:%M}", "提醒" if kind == "remind" else "事后问")
        return int(pid)

    def plans(self, gid: int | None = None, status: str | None = "open", limit: int = 50) -> list[dict]:
        q, a = "SELECT * FROM qq_plans WHERE 1=1", []
        if gid is not None:
            q += " AND group_id=?"
            a.append(gid)
        if status:
            q += " AND status=?"
            a.append(status)
        q += " ORDER BY due " + ("ASC" if status == "open" else "DESC") + " LIMIT ?"
        return [dict(r) for r in self.db.conn.execute(q, (*a, limit)).fetchall()]

    def set_plan(self, pid: int, status: str):
        self.db.conn.execute("UPDATE qq_plans SET status=? WHERE id=?", (status, int(pid)))
        self.db.conn.commit()

    async def plans_tick(self, gid: int, st) -> bool:
        """到点提醒 / 事后问结果。每个群每天最多 plans_per_day 次。说了返回 True。"""
        g = self._gk(gid)
        if not g.get("plans", True):
            return False
        now = time.time()
        self.db.conn.execute("UPDATE qq_plans SET status='expired' WHERE status='open' AND due<?", (now - 30 * 3600,))
        day_key = f"qq_plans_n_{gid}_{self.brain.now():%Y-%m-%d}"
        if int(self.db.get(day_key, 0) or 0) >= int(g.get("plans_per_day", 3)):
            return False
        for p in self.plans(gid):
            due = float(p["due"])
            if p["kind"] == "remind":
                ready = due - 45 * 60 <= now <= due + 15 * 60
                late = now > due + 15 * 60
            else:
                ready = now >= due + (0 if p["all_day"] else 2 * 3600)
                late = now > due + 28 * 3600
            if late:
                self.set_plan(p["id"], "expired")
                continue
            if not ready:
                continue
            self.db.conn.execute("UPDATE qq_plans SET status='done', fired=? WHERE id=?", (now, p["id"]))
            self.db.conn.commit()
            self.db.set(day_key, int(self.db.get(day_key, 0) or 0) + 1)
            at = f"（想 @ 他就写 [@{p['who']}]）" if p["user_id"] else ""
            if p["kind"] == "remind":
                when = datetime.fromtimestamp(due, self.cfg.tz)
                why = (f"之前群里约好了「{p['what']}」（{when:%H:%M}），时间快到了。像朋友那样在群里喊一声提醒大家"
                       f"{'（主要是' + p['who'] + '）' if p['user_id'] else ''}，一句话，自然一点，比如“几点了，人呢”{at}")
            else:
                why = (f"{p['who']}之前说过「{p['what']}」，现在应该已经有结果了。你记着这事，像朋友那样问一句怎么样了"
                       f"（关心，别查户口；他不想说就算了）{at}")
            async with st.lock:
                try:
                    await self.speak(gid, mentioned=False, owner_spoke=False, trigger=None, who=p["who"], why=why)
                except Exception as e:  # noqa: BLE001
                    log.info("群 %s 提醒失败：%s", gid, e)
            return True
        return False

    # ================= 被纠正了就改 =================
    def maybe_fix(self, gid: int, uid: int, text: str):
        """有人回她“不对 / 错了 / 其实是……”：后台对一下她记的知识。"""
        kn = getattr(self, "knowledge", None)
        if kn is None or not CORRECT_RE.search(text or "") or not self._gk(gid).get("fix_knowledge", True):
            return
        last = self.db.conn.execute("SELECT ts, content FROM qq_group_messages WHERE group_id=? AND role='assistant' "
                                    "ORDER BY id DESC LIMIT 1", (gid,)).fetchone()
        if not last or time.time() - float(last["ts"]) > 20 * 60 or not self._allow(f"fix_{gid}", 6):
            return
        self._spawn(self.fix_knowledge(gid, last["content"] + " " + text))

    async def fix_knowledge(self, gid: int, text: str) -> bool:
        kn = self.knowledge
        es = kn.recall(gid, text, k=4)
        if not es:
            return False
        known = "\n".join(f"{e['id']}|{e['topic']}|{e['summary']}" for e in es)
        try:
            d = await self.brain.llm.json([{"role": "user", "content": FIX_PROMPT % (known, self.transcript(gid, 10))}],
                                          default={}, role="helper")
        except Exception as e:  # noqa: BLE001
            log.info("对知识出错：%s", e)
            return False
        if not isinstance(d, dict) or not d.get("fix"):
            return False
        try:
            kid = int(d.get("id"))
        except (TypeError, ValueError):
            return False
        summary = str(d.get("summary") or "").strip()
        if kid not in {e["id"] for e in es} or not summary or looks_like_order(summary):
            return False
        kn.update(kid, summary=summary, source="fixed")
        log.info("群 %s：被纠正了，改了她记的「%s」", gid, next(e["topic"] for e in es if e["id"] == kid))
        return True

    # ================= 每月整理“我是怎样的人” =================
    async def monthly_self(self, force: bool = False) -> int:
        month = f"{self.brain.now():%Y-%m}"
        n = 0
        for gid in sorted(self.groups):
            key = f"qq_selfcore_month_{gid}"
            if self.db.get(key) == month and not force:
                continue
            hist = self.db.get(f"qq_reflect_hist_{gid}") or []
            if len(hist) < 2 and not force:
                continue
            p = self.brain.persona
            prefs = "\n".join(f"- {k}：{v.get('v')}" for k, v in self.prefs().items()) or "（没有）"
            prompt = MONTHLY_PROMPT % (p.name, p.body[:1500], self.db.get(f"qq_selfcore_{gid}") or "（还没有）",
                                       "\n".join(f"[{h.get('week')}]\n{h.get('text')}" for h in hist[-8:])[:2400] or "（没有）",
                                       prefs[:800], (self.self_notes(gid) or "（没有）")[:600])
            try:
                raw = await self.brain.llm.chat([{"role": "user", "content": prompt}], role="helper", max_tokens=400,
                                                temperature=0.5, background=True)
            except Exception as e:  # noqa: BLE001
                log.info("群 %s 每月整理失败：%s", gid, e)
                continue
            lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip().startswith("-")][:6]
            lines = [x for x in lines if not re.search(r"AI|模型|程序|提示词", x)]
            if lines:
                self.db.set(f"qq_selfcore_{gid}", "\n".join(lines)[:500])
                self.db.set(key, month)
                n += 1
                log.info("群 %s：她整理了一遍“我是怎样的人”（%d 句）", gid, len(lines))
        return n

    # ================= 偶尔分享自己刷到的 =================
    def shares(self, gid: int | None = None) -> list[dict]:
        out = list(self.db.get("qq_shares") or [])
        return [x for x in out if gid is None or x.get("gid") == gid]

    def _note_share(self, gid: int, kind: str, text: str, topic: str = ""):
        out = list(self.db.get("qq_shares") or [])
        out.append({"ts": time.time(), "gid": gid, "kind": kind, "text": text[:120], "topic": topic})
        self.db.set("qq_shares", out[-80:])
        k = f"qq_share_n_{gid}_{self.brain.now():%Y-%m-%d}"
        self.db.set(k, int(self.db.get(k, 0) or 0) + 1)

    def share_due(self, gid: int, now: float, step_minutes: float = 1.0) -> bool:
        g = self._gk(gid)
        per_day = int(g.get("share_per_day", 1))
        if per_day <= 0:
            return False
        if int(self.db.get(f"qq_share_n_{gid}_{self.brain.now():%Y-%m-%d}", 0) or 0) >= per_day:
            return False
        h = self.brain.now().hour
        if not 10 <= h < 23:
            return False
        last = self.db.conn.execute("SELECT ts, role FROM qq_group_messages WHERE group_id=? ORDER BY id DESC LIMIT 1",
                                    (gid,)).fetchone()
        if not last or last["role"] == "assistant" or now - float(last["ts"]) < 20 * 60:   # 正聊得热闹 / 最后一句是她：先不插
            return False
        if now - float(last["ts"]) > 3 * 86400:            # 群已经好几天没人说话：别自说自话
            return False
        # 一天大约 13 个小时可以分享；share_chance = 一天里分享的概率
        return random.random() < float(g.get("share_chance", 0.6)) * step_minutes / (13 * 60)

    async def share(self, gid: int, st, force_photo: bool | None = None) -> bool:
        """刷到点东西发进群：多数时候是和大家兴趣有关的新消息，有时候是一张自己的日常照片。"""
        g = self._gk(gid)
        photo = getattr(self, "photos", None)
        want_photo = force_photo if force_photo is not None else (
            photo is not None and photo.ready and random.random() < float(g.get("photo_share_chance", 0.3)))
        if want_photo and photo is not None and photo.ready and photo.remaining_today() > 0:
            return await self._share_photo(gid, st)
        if not getattr(self, "web", None) or not self.web.enabled:
            return False
        p = self.brain.persona
        kn = getattr(self, "knowledge", None)
        likes = ", ".join(e["topic"] for e in sorted(kn.entries(gid, 60), key=lambda e: -int(e["hits"] or 0))[:10]) if kn else ""
        mine = ", ".join(self.interests()[:10]) if hasattr(self, "interests") else ""
        recent = ", ".join(x.get("topic") or x.get("text", "")[:12] for x in self.shares(gid)[-6:]) or "（没有）"
        try:
            d = await self.brain.llm.json([{"role": "user", "content": SHARE_PICK % (
                p.name, f"{mine}；{likes}"[:400] or "（不太清楚）", (self.db.get(f"qq_group_notes_{gid}") or "（没有）")[:500],
                recent)}], default={}, role="helper")
        except Exception as e:  # noqa: BLE001
            log.info("挑分享话题失败：%s", e)
            return False
        q = str((d or {}).get("query") or "").strip()[:30]
        if len(q) < 2:
            return False
        res = await self.web.search(q)
        if not res:
            return False
        known = "\n".join(f"- {e['topic']}：{e['summary']}" for e in (kn.entries(gid, 30) if kn else []))[:1200] or "（没有）"
        found = "\n".join(f"- {r['title']}：{r['snippet']}" for r in res[:5])
        try:
            j = await self.brain.llm.json([{"role": "user", "content": SHARE_JUDGE % (p.name, known, q, found)}],
                                          default={}, role="helper")
        except Exception as e:  # noqa: BLE001
            log.info("判断要不要分享失败：%s", e)
            return False
        if not isinstance(j, dict) or not j.get("share") or not j.get("gist"):
            self._note_share(gid, "skip", q, str((d or {}).get("topic") or ""))     # 算一次：今天别再刷了
            return False
        topic = str(j.get("topic") or d.get("topic") or q)[:20]
        if kn is not None and j.get("summary"):
            kn.add(gid, topic, [topic, *re.split(r"\s+", q)[:3]], str(j["summary"]), "share", q)
        why = (f"你刚在手机上刷到：{j['gist']}。觉得群里的人会感兴趣，随手分享一句（像朋友在群里说“诶你们看到没……”），"
               "可以带一句自己的看法；别说“我搜到”“根据”，别发链接，一两句话就够")
        async with st.lock:
            try:
                ok = await self.speak(gid, mentioned=False, owner_spoke=False, trigger=None, who="", why=why,
                                      web=self.web.block(q, res))
            except Exception as e:  # noqa: BLE001
                log.info("群 %s 分享失败：%s", gid, e)
                return False
        self._note_share(gid, "news" if ok else "skip", str(j["gist"]), topic)
        return bool(ok)

    async def _share_photo(self, gid: int, st) -> bool:
        p = self.brain.persona
        life = self.db.get(f"life_{self.brain.now():%Y-%m-%d}", "") or "（普通的一天）"
        try:
            d = await self.brain.llm.json([{"role": "user", "content": PHOTO_PROMPT % (
                p.name, p.body[:900], str(life)[:400])}], default={}, role="helper")
        except Exception as e:  # noqa: BLE001
            log.info("想照片失败：%s", e)
            return False
        scene, say = str((d or {}).get("scene") or "").strip(), str((d or {}).get("say") or "").strip()[:30]
        if len(scene) < 6 or looks_like_order(say):
            return False
        data = await self.photos.make(scene)
        if not data:
            return False
        from aigroup.qq.onebot import image_seg, text_seg
        async with st.lock:
            try:
                mid = await self.ob.send_group(gid, [image_seg(data)])
                self._record(gid, self.ob.self_id or 0, p.name, f"[图片：{scene}]", mid, role="assistant")
                if say:
                    mid2 = await self.ob.send_group(gid, [text_seg(say)])
                    self._record(gid, self.ob.self_id or 0, p.name, say, mid2, role="assistant")
            except Exception as e:  # noqa: BLE001
                log.info("群 %s 发照片失败：%s", gid, e)
                return False
        self._note_share(gid, "photo", f"{say}（{scene}）", "照片")
        log.info("群 %s：发了一张照片（%s）", gid, scene)
        return True

    # ================= 群友在哪、那边天气（4.4） =================
    def maybe_where(self, gid: int, uid: int, text: str):
        """群友自己说“我在珠海出差 / 到成都了”：查一下是不是真的城市，是就记下。"""
        wh = getattr(self, "where", None)
        if wh is None or not self._gk(gid).get("weather", True):
            return
        from aigroup.net.where import said_where
        got = said_where(text)
        if got and self._allow(f"where_{uid}", 3):
            self._spawn(wh.set(uid, got[0], got[1], "", "said"))

    async def weather_tick(self, gid: int, st) -> bool:
        """群友在外地、那边今天 / 明天要变天：像朋友那样提醒一句（每个群每天最多一次）。"""
        wh = getattr(self, "where", None)
        g = self._gk(gid)
        if wh is None or not g.get("weather", True) or not g.get("weather_care", True):
            return False
        now = self.brain.now()
        day = f"{now:%Y-%m-%d}"
        last = float(self.db.get(f"qq_wxcheck_{gid}", 0) or 0)
        if time.time() - last < 30 * 60 or self.db.get(f"qq_wxcare_n_{gid}_{day}"):
            return False
        self.db.set(f"qq_wxcheck_{gid}", time.time())
        target = "today" if now.hour < 15 else "tomorrow"
        for p in wh.all():
            if p["kind"] != "trip" or p.get("lat") is None:
                continue
            r = self.db.conn.execute("SELECT name FROM qq_group_messages WHERE group_id=? AND user_id=? AND ts>? "
                                     "ORDER BY id DESC LIMIT 1", (gid, p["user_id"], time.time() - 3 * 86400)).fetchone()
            if not r:
                continue
            key = f"qq_wxcare_{p['user_id']}_{day}_{target}"
            if self.db.get(key):
                continue
            w = await wh.weather(p["lat"], p["lon"])
            a = wh.alert((w or {}).get(target) or {})
            if not a:
                continue
            self.db.set(key, True)
            self.db.set(f"qq_wxcare_n_{gid}_{day}", True)
            d = w[target]
            when = "今天" if target == "today" else "明天"
            why = (f"{r['name']}这几天在{p['city']}{p['why'] or ''}，那边{when}{a}（{d['text']} {d['min']}~{d['max']}°C）。"
                   f"像朋友那样提醒他一句（想 @ 他就写 [@{r['name']}]），一两句，别像天气预报")
            async with st.lock:
                try:
                    await self.speak(gid, mentioned=False, owner_spoke=False, trigger=None, who=r["name"], why=why)
                except Exception as e:  # noqa: BLE001
                    log.info("群 %s 天气提醒没说成：%s", gid, e)
            return True
        return False

    def where_list(self) -> list[dict]:
        wh = getattr(self, "where", None)
        if wh is None:
            return []
        names = {int(r["user_id"]): r["name"] for r in self.db.conn.execute("SELECT user_id, name FROM qq_people").fetchall()}
        out = []
        for p in wh.all():
            w = wh._wx.get(f"{p['lat']:.2f},{p['lon']:.2f}") if p.get("lat") is not None else None
            out.append({"uid": p["user_id"], "name": names.get(int(p["user_id"]), str(p["user_id"])), "city": p["city"],
                        "kind": p["kind"], "why": p["why"], "until": p["until"], "src": p["src"],
                        "weather": wh.brief(w[1]) if w and w[1] else ""})
        return out

    # ================= 生日和节日（4.3） =================
    def birthdays(self) -> dict:
        d = self.db.get("qq_birthdays") or {}
        return d if isinstance(d, dict) else {}

    def set_birthday(self, uid: int, day: str) -> bool:
        day = (day or "").strip().replace("/", "-").replace(".", "-").replace("月", "-").replace("日", "").replace("号", "")
        d = self.birthdays()
        if not day:
            d.pop(str(uid), None)
            self.db.set("qq_birthdays", d or None)
            return True
        m = re.match(r"^(\d{1,2})-(\d{1,2})$", day)
        if not m or not (1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31):
            return False
        d[str(int(uid))] = f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        self.db.set("qq_birthdays", d)
        return True

    @staticmethod
    def festival(day) -> str:
        """群里值得应个景的节日（节气只算冬至）。"""
        from aigroup.core.schedule import festival_today
        names = [x for x in (festival_today(day) or "").split("、") if x]
        jieqi = {"立春", "雨水", "惊蛰", "春分", "清明", "谷雨", "立夏", "小满", "芒种", "夏至", "小暑", "大暑", "立秋", "处暑",
                 "白露", "秋分", "寒露", "霜降", "立冬", "小雪", "大雪", "小寒", "大寒"}
        return "、".join(x for x in names if x not in jieqi)

    def upcoming(self, days: int = 30) -> list[dict]:
        """接下来一个月的生日和节日（小屋里看）。"""
        names = {str(r["user_id"]): r["name"] for r in self.db.conn.execute("SELECT user_id, name FROM qq_people").fetchall()}
        today = self.brain.now().date()
        out = []
        for i in range(days):
            d = today + timedelta(days=i)
            for uid, md in self.birthdays().items():
                if md == f"{d:%m-%d}":
                    out.append({"day": f"{d:%m-%d}", "in": i, "kind": "birthday", "name": names.get(uid, uid), "uid": uid})
            f = self.festival(d)
            if f:
                out.append({"day": f"{d:%m-%d}", "in": i, "kind": "festival", "name": f})
        return out

    async def celebrate_tick(self, gid: int, st) -> bool:
        """每个群每天一次：今天有群友过生日 / 是节日，上午十点以后在群里说一声。说了返回 True。"""
        if not self._gk(gid).get("celebrate", True):
            return False
        now = self.brain.now()
        key = f"qq_celebrate_{gid}_{now:%Y-%m-%d}"
        if self.db.get(key) or not 10 <= now.hour < 22:
            return False
        self.db.set(key, True)
        md = f"{now:%m-%d}"
        since = time.time() - 90 * 86400
        who = []
        for uid, d in self.birthdays().items():
            if d != md:
                continue
            r = self.db.conn.execute("SELECT name FROM qq_group_messages WHERE group_id=? AND user_id=? AND ts>? "
                                     "ORDER BY id DESC LIMIT 1", (gid, int(uid), since)).fetchone()
            if r:                                           # 只在他常待的群里祝（别的群的人不认识他）
                who.append(r["name"])
        fest = self.festival(now.date())
        if not who and not fest:
            return False
        bits = []
        if who:
            names = "、".join(who)
            bits.append(f"今天是{names}的生日，你记得。在群里祝{'他们' if len(who) > 1 else '他'}生日快乐（想 @ 就写 [@{who[0]}]），"
                        "像朋友那样，可以带一句只有你们懂的梗，别像群发的祝福短信")
        if fest:
            bits.append(f"今天是{fest}，应个景说一句（随口的祝福、或者约大家做点应景的事），别像群发的祝福短信")
        why = "；".join(bits) + "。一两句话，这次一定要说"
        async with st.lock:
            try:
                await self.speak(gid, mentioned=False, owner_spoke=False, trigger=None, who=who[0] if who else "", why=why)
            except Exception as e:  # noqa: BLE001
                log.info("群 %s 祝生日 / 过节没说成：%s", gid, e)
                return False
        log.info("群 %s：%s", gid, "祝了生日：" + "、".join(who) if who else "过节：" + fest)
        return True

    # ================= 小屋“她的日子”用 =================
    def life_snapshot(self, gid: int | None) -> dict:
        gids = [gid] if gid else sorted(self.groups)
        g0 = gids[0] if gids else None
        people = []
        for r in self.db.conn.execute("SELECT user_id, name, msgs FROM qq_people ORDER BY last_seen DESC LIMIT 60").fetchall():
            if self.ob is not None and getattr(self.ob, "self_id", None) and int(r["user_id"]) == int(self.ob.self_id):
                continue
            sc = self.closeness(int(r["user_id"]))
            people.append({"uid": r["user_id"], "name": r["name"], "score": round(sc, 1), "tier": tier(sc),
                           "owner": bool(self.owner_id and int(r["user_id"]) == self.owner_id)})
        people.sort(key=lambda x: -x["score"])
        kn = getattr(self, "knowledge", None)
        know = []
        for x in gids:
            know += [dict(e, gid=x) for e in (kn.entries(x, 30) if kn else [])]
        know.sort(key=lambda e: -e["ts"])
        plans_open = [p for x in gids for p in self.plans(x)]
        plans_done = sorted([p for x in gids for p in self.plans(x, "done", 10)], key=lambda p: -p["due"])[:10]
        photo = getattr(self, "photos", None)
        return {
            "reflect": self.db.get(f"qq_reflect_{g0}") or "" if g0 else "",
            "reflect_hist": (self.db.get(f"qq_reflect_hist_{g0}") or [])[-8:] if g0 else [],
            "selfcore": self.db.get(f"qq_selfcore_{g0}") or "" if g0 else "",
            "prefs": [{"topic": k, "v": v.get("v"), "ts": v.get("ts")} for k, v in
                      sorted(self.prefs().items(), key=lambda kv: -kv[1].get("ts", 0))],
            "people": people[:40],
            "close_events": list(reversed(self.db.get("qq_close_events") or []))[:20],
            "plans": plans_open, "plans_done": plans_done,
            "shares": list(reversed([s for s in self.shares() if s.get("kind") != "skip" and (not gid or s.get("gid") == gid)]))[:20],
            "knowledge": [{"id": e["id"], "gid": e["gid"], "topic": e["topic"], "summary": e["summary"],
                           "source": e["source"], "ts": e["ts"], "hits": e["hits"]} for e in know[:24]],
            "photo_ready": bool(photo is not None and photo.ready),
            "birthdays": [{"uid": p["uid"], "name": p["name"], "day": self.birthdays().get(str(p["uid"]), "")}
                          for p in people[:40]],
            "upcoming": self.upcoming(),
            "where": self.where_list(),
        }
