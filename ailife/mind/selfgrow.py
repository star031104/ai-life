"""11.2 她自己慢慢长：说出口的喜好不改口、每月把“她自己说过的事”整理一遍（合并重复、前后矛盾的以新的为准）、
被他纠正了就改掉她查过的那条。

- 喜好：每天整理一次昨天她说过的话，挑出“喜欢 / 讨厌 / 支持 / 觉得……”这种亲口说的态度，第一次说的就定下来
  （存 kv self_prefs），每次聊天都带上，被问到就照这个说；小屋里能删、能改
- 每月整理：她自己的记忆（owner='self'）攒多了会重复、会前后矛盾，每月初让后台模型合并一遍（最多删掉三成，删不掉的留着）
- 纠错：他说“不对 / 其实是……”，而且她刚说的话里用到了她查过的东西（“我查过「…」”那几条记忆），就让后台模型对一下，对的就改
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

log = logging.getLogger("ailife.self")

PREF_PROMPT = """下面是%s昨天和他聊天时自己说的话。挑出她亲口说的喜好和看法（喜欢 / 讨厌什么、最爱哪个、支持谁、觉得某样东西怎么样），
一个话题一条；态度写她原话的意思（12 字以内）。玩笑话、反话、为了哄他随口说的不算。没有就输出 []。
只输出 JSON 数组：[{"topic": "话题（2~8 个字）", "v": "她的态度"}]

她说的话：
%s"""

TIDY_PROMPT = """下面是%s记得的“自己的事”（编号|内容），时间从早到晚。帮她整理一下：
- 说的是同一件事的几条合并成一条（写清楚，40 字以内）；
- 前后矛盾的，以后面（更新）的为准，前面那条删掉；
- 不重复、不矛盾的不动（不要写出来）。
只输出 JSON：{"merge": [{"ids": [要合并的编号], "text": "合并后的一条"}], "drop": [要删掉的编号]}

%s"""

FIX_PROMPT = """他好像在纠正她刚才说的话。下面是聊天和她查过、记住的几条。判断：他说的对不对、要不要改她记的那条。
只有他给出了具体、可信的更正（日期、名字、数字、结论）才改；开玩笑、抬杠、或者和那几条无关就不改。
只输出 JSON：{"fix": true 或 false, "id": 要改的那条的编号, "text": "改好的这条（以“我查过「话题」：”开头，不超过 100 字）"}

# 她记住的（编号|内容）
%s

# 最近的聊天
%s"""

CORRECT_RE = re.compile(r"(不对|错了|不是这样|不是的|搞错|记错|说错|其实是|应该是|明明是|早就|瞎说|乱说|胡说)")


class SelfGrow:
    def __init__(self, brain):
        self.b = brain
        self.db = brain.db

    # ---------------- 喜好 ----------------
    def prefs(self) -> dict:
        d = self.db.get("self_prefs") or {}
        return d if isinstance(d, dict) else {}

    def add_pref(self, topic: str, v: str, force: bool = False) -> bool:
        topic, v = (topic or "").strip()[:12], (v or "").strip()[:24]
        if not topic or not v:
            return False
        d = self.prefs()
        if topic in d and not force:
            return False
        d[topic] = {"v": v, "ts": time.time()}
        if len(d) > 60:
            for k in sorted(d, key=lambda k: d[k].get("ts", 0))[: len(d) - 60]:
                d.pop(k, None)
        self.db.set("self_prefs", d)
        return True

    def delete_pref(self, topic: str):
        d = self.prefs()
        d.pop(topic, None)
        self.db.set("self_prefs", d or None)

    def prefs_block(self) -> str:
        d = self.prefs()
        if not d:
            return ""
        items = sorted(d.items(), key=lambda kv: kv[1].get("ts", 0))[-30:]
        return ("# 你的喜好和看法（你亲口说过的，一直是这样，别改口；被问到就照这个说）\n"
                + "\n".join(f"- {k}：{v.get('v')}" for k, v in items))

    async def learn_prefs(self, day: datetime) -> int:
        key = f"self_prefs_day_{day:%Y-%m-%d}"
        if self.db.get(key):
            return 0
        self.db.set(key, True)
        t0 = day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        mine = [r["content"] for r in self.db.messages_between(t0, t0 + 86400)
                if r["role"] == "assistant" and r["kind"] in ("text", "proactive", "voice")]
        if len(mine) < 3:
            return 0
        try:
            raw = await self.b.llm.chat([{"role": "user", "content": PREF_PROMPT % (
                self.b.persona.name, "\n".join(mine[-80:])[-3000:])}], role="helper", max_tokens=300, background=True)
        except Exception as e:  # noqa: BLE001
            log.info("整理喜好失败：%s", e)
            return 0
        from ailife.llm.client import parse_json
        items = parse_json(raw or "", [])
        n = 0
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict) and self.add_pref(str(it.get("topic") or ""), str(it.get("v") or "")):
                n += 1
        if n:
            log.info("记下了她说过的 %d 条喜好", n)
        return n

    # ---------------- 每月整理 ----------------
    async def monthly_tidy(self, now: datetime, force: bool = False) -> dict:
        key = "self_tidy_month"
        month = f"{now:%Y-%m}"
        if self.db.get(key) == month and not force:
            return {}
        rows = [r for r in self.db.all_memories("self")]
        if len(rows) < 12 and not force:
            self.db.set(key, month)
            return {}
        listing = "\n".join(f"{r['id']}|{r['content']}" for r in rows[-150:])
        try:
            d = await self.b.llm.json([{"role": "user", "content": TIDY_PROMPT % (self.b.persona.name, listing)}],
                                      default={}, role="helper")
        except Exception as e:  # noqa: BLE001
            log.info("每月整理失败：%s", e)
            return {}
        valid = {int(r["id"]) for r in rows[-150:]}
        cap = max(1, len(valid) * 3 // 10)                     # 一次最多删三成：模型抽风也不会把记忆清空
        removed, merged = 0, 0
        for m in (d or {}).get("merge") or []:
            ids = [int(x) for x in (m.get("ids") or []) if str(x).isdigit() and int(x) in valid]
            text = str(m.get("text") or "").strip()[:80]
            if len(ids) < 2 or not text or removed + len(ids) - 1 > cap:
                continue
            keep = max(ids)
            self.db.update_memory(keep, content=text)
            for x in ids:
                if x != keep:
                    self.db.delete_memory(x)
                    valid.discard(x)
                    removed += 1
            merged += 1
        for x in (d or {}).get("drop") or []:
            if str(x).isdigit() and int(x) in valid and removed < cap:
                self.db.delete_memory(int(x))
                valid.discard(int(x))
                removed += 1
        self.db.set(key, month)
        out = {"merged": merged, "removed": removed, "month": month, "ts": time.time()}
        self.db.set("self_tidy_last", out)
        log.info("每月整理她自己的事：合并 %d 组，删掉 %d 条", merged, removed)
        return out

    # ---------------- 被纠正了就改 ----------------
    def maybe_fix(self, text: str):
        if not CORRECT_RE.search(text or ""):
            return None
        last = self.db.last_message("assistant")
        if not last or time.time() - float(last["ts"]) > 20 * 60:
            return None
        return self.fix(last["content"] + " " + text)

    async def fix(self, text: str) -> bool:
        rows = [r for r in self.db.all_memories("self") if str(r["content"]).startswith("我查过「")]
        hits = []
        for r in rows:
            m = re.match(r"我查过「([^」]{1,30})」", r["content"])
            if m and any(w in text for w in re.split(r"[\s·,，]+", m.group(1)) if len(w) >= 2):
                hits.append(r)
        if not hits:
            return False
        known = "\n".join(f"{r['id']}|{r['content']}" for r in hits[-4:])
        recent = "\n".join(f"{'她' if r['role'] == 'assistant' else '他'}：{r['content']}"
                           for r in self.db.recent_messages(10) if r["role"] in ("user", "assistant"))
        try:
            d = await self.b.llm.json([{"role": "user", "content": FIX_PROMPT % (known, recent)}], default={}, role="helper")
        except Exception as e:  # noqa: BLE001
            log.info("对她查过的东西出错：%s", e)
            return False
        if not isinstance(d, dict) or not d.get("fix"):
            return False
        try:
            mid = int(d.get("id"))
        except (TypeError, ValueError):
            return False
        new = str(d.get("text") or "").strip()[:120]
        if mid not in {int(r["id"]) for r in hits} or not new.startswith("我查过「"):
            return False
        self.db.update_memory(mid, content=new)
        log.info("被他纠正了，改了她记的一条：%s", new[:40])
        return True

    # ---------------- 小屋用 ----------------
    def snapshot(self) -> dict:
        learned = [dict(id=r["id"], content=r["content"], ts=r["ts"]) for r in self.db.all_memories("self")
                   if str(r["content"]).startswith("我查过「")][-20:]
        return {"prefs": [{"topic": k, "v": v.get("v"), "ts": v.get("ts")} for k, v in
                          sorted(self.prefs().items(), key=lambda kv: -kv[1].get("ts", 0))],
                "learned": list(reversed(learned)),
                "tidy": self.db.get("self_tidy_last") or {},
                "voice_today": int(self.db.get(f"voice_{datetime.now(self.b.cfg.tz):%Y-%m-%d}", 0) or 0),
                "photos_week": int(self.db.get(f"life_photo_{datetime.now(self.b.cfg.tz):%G-W%V}", 0) or 0)}

