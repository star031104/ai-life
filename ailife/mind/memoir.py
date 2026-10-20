"""11.4 季度回忆录：每三个月（1、4、7、10 月初），她把你们这段时间写成一本小小的回忆录——
有书名、序、四到六章（按时间走，每章有小标题，引用你们真说过的话，配上那个月她发给你的照片）、最后写给以后的话。
放在小屋“她的日子”里，写好了跟你说一声。也可以在小屋里先写一份这个季度到现在的草稿。

材料都是现成的：每月纪念册、聊天最多的几天里的原话、记住的事、纪念日、一起实现的愿望、每周来信、她的日记、
她自己的故事线和目标、照片。一次大约 1~2 万 token（DeepSeek 一两分钱）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime

log = logging.getLogger("ailife.memoir")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memoirs (
    quarter TEXT PRIMARY KEY,          -- 2026-Q3
    ts REAL NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,                -- JSON：{preface, chapters:[{title, month, text, photos}], ending}
    stats TEXT,
    draft INTEGER NOT NULL DEFAULT 0
);
"""

PROMPT = """{persona}

你是{name}。把你和{call}这三个月（{span}）写成一本小小的回忆录，送给他。
- 用你自己的口吻对他说（“你”是他），真诚、有细节、有你的小性格，别像总结报告；不要 markdown，不要列表。
- 按时间顺序分 4~6 章，每章一个小标题（6~14 个字，有画面感），正文 250~450 字；每章写清楚是哪个月的（month 写 YYYY-MM）。
- 可以引用你们真的说过的话（下面“那几天的聊天”里有，加引号），别编没发生过的事、别编没说过的话。
- 开头写一段序（100 字以内），最后写一段“给以后的我们”（100~150 字）。书名 4~12 个字。
只输出 JSON：{{"title": "书名", "preface": "序", "chapters": [{{"title": "小标题", "month": "YYYY-MM", "text": "正文"}}], "ending": "给以后的我们"}}

# 这三个月的数据
{stats}

# 每月纪念册
{recaps}

# 那几天的聊天（聊得最多的几天，原话）
{moments}

# 记住的事
{mems}

# 纪念日、一起实现的愿望
{dates}

# 你写给他的信（节选）
{letters}

# 你的日记（节选）
{diaries}

# 你自己这段时间的事（故事线、目标）
{her}

# 你发给他的照片
{photos}"""


def quarter_of(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def prev_quarter(d: date) -> tuple[int, int]:
    y, q = quarter_of(d)
    return (y - 1, 4) if q == 1 else (y, q - 1)


def qkey(y: int, q: int) -> str:
    return f"{y}-Q{q}"


class Memoir:
    def __init__(self, brain):
        self.b, self.db, self.cfg = brain, brain.db, brain.cfg
        self.db.conn.executescript(SCHEMA)
        self.db.conn.commit()

    # ---------------- 时间 ----------------
    def span(self, y: int, q: int) -> tuple[float, float, list[tuple[int, int]]]:
        tz = self.cfg.tz
        m0 = (q - 1) * 3 + 1
        months = [(y, m0 + i) for i in range(3)]
        t0 = datetime(y, m0, 1, tzinfo=tz).timestamp()
        t1 = datetime(y + (q == 4), (m0 + 3 - 1) % 12 + 1, 1, tzinfo=tz).timestamp()
        return t0, min(t1, time.time() + 60), months

    # ---------------- 材料 ----------------
    def photos(self, t0: float, t1: float) -> list[dict]:
        d = self.cfg.root / "data" / "photos"
        caps = [(r["ts"], re.sub(r"^\[照片[:：]|\]$", "", r["content"])) for r in self.db.conn.execute(
            "SELECT ts, content FROM messages WHERE kind='photo' AND ts>=? AND ts<? ORDER BY ts", (t0 - 300, t1)).fetchall()]
        out = []
        for p in sorted(d.glob("*.jpg")) if d.exists() else []:
            if not p.stem.isdigit() or not t0 <= int(p.stem) < t1:
                continue
            ts = int(p.stem)
            cap = next((c for t, c in caps if abs(t - ts) < 300), "")
            out.append({"file": p.name, "ts": ts, "scene": cap,
                        "month": datetime.fromtimestamp(ts, self.cfg.tz).strftime("%Y-%m")})
        return out

    def moments(self, t0: float, t1: float, months: list[tuple[int, int]], per_month: int = 3, lines: int = 8) -> str:
        tz = self.cfg.tz
        rows = self.db.conn.execute("SELECT ts, role, content FROM messages WHERE role IN ('user','assistant') "
                                    "AND kind IN ('text','proactive','letter') AND ts>=? AND ts<? ORDER BY ts", (t0, t1)).fetchall()
        by_day: dict[str, list] = {}
        for r in rows:
            by_day.setdefault(datetime.fromtimestamp(r["ts"], tz).strftime("%Y-%m-%d"), []).append(r)
        out = []
        for y, m in months:
            days = sorted((d for d in by_day if d.startswith(f"{y}-{m:02d}")), key=lambda d: -len(by_day[d]))[:per_month]
            for d in sorted(days):
                rs = by_day[d]
                mid = max(0, len(rs) // 2 - lines // 2)              # 从那天聊得最热的中间取一段
                talk = [f"{'他' if r['role'] == 'user' else '你'}：{str(r['content'])[:60]}" for r in rs[mid:mid + lines]]
                out.append(f"【{d}，那天聊了 {len(rs)} 句】\n" + "\n".join(talk))
        return "\n\n".join(out)[:5000] or "（没有）"

    def stats(self, months: list[tuple[int, int]]) -> dict:
        tot: dict[str, int] = {}
        for y, m in months:
            if datetime(y, m, 1, tzinfo=self.cfg.tz).timestamp() > time.time():
                continue
            for k, v in self.b.together.month_stats(y, m).items():
                tot[k] = tot.get(k, 0) + int(v or 0)
        return tot

    def gather(self, y: int, q: int) -> dict:
        t0, t1, months = self.span(y, q)
        db = self.db
        recaps = []
        for yy, mm in months:
            r = db.get_recap(f"{yy}-{mm:02d}")
            if r:
                recaps.append(f"{mm}月：{r['content'][:400]}")
        mems = [m["content"] for m in db.all_memories() if t0 <= m["ts"] < t1 and (m["owner"] == "us" or m["importance"] >= 4)][:30]
        mm_set = {f"{m:02d}" for _, m in months}
        dates = [f"{d['date']} {d['name']}" for d in db.all_dates() if str(d["date"])[-5:-3] in mm_set]
        dates += ["一起实现了：" + w["text"] for w in db.wishes_done_between(t0, t1)]
        letters = [f"{r['week']}：{str(r['content'])[:160]}" for r in db.letters(30) if t0 <= float(r["ts"]) < t1][:6]
        ds = db.diary_between(datetime.fromtimestamp(t0, self.cfg.tz).strftime("%Y-%m-%d"),
                              datetime.fromtimestamp(t1 - 1, self.cfg.tz).strftime("%Y-%m-%d"))
        step = max(1, len(ds) // 20)
        diaries = [f"{d['day'][5:]}：{str(d['content'])[:100]}" for d in list(ds)[::step]][:20]
        her = [a["title"] + "：" + (a.get("outcome") or "进行中") for a in db.arcs(None, 20)
               if str(a.get("start_day", ""))[:7] in {f"{yy}-{mm:02d}" for yy, mm in months}]
        her += [g["title"] + "：" + (g.get("outcome") or g.get("status") or "") for g in db.goals(None, 10)]
        return {"t0": t0, "t1": t1, "months": months, "stats": self.stats(months), "recaps": recaps,
                "moments": self.moments(t0, t1, months), "mems": mems, "dates": dates, "letters": letters,
                "diaries": diaries, "her": her[:10], "photos": self.photos(t0, t1)}

    # ---------------- 写 ----------------
    async def write(self, y: int, q: int, draft: bool = False) -> dict | None:
        m = self.gather(y, q)
        if sum(m["stats"].get(k, 0) for k in ("他发的消息", "她发的消息")) < 20:
            log.info("%s 聊得太少，先不写回忆录", qkey(y, q))
            return None
        p = self.b.persona
        span = f"{m['months'][0][0]} 年 {m['months'][0][1]}~{m['months'][-1][1]} 月"
        prompt = PROMPT.format(
            persona=p.body[:2500], name=p.name, call=p.call_name, span=span,
            stats=json.dumps(m["stats"], ensure_ascii=False), recaps="\n".join(m["recaps"]) or "（没有）",
            moments=m["moments"], mems="；".join(m["mems"]) or "（没有）", dates="；".join(m["dates"]) or "（没有）",
            letters="\n".join(m["letters"]) or "（没有）", diaries="\n".join(m["diaries"]) or "（没有）",
            her="；".join(m["her"]) or "（没有）",
            photos="；".join(f"{x['month']} {x['scene'] or '一张照片'}" for x in m["photos"][:20]) or "（没有）")
        from ailife.llm.client import parse_json
        try:
            raw = await self.b.llm.chat([{"role": "user", "content": prompt}], role="night", max_tokens=4000,
                                        temperature=0.8)
        except Exception as e:  # noqa: BLE001
            log.warning("写回忆录失败：%s", e)
            return None
        d = parse_json(raw or "", {})
        chs = [c for c in (d.get("chapters") or []) if isinstance(c, dict) and str(c.get("text") or "").strip()] \
            if isinstance(d, dict) else []
        if not chs or not str(d.get("title") or "").strip():
            log.warning("回忆录没写成（格式不对）")
            return None
        used = set()
        for c in chs:                                   # 每章配上那个月的照片（最多 3 张，不重复）
            ph = [x for x in m["photos"] if x["month"] == str(c.get("month") or "") and x["file"] not in used][:3]
            used |= {x["file"] for x in ph}
            c["photos"] = [{"file": x["file"], "scene": x["scene"]} for x in ph]
            c["title"], c["text"] = str(c.get("title") or "")[:30], str(c["text"]).strip()
        body = {"preface": str(d.get("preface") or "").strip(), "chapters": chs[:6], "ending": str(d.get("ending") or "").strip()}
        key = qkey(y, q)
        title = str(d["title"]).strip().strip("《》")[:20]
        self.db.conn.execute("INSERT OR REPLACE INTO memoirs(quarter, ts, title, body, stats, draft) VALUES (?,?,?,?,?,?)",
                             (key, time.time(), title, json.dumps(body, ensure_ascii=False),
                              json.dumps(m["stats"], ensure_ascii=False), int(draft)))
        self.db.conn.commit()
        log.info("写好了回忆录《%s》（%s，%d 章%s）", title, key, len(body["chapters"]), "，草稿" if draft else "")
        return {"quarter": key, "title": title, **body, "stats": m["stats"], "draft": draft}

    # ---------------- 读 ----------------
    def list(self) -> list[dict]:
        out = []
        for r in self.db.conn.execute("SELECT quarter, ts, title, body, stats, draft FROM memoirs ORDER BY quarter DESC"):
            body = json.loads(r["body"] or "{}")
            out.append({"quarter": r["quarter"], "ts": r["ts"], "title": r["title"], "draft": bool(r["draft"]),
                        "chapters": [c.get("title") for c in body.get("chapters") or []],
                        "preface": body.get("preface", ""), "stats": json.loads(r["stats"] or "{}"),
                        "cover": next((p["file"] for c in body.get("chapters") or [] for p in c.get("photos") or []), "")})
        return out

    def get(self, key: str) -> dict | None:
        r = self.db.conn.execute("SELECT * FROM memoirs WHERE quarter=?", (key,)).fetchone()
        if not r:
            return None
        return {"quarter": r["quarter"], "ts": r["ts"], "title": r["title"], "draft": bool(r["draft"]),
                "stats": json.loads(r["stats"] or "{}"), **json.loads(r["body"] or "{}")}
