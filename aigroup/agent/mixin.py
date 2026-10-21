"""19.0 她回话之前先“想一想要不要办点事”：判断层（要不要查、要不要设提醒）→ 研究层（不带人设，用工具查、交一份简报）
→ 简报交给原来的说话层，用她的口吻说。

挂在 GroupChat 上（AgentMixin），用到：self.brain.llm（chat_tools）、self.web（搜索）、self.links（读网页）、
self.memory / self.knowledge（她的记忆）、grow.py 里的提醒（remind_set / remind_list / remind_cancel）。
开关：群设置 agent（默认关，微信群在 config 里打开）。失败时返回 None，调用方退回老的“搜一次”。
"""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime
from urllib.parse import urlparse

from aigroup.agent import prompts as P
from aigroup.agent.loop import tool_loop

log = logging.getLogger("aigroup.agent")
tlog = logging.getLogger("aigroup.trace")


def _date_key(r: dict) -> str:
    return str(r.get("date") or "")


def format_results(results: list[dict], engine: str) -> str:
    """搜索结果 → 给研究员看的一段：有日期的按日期从旧到新排（最新的放最后），每条包在 <资料> 里。"""
    if not results:
        return "（没搜到东西：换个说法、换中英文、或者放宽 days 再试）"
    from aigroup.net.links import site_name
    rows = sorted(results, key=lambda r: (bool(_date_key(r)), _date_key(r)))
    out = []
    for r in rows:
        host = (urlparse(r.get("url") or "").hostname or "").lower()
        site = site_name(host) if host else ""
        out.append(f'<资料 来源="{site or host or "未知"}" 日期="{_date_key(r) or "无"}">\n'
                   f'{r.get("title", "")}\n{r.get("snippet", "")}\n链接：{r.get("url", "")}\n</资料>')
    tail = "" if engine in ("Tavily", "缓存") else (
        f"\n（这次用的是{engine}，结果没有发布日期：别把它们当成“最近”的事，需要日期就 read_url 打开看）")
    return "\n".join(out) + tail


def format_page(info: dict, url: str, offset: int) -> str:
    if not info.get("ok"):
        return f"（{url} 没打开：{info.get('error') or '读不到'}。换一个来源，或者用搜索摘要）"
    more = (f"\n（没读完：全文 {info['total']} 字，下一页用 offset={offset + len(info['text'])}）"
            if info.get("more") else "")
    return (f'<资料 来源="{info.get("site") or ""}" 日期="{info.get("date") or "无"}">\n'
            f'{info.get("title", "")}\n{info.get("text", "")}\n</资料>{more}')


def parse_brief(text: str) -> dict:
    from aigroup.chat.plan import lenient_json
    d = lenient_json(text or "")
    if not isinstance(d, dict) or ("answer_points" not in d and not d.get("premise")):
        t = (text or "").strip()
        return {"answer_points": [t[:400]] if t else [], "premise": "", "as_of": "", "sources": [],
                "confidence": "low", "unknown": "" if t else "没查到"}
    pts = d.get("answer_points") or []
    d["answer_points"] = [str(x).strip() for x in (pts if isinstance(pts, list) else [pts]) if str(x).strip()][:6]
    return d


def brief_block(goal: str, d: dict, fresh: bool = True) -> str:
    """简报 → 放进她提示词里的一段。"""
    head = (f"# 你刚在手机上查了一下：{goal}" if fresh else f"# 你刚才查过的（还记得）：{goal}")
    if d.get("as_of"):
        head += f"（信息截至 {d['as_of']}）"
    lines = [head]
    if d.get("premise"):
        lines.append(f"前提：{d['premise']}")
    lines += [f"- {p}" for p in d.get("answer_points") or []]
    if not d.get("answer_points") and not d.get("premise"):
        lines.append("- （什么都没查到）")
    if d.get("unknown"):
        lines.append(f"没查到 / 不确定：{d['unknown']}")
    conf = {"high": "比较有把握", "medium": "大概对", "low": "把握不大"}.get(str(d.get("confidence") or ""), "")
    if conf:
        lines.append(f"把握：{conf}")
    srcs = [s for s in (d.get("sources") or []) if isinstance(s, dict)][:4]
    if srcs:
        lines.append("来源：" + "；".join(f"{s.get('site') or s.get('title', '')[:20]}"
                                         f"{'（' + str(s.get('date')) + '）' if s.get('date') else ''}" for s in srcs))
    lines.append(P.BRIEF_HOWTO)
    return "\n".join(lines)


class AgentMixin:
    # ---------------- 开关和限额 ----------------
    def agent_on(self, gid: int) -> bool:
        llm = self.brain.llm
        return bool(self.gk(gid).get("agent", False)) and hasattr(llm, "tool_profiles") and bool(llm.tool_profiles())

    def _agent_day_key(self, gid: int) -> str:
        return f"agent_n_{gid}_{self.brain.now():%Y-%m-%d}"

    def _agent_log(self, rec: dict):
        logs = self.db.get("agent_log") or []
        logs = ([rec] + [x for x in logs if isinstance(x, dict)])[:60]
        self.db.set("agent_log", logs)

    def _briefs(self, gid: int) -> list[dict]:
        bs = self.db.get(f"agent_briefs_{gid}") or []
        return [b for b in bs if isinstance(b, dict) and time.time() - float(b.get("ts", 0)) < 6 * 3600]

    def _save_brief(self, gid: int, goal: str, d: dict):
        bs = [{"goal": goal, "ts": time.time(), "brief": d}] + self._briefs(gid)
        self.db.set(f"agent_briefs_{gid}", bs[:5])

    def _now_text(self) -> str:
        now = self.brain.now()
        return f"{now:%Y-%m-%d %H:%M} 星期{P.WEEK[now.weekday()]}"

    # ---------------- 入口：说话前先想一想 ----------------
    async def agent_think(self, gid: int, mentioned: bool, who: str = "", trigger=None) -> str | None:
        """返回放进提示词的一段（可能是空字符串：什么都不用办）；出错返回 None（调用方退回老办法）。"""
        rows = list(self._rows(gid, 12))
        users = [r for r in rows if r["role"] == "user"]
        if not users:
            return ""
        asker = users[-1]
        if trigger:
            hit = next((r for r in reversed(users) if r["qq_msg_id"] == trigger), None)
            asker = hit or asker
        recent = self._briefs(gid)
        rec_txt = "\n".join(f"- {b['goal']}（{int((time.time() - b['ts']) / 60)} 分钟前）" for b in recent[:3]) or "（没有）"
        focus = (f"{'有人在叫她，' if mentioned else '她想接一句，'}最新一条是 {asker['name']} 说的。"
                 f"说这句话的人（“我”）是：{asker['name']}")
        try:
            gname = await self.group_name(gid)
        except Exception:  # noqa: BLE001
            gname = "群"
        msgs = [{"role": "system", "content": P.ROUTER_SYSTEM.format(now=self._now_text(), name=self.brain.persona.name,
                                                                    group=gname)},
                {"role": "user", "content": P.ROUTER_USER.format(transcript=self.transcript(gid, 12), recent=rec_txt,
                                                                 focus=focus)}]
        t0 = time.time()
        try:
            m = await self.brain.llm.chat_tools(msgs, P.ROUTER_TOOLS, role="helper", thinking=False, max_tokens=500)
        except Exception as e:  # noqa: BLE001
            log.info("群 %s：判断要不要办事失败：%s", gid, e)
            return None
        calls = m.get("tool_calls") or []
        if not calls:
            tlog.info("群 %s 想一想：不用办事（%.1fs）", gid, time.time() - t0)
            if recent and time.time() - recent[0]["ts"] < 30 * 60:
                return brief_block(recent[0]["goal"], recent[0]["brief"], fresh=False)
            return ""
        from aigroup.agent.loop import parse_args
        acts, research = [], None
        for tc in calls:
            name, args = tc["function"]["name"], parse_args(tc["function"].get("arguments"))
            tlog.info("群 %s 想一想：%s %s", gid, name, json.dumps(args, ensure_ascii=False)[:120])
            if name == "research" and research is None and str(args.get("goal") or "").strip():
                research = args
            elif name == "reminder_set":
                acts.append(self.remind_set(gid, args, int(asker["user_id"]), asker["name"]))
            elif name == "reminder_list":
                acts.append(self.remind_list(gid))
            elif name == "reminder_cancel":
                acts.append(self.remind_cancel(gid, args))
        parts = []
        if acts:
            parts.append("# 你刚办的事（回话时顺口确认一下，时间说清楚；没办成的就说没办成）\n" + "\n".join(f"- {a}" for a in acts))
        if research:
            block = await self._agent_research(gid, str(research["goal"]).strip()[:120],
                                               "deep" if research.get("depth") == "deep" else "quick", mentioned)
            if block is None:
                old = await self.web_block(gid, [r["content"] for r in users[-4:]], mentioned)
                if old:
                    parts.append(old)
            elif block:
                parts.append(block)
        return "\n\n".join(parts)

    # ---------------- 研究层 ----------------
    async def _agent_research(self, gid: int, goal: str, depth: str, mentioned: bool) -> str | None:
        g = self.gk(gid)
        day_key = self._agent_day_key(gid)
        if int(self.db.get(day_key, 0) or 0) >= int(g.get("agent_per_day", 60)):
            log.info("群 %s：今天查得够多了，退回只搜一次", gid)
            return None
        if not self._allow("research", int(g.get("search_per_hour", 12))):
            log.info("群 %s：这小时查得够多了，退回只搜一次", gid)
            return None
        self.db.set(day_key, int(self.db.get(day_key, 0) or 0) + 1)
        deep = depth == "deep"
        if deep and mentioned and g.get("agent_filler", True):
            await self._agent_filler(gid)
        thinking = deep and bool(g.get("agent_deep_thinking", True))
        rounds = int(g.get("agent_deep_rounds", 4) if deep else g.get("agent_quick_rounds", 2))
        deadline = float(g.get("agent_deep_seconds", 40) if deep else g.get("agent_quick_seconds", 15))
        msgs = [{"role": "system", "content": P.RESEARCH_SYSTEM.format(
                    now=self._now_text(), name=self.brain.persona.name,
                    rounds=f"这次最多查 {rounds} 轮，一般 {max(1, rounds - 1)} 轮就该交卷。")},
                {"role": "user", "content": P.RESEARCH_USER.format(goal=goal, context=self.transcript(gid, 6))}]
        try:
            tb = self.toolbox()
            res = await tool_loop(self.brain.llm, msgs, tb.schemas(recall=True),
                                  lambda n, a: tb.run(n, a, recall=lambda q: self._agent_recall(gid, q)),
                                  max_rounds=rounds, thinking=thinking, deadline_s=deadline, finish_prompt=P.FINISH)
        except Exception as e:  # noqa: BLE001
            log.info("群 %s：查资料失败：%s", gid, e)
            return None
        d = parse_brief(res.text)
        log.info("群 %s：查了「%s」（%s，%d 轮，%.0f 秒%s）", gid, goal, depth, res.rounds, res.seconds,
                 "，到点交卷" if res.forced else "")
        for s in res.steps:
            tlog.info("群 %s 查资料：%s", gid, s)
        self._agent_log({"ts": time.time(), "gid": gid, "goal": goal, "depth": depth, "thinking": thinking,
                         "rounds": res.rounds, "seconds": round(res.seconds, 1), "forced": res.forced,
                         "steps": res.steps[:20], "brief": d})
        if not d.get("answer_points") and not d.get("premise"):
            return brief_block(goal, {"answer_points": [], "unknown": d.get("unknown") or "没查到"})
        self._save_brief(gid, goal, d)
        if g.get("remember_web", True) and d.get("confidence") != "low" and d.get("answer_points"):
            self._spawn(self.learn(gid, goal, [{"title": goal, "snippet": "；".join(d["answer_points"][:3])}]))
        return brief_block(goal, d)

    async def _agent_filler(self, gid: int):
        """要查一会儿：先说一句“等我看看”（每个群 10 分钟最多一次）。"""
        k = f"agent_filler_{gid}"
        if time.time() - float(self.db.get(k, 0) or 0) < 600:
            return
        self.db.set(k, time.time())
        text = random.choice(P.FILLERS)
        try:
            from aigroup.qq.onebot import text_seg
            mid = await self.ob.send_group(gid, [text_seg(text)])
            self._record(gid, self.ob.self_id or 0, self.brain.persona.name, text, mid, role="assistant")
        except Exception as e:  # noqa: BLE001
            log.info("群 %s：“等我看看”没发出去：%s", gid, e)

    def toolbox(self):
        """19.3 研究员的工具箱（和有希共用 aigroup/agent/tools.py）：搜索、读任何网页、翻记忆、B 站、电竞、天气、算数。"""
        tb = getattr(self, "_toolbox", None)
        if tb is None:
            from aigroup.agent.tools import Toolbox
            tb = self._toolbox = Toolbox(self.cfg, self.links, self.web, where=getattr(self, "where", None),
                                         tz=self.cfg.tz, video_cache=lambda bv: self.db.get(f"qq_video_{bv}"))
        return tb

    async def _agent_exec(self, gid: int, name: str, args: dict) -> str:
        return await self.toolbox().run(name, args, recall=lambda q: self._agent_recall(gid, q))

    async def _agent_recall(self, gid: int, q: str) -> str:
        out = []
        try:
            found = self.memory.recall(gid, q, k_msgs=6, k_facts=6)
            blk = self.memory.prompt_block(found, self.names(gid))
            if blk:
                out.append(blk)
        except Exception as e:  # noqa: BLE001
            log.info("翻记忆出错：%s", e)
        kn = getattr(self, "knowledge", None)
        if kn is not None:
            es = kn.recall(gid, q, k=4)
            if es:
                out.append("以前查到过的：\n" + "\n".join(f"- {e['topic']}：{e['summary']}"
                                                     f"（{datetime.fromtimestamp(e['ts'], self.cfg.tz):%Y-%m-%d}）"
                                                     for e in es))
        return "\n".join(out) or "（没想起来相关的）"
