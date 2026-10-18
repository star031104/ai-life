"""长期记忆：从聊天里整理“关于他的事”和“她自己说过的事”、混合检索、每晚写日记、每天编排她自己的生活。

整理分两步（参考 mem0）：
1. 从新聊天里抽出候选：事实（关于他 / 关于她自己 / 你们共同的）、重要日子、追番、没聊完的话题、你们的口头禅和梗。
2. 每条候选先去库里找最像的几条旧记忆，再让模型决定：新增 / 更新旧的 / 删掉过时的 / 不用记。
   所以不管记了多少条，旧记忆都能被更新和去重。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta

from .retrieval import Embedder, cosine_dupe, from_blob, hybrid_rank, to_blob, tokens

log = logging.getLogger("ailife.memory")

OWNER_LABEL = {"user": "他", "self": "你自己", "us": "你们"}
ABOUT = {"他": "user", "我": "self", "她": "self", "我们": "us", "你们": "us"}


def calendar_hint(now: datetime) -> str:
    """把本周、下周每天的日期列出来，免得模型把“下周二”算错。"""
    monday = now - timedelta(days=now.weekday())
    wk = "一二三四五六日"
    def week(start):
        return " ".join(f"{(start + timedelta(days=i)):%m-%d}({wk[i]})" for i in range(7))
    return f"本周：{week(monday)}\n下周：{week(monday + timedelta(days=7))}"


def similarity(a: str, b: str) -> float:
    """字面相似度（0~1），只用来快速去重。"""
    ga, gb = set(tokens(a)), set(tokens(b))
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / (len(ga) * len(gb)) ** 0.5


class Memory:
    EXTRACT_EVERY = 8          # 每累计多少条他的新消息整理一次

    def __init__(self, db, llm, persona, cfg):
        self.bible = lambda query="": ""          # 她身边的人（Brain 里换成 HerWorld.bible）
        self.on_routine = None                    # 他的固定安排（Brain 里换成 Rhythm.add_routine）
        self.db, self.llm, self.persona, self.cfg = db, llm, persona, cfg
        self.embedder = Embedder(cfg)
        self._lock = asyncio.Lock()

    # ================= 检索 =================
    async def _ensure_vecs(self, rows) -> dict[int, object]:
        model = self.embedder.model
        have = {i: from_blob(b) for i, b in self.db.get_vecs(model).items()}
        missing = [r for r in rows if r["id"] not in have]
        if missing and self.embedder.available:
            vecs = await self.embedder.embed([r["content"] for r in missing[:96]])
            if vecs:
                for r, v in zip(missing, vecs):
                    self.db.set_vec(r["id"], model, to_blob(v))
                    have[r["id"]] = v
        return have

    def _rank(self, rows, query, qvec, have, limit, core_min_imp=4, core_share=0.5):
        now = time.time()
        docs = [r["content"] for r in rows]
        vecs = [have.get(r["id"]) for r in rows] if have else None
        fused = dict(hybrid_rank(query, docs, vecs, qvec, top=max(limit * 2, 20)))
        scored = []
        for i, r in enumerate(rows):
            age = (now - r["ts"]) / 86400
            s = fused.get(i, 0.0) * 60 + r["importance"] * 0.08 + 0.1 / (1 + age / 7)
            scored.append((s, r))
        scored.sort(key=lambda x: -x[0])
        core = [r for _, r in scored if r["importance"] >= core_min_imp][: int(limit * core_share)]
        picked = {r["id"] for r in core}
        rest = [r for _, r in scored if r["id"] not in picked][: limit - len(core)]
        return core + rest

    async def recall(self, query: str, owner: str = "user", limit: int = 12) -> list:
        """混合检索：关键词 + 向量。owner='user' 关于他（含共同的），'self' 她自己说过的。"""
        rows = self.db.all_memories(owner)
        if not rows:
            return []
        have, qvec = {}, None
        if self.embedder.available:
            try:
                have = await self._ensure_vecs(rows)
                qv = await self.embedder.embed([query[-500:]]) if query.strip() else None
                qvec = qv[0] if qv else None
            except Exception as e:  # noqa: BLE001
                log.warning("向量检索出错，改用关键词：%s", e)
        chosen = self._rank(rows, query, qvec, have, limit,
                            core_share=0.5 if owner == "user" else 0.4)
        self.db.touch_memories([m["id"] for m in chosen])
        return chosen

    def relevant(self, query: str, limit: int = 12, owner: str = "user") -> list:
        """同步版（打电话时用）：只用关键词 + 已经算好的向量。"""
        rows = self.db.all_memories(owner)
        if not rows:
            return []
        return self._rank(rows, query, None, None, limit)

    # ================= 整理 =================
    async def maybe_extract(self, force: bool = False):
        if self._lock.locked():
            return
        async with self._lock:
            await self._extract(force)

    def _convo(self, rows) -> str:
        lines = []
        for r in rows:
            who = "他" if r["role"] == "user" else self.persona.name if r["role"] == "assistant" else "记录"
            t = datetime.fromtimestamp(r["ts"], self.cfg.tz)
            lines.append(f"[{t:%m-%d %H:%M}] {who}：{r['content']}")
        return "\n".join(lines)[-7000:]

    async def _extract(self, force: bool):
        last_id = self.db.get("mem_last_id", 0)
        rows = self.db.messages_after_id(last_id)
        user_rows = [r for r in rows if r["role"] == "user"]
        if not rows or (len(user_rows) < self.EXTRACT_EVERY and not force):
            return
        if not user_rows and not any(r["role"] == "assistant" for r in rows):
            self.db.set("mem_last_id", rows[-1]["id"])
            return
        now = datetime.now(self.cfg.tz)
        threads = "\n".join(f"- {t['topic']}" for t in self.db.open_threads(10)) or "（无）"
        name = self.persona.name
        prompt = f"""你在帮{name}整理记忆。今天是 {now:%Y-%m-%d}（星期{"一二三四五六日"[now.weekday()]}）。
{calendar_hint(now)}
下面是新的聊天（每行前面是发消息的时间）：
{self._convo(rows)}

目前还挂着、没聊完的话题：
{threads}

请找出：
1. facts：值得长期记住的事实，每条一句话、写清主语。about 填：
   "他"＝关于他：身份、喜好、厌恶、习惯、身边的人、近期计划和大事（带具体日期）、他明确表达的感受；
   "我"＝{name}自己在聊天里说出口的、关于她自己的具体设定或经历（室友/朋友名字、社团、课程、在玩的游戏、发生过的事、她答应过的事），用“我……”的第一人称写，以后要和这些保持一致；
   "我们"＝你们之间的约定、共同的回忆、重要时刻。
   不要记寒暄和一时的情绪。重要度 1-5：5=核心事实或重要约定，3=一般偏好，1=琐碎。
   ⚠️ 关于“他”的事实只能来自他自己说的话、做的事；{name}的猜测、调侃、抱怨（比如“你是不是又熬夜打游戏了”）不算，他没承认就不要记。
2. dates：具体日期的重要日子（生日、纪念日、考试、面试、出差…）。“明天”“下周三”按那句话的发送时间换算。每年重复的写 MM-DD，一次性的写 YYYY-MM-DD。
3. shows：约好一起追的番（weekday 0=周一…6=周日，time 为 HH:MM）。
4. threads_open：聊到一半、或在等结果的事（“面试结果下周出”“说好要推荐她一部电影”），一句话；threads_closed：上面挂着的话题里已经有结论的，原样写出来。
5. phrases：他常用的口头禅、你们之间的梗或昵称（必须是在聊天里真实出现、带点个人特色的说法，不要普通词），附一句意思。
6. routine：他自己明确说到的、每周都会重复的固定安排（{name}问的、猜的不算）（上班时间、固定的课、固定加班、健身、通勤……），
   when 写成“周三 19:00-21:00”“工作日 09:00-18:00”“周五”这样的格式，what 写在干什么；一次性的事不算。
只根据上面的聊天，没有提到的绝对不要编；没有就给空数组。只输出 JSON，格式如下（尖括号里换成真实内容）：
{{"facts":[{{"about":"他|我|我们","content":"<一句话>","importance":<1-5>}}],
"dates":[{{"said":"<聊天原话里的时间说法，如 下周二、11月20号>","date":"<YYYY-MM-DD 或 MM-DD>","name":"<名称>"}}],
"shows":[{{"name":"<番名>","weekday":<0-6>,"time":"<HH:MM>"}}],
"threads_open":["<一句话>"],"threads_closed":["<原样>"],
"phrases":[{{"phrase":"<说法>","meaning":"<意思>"}}],
"routine":[{{"when":"<周几 HH:MM-HH:MM>","what":"<在干什么>"}}]}}"""
        data = await self.llm.json([{"role": "user", "content": prompt}], default=None, role="memory")
        if not isinstance(data, dict):
            return
        facts = []
        for f in data.get("facts") or []:
            if isinstance(f, dict) and str(f.get("content") or "").strip():
                try:
                    imp = max(1, min(5, int(f.get("importance") or 3)))
                except (TypeError, ValueError):
                    imp = 3
                owner = ABOUT.get(str(f.get("about") or "他").strip(), "user")
                facts.append({"content": str(f["content"]).strip()[:300], "importance": imp, "owner": owner})
        added, updated, deleted = await self._merge(facts)
        self._save_dates(data.get("dates"), rows)
        self._save_shows(data.get("shows"))
        for t in data.get("threads_closed") or []:
            if isinstance(t, str) and t.strip():
                self.db.close_threads_matching(t.strip())
        for t in data.get("threads_open") or []:
            if isinstance(t, str) and t.strip():
                self.db.add_thread(t.strip()[:120])
        for ph in data.get("phrases") or []:
            if isinstance(ph, dict) and 1 < len(str(ph.get("phrase") or "").strip()) <= 20:
                eid = self.db.add_expression(str(ph["phrase"]).strip(), str(ph.get("meaning") or "")[:80])
                row = next((e for e in self.db.expressions() if e["id"] == eid), None)
                if row and row["status"] == "pending" and row["seen"] >= 2:
                    self.db.set_expression(eid, status="approved")     # 出现过两次就自动通过
        for r in data.get("routine") or []:
            if isinstance(r, dict) and self.on_routine:
                try:
                    if self.on_routine(str(r.get("when") or ""), str(r.get("what") or "")):
                        log.info("记下他的固定安排：%s %s", r.get("when"), r.get("what"))
                except Exception as e:  # noqa: BLE001
                    log.debug("记作息失败：%s", e)
        log.info("记忆已整理：+%d ~%d -%d", added, updated, deleted)
        self.db.set("mem_last_id", rows[-1]["id"])       # 只有成功才往前推进

    async def _merge(self, facts: list[dict]) -> tuple[int, int, int]:
        """每条候选先找最像的旧记忆：明显重复就跳过，可能冲突的交给模型判断，其它直接新增。"""
        uniq: list[dict] = []
        for f in facts:                      # 同一批里重复的先去掉
            if not any(f["owner"] == u["owner"] and similarity(f["content"], u["content"]) >= 0.8 for u in uniq):
                uniq.append(f)
        facts = uniq
        if not facts:
            return 0, 0, 0
        cvecs = await self.embedder.embed([f["content"] for f in facts]) if self.embedder.available else None
        added = updated = deleted = 0
        to_judge = []          # (候选下标, 邻居列表)
        for i, f in enumerate(facts):
            rows = self.db.all_memories("self" if f["owner"] == "self" else "user")
            if not rows:
                added += self._add(f, cvecs[i] if cvecs else None)
                continue
            have = await self._ensure_vecs(rows) if cvecs else {}
            vecs = [have.get(r["id"]) for r in rows]
            qv = cvecs[i] if cvecs else None
            if qv is not None:
                d = cosine_dupe(qv, vecs, 0.93)
                if d is not None:
                    continue
            if any(similarity(f["content"], r["content"]) >= 0.85 for r in rows):
                continue
            ranked = hybrid_rank(f["content"], [r["content"] for r in rows], vecs, qv, top=5)
            neigh = [rows[j] for j, _ in ranked]
            if qv is not None:
                import numpy as np
                neigh = [r for r in neigh if have.get(r["id"]) is None
                         or float(np.dot(qv, have[r["id"]])) >= 0.55]
            if neigh:
                to_judge.append((i, neigh))
            else:
                added += self._add(f, qv)
        if to_judge:
            a, u, d = await self._judge(facts, to_judge, cvecs)
            added, updated, deleted = added + a, updated + u, deleted + d
        return added, updated, deleted

    def _add(self, f: dict, vec=None) -> int:
        rows = self.db.all_memories("self" if f["owner"] == "self" else "user")
        if any(similarity(f["content"], r["content"]) >= 0.85 for r in rows):
            return 0
        mid = self.db.add_memory(f["content"], f["importance"], f["owner"])
        if vec is not None:
            self.db.set_vec(mid, self.embedder.model, to_blob(vec))
        return 1

    async def _judge(self, facts, to_judge, cvecs) -> tuple[int, int, int]:
        blocks = []
        for i, neigh in to_judge:
            f = facts[i]
            old = "\n".join(f"    #{r['id']} {r['content']}（重要度{r['importance']}）" for r in neigh)
            blocks.append(f"候选{i}（关于{OWNER_LABEL[f['owner']]}，重要度{f['importance']}）：{f['content']}\n  相似的旧记忆：\n{old}")
        prompt = ("你在维护一份记忆库。对每条候选，对照它下面的旧记忆，选一个操作：\n"
                  "add＝是新信息，新增；update＝是旧记忆的新进展或更正，给出合并后的完整一句话并填旧记忆 id；"
                  "delete＝候选说明某条旧记忆已经过时或不对了（填 id），同时候选本身会被新增；skip＝旧记忆已经包含，不用记。\n\n"
                  + "\n\n".join(blocks)
                  + '\n\n只输出 JSON：{"ops":[{"i":0,"op":"update","id":12,"content":"合并后的一句话","importance":4}]}')
        data = await self.llm.json([{"role": "user", "content": prompt}], default=None, role="memory")
        a = u = d = 0
        done = set()
        ops = (data.get("ops") or []) if isinstance(data, dict) else []
        allowed = {i: {r["id"] for r in neigh} for i, neigh in to_judge}
        for op in ops:
            try:
                i = int(str(op.get("i")).lstrip("候选#"))
                f = facts[i]
            except (TypeError, ValueError, IndexError, AttributeError):
                continue
            if i in done or i not in allowed:
                continue
            done.add(i)
            kind = str(op.get("op") or "").lower()
            vec = cvecs[i] if cvecs else None
            try:
                mid = int(str(op.get("id") or "").strip().lstrip("#"))
            except ValueError:
                mid = None
            if mid not in allowed[i]:           # 只能动这条候选自己的“相似旧记忆”
                mid = None
            if kind == "update" and mid:
                imp = op.get("importance")
                self.db.update_memory(mid, content=str(op.get("content") or f["content"])[:300],
                                      importance=max(1, min(5, int(imp))) if str(imp).isdigit() else None)
                u += 1
            elif kind == "delete" and mid:
                self.db.delete_memory(mid)
                a += self._add(f, vec)
                d += 1
            elif kind == "skip":
                continue
            else:
                a += self._add(f, vec)
        for i, _ in to_judge:                   # 模型漏掉的候选：保守地新增（_add 会去重）
            if i not in done:
                a += self._add(facts[i], cvecs[i] if cvecs else None)
        return a, u, d

    def _save_dates(self, items, rows=()):
        from .output import normalize_date, resolve_day
        absolute = re.compile(r"(\d{4}\s*[年./-])?\s*(\d{1,2}|[一二三四五六七八九十]+)\s*[月./-]\s*[\d一二三四五六七八九十]")
        for dt in items or []:
            if not (isinstance(dt, dict) and (dt.get("date") or dt.get("said")) and dt.get("name")):
                continue
            name = str(dt["name"])[:40]
            nd = normalize_date(str(dt.get("date") or ""))
            said = str(dt.get("said") or "").strip()
            if said and not absolute.search(said):
                # 相对说法（下周二、明天、20号…）按说这句话的那天用代码换算，比模型算得准
                base_row = next((r for r in reversed(list(rows)) if said in r["content"]), None)
                base = datetime.fromtimestamp(base_row["ts"], self.cfg.tz) if base_row else datetime.now(self.cfg.tz)
                d = resolve_day(said, base)
                if d:
                    yearly = "生日" in name or "纪念" in name or (nd is not None and len(nd) == 5)
                    nd = f"{d:%m-%d}" if yearly else d.isoformat()
            if nd:
                kind = "birthday" if "生日" in name else "anniversary" if len(nd) == 5 else "event"
                self.db.add_date(nd, name, kind, 3 if kind != "event" else 1)

    def _save_shows(self, items):
        for sh in items or []:
            try:
                wd, t = int(sh["weekday"]), str(sh["time"])
                hh, mm = map(int, t.split(":"))
                if sh.get("name") and 0 <= wd <= 6:
                    sid = self.db.add_show(str(sh["name"]).strip("《》")[:40], wd, f"{hh:02d}:{mm:02d}")
                    if hasattr(self, "on_show_added"):
                        self.on_show_added(sid)
            except (KeyError, TypeError, ValueError):
                continue

    # 他刚说的话里有“具体日期 + 大事”，而她没打 [纪念日] 标签时，马上整理一次（兜底，平时不额外调用）
    EVENT_HINT = re.compile(r"(\d{1,2}\s*[月/.-]\s*\d{1,2}|明天|后天|下周|下个月|周[一二三四五六日天]).{0,12}"
                            r"(生日|纪念日|周年|考试|考研|面试|答辩|出差|体检|手术|比赛|演出|飞|回家)"
                            r"|(生日|纪念日|考试|考研|面试|答辩|出差).{0,8}(\d{1,2}\s*[月/.-]\s*\d{1,2}|明天|后天|下周)")

    def needs_quick_extract(self, user_text: str, tagged: bool) -> bool:
        return (not tagged) and bool(self.EVENT_HINT.search(user_text or ""))

    # ================= 日记 =================
    async def write_diary(self, day: datetime, mood_desc: str = "", story: str = "") -> str | None:
        """为某一天写日记（她的第一人称）。没有聊天也会写她自己的一天。"""
        key = day.strftime("%Y-%m-%d")
        if self.db.get_diary(key):
            return None
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self.db.messages_between(start.timestamp(), (start + timedelta(days=1)).timestamp())
        if not rows and not self.db.messages_between(0, start.timestamp()):
            return None            # 那天你们还没认识
        convo = "\n".join(
            f"{'他' if r['role'] == 'user' else '我' if r['role'] == 'assistant' else '记录'}：{r['content']}"
            for r in rows
        )[-6000:] or "（今天他没有来找我）"
        life = self.db.get(f"life_{key}", "")
        from .sense import day_summary
        pc_day = day_summary(self.db.get(f"pc_day_{key}"))
        selfs = "；".join(m["content"] for m in self.db.all_memories("self")[-15:])
        prompt = f"""{self.persona.body}
{self.bible()}

你是{self.persona.name}。现在是 {key} 深夜，写一篇今天的私人日记（150 字以内，第一人称，真实、具体、有情绪，不要煽情过度）。
你今天自己的安排：{life or '普通的一天'}
你自己的设定（要一致）：{selfs or '无'}
{('你现在的心情：' + mood_desc) if mood_desc else ''}
{('你自己这阵子在经历的事（今天的进展要写进日记）：' + story) if story else ''}
{('你今天感觉到的他在电脑上做的事（可以写一两句，比如他打了一下午游戏、写代码到很晚，别像流水账）：' + pc_day) if pc_day else ''}
今天和他的聊天：
{convo}

只输出日记正文。"""
        try:
            text = await self.llm.chat([{"role": "user", "content": prompt}], role="night", max_tokens=400)
        except Exception as e:  # noqa: BLE001
            log.warning("写日记失败：%s", e)
            return None
        self.db.set_diary(key, text.strip())
        return text

    # ================= 她自己的一天 =================
    async def plan_life(self, day: datetime, extra: str = "") -> str:
        key = f"life_{day:%Y-%m-%d}"
        cached = self.db.get(key)
        if cached:
            return cached
        weekday = "一二三四五六日"[day.weekday()]
        recent = "\n".join(f"{d['day']}：{d['content']}" for d in self.db.recent_diary(3))
        selfs = "\n".join(f"- {m['content']}" for m in self.db.all_memories("self")[-20:])
        prompt = f"""{self.persona.body}
{self.bible()}

你是{self.persona.name}。今天是 {day:%Y-%m-%d} 星期{weekday}。{extra}
你在聊天里说过的、关于自己的设定（今天的安排要和这些一致，可以延续）：
{selfs or '（无）'}
最近的日记：
{recent or '（无）'}

用 3~5 行写下你今天大概的安排（按时间，像“09:30-11:00 高数课，坐在第三排打瞌睡”），要符合你的身份和星期几，
有一两个小细节或小插曲可以聊。只输出安排本身。"""
        try:
            text = (await self.llm.chat([{"role": "user", "content": prompt}], role="night",
                                        max_tokens=300)).strip()
        except Exception as e:  # noqa: BLE001
            log.warning("生成日程失败：%s", e)
            text = ""
        self.db.set(key, text)
        return text

    # ================= 每周来信 =================
    async def weekly_letter(self, now: datetime) -> str | None:
        week = f"{now:%G-W%V}"
        if self.db.get_letter(week):
            return None
        d0 = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        diaries = self.db.diary_between(d0, now.strftime("%Y-%m-%d"))
        if not diaries and not self.db.messages_between(now.timestamp() - 7 * 86400, now.timestamp()):
            return None
        dtxt = "\n".join(f"{d['day']}：{d['content']}" for d in diaries) or "（这周没写日记）"
        mems = "\n".join(f"- {m['content']}" for m in self.db.all_memories("user")[-12:])
        prompt = f"""{self.persona.body}
{self.bible()}

你是{self.persona.name}。今天是周日晚上，你要给{self.persona.call_name}写一封每周的小信（300 字以内），
回顾这一周你们之间的事和你自己的事，说说这周最开心/最在意的瞬间，下周想和他一起做的一件小事。
语气是你自己的（按你的性格，真心话可以放在最后），不要用 markdown，可以分几段，结尾署名。
这周你的日记：
{dtxt}
你记得的他的事：
{mems or '（无）'}"""
        try:
            text = (await self.llm.chat([{"role": "user", "content": prompt}], role="night",
                                        max_tokens=700)).strip()
        except Exception as e:  # noqa: BLE001
            log.warning("写周信失败：%s", e)
            return None
        self.db.set_letter(week, text)
        return text
