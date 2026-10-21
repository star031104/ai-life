"""19.0 有希的提醒：发出去了才算完成（以前先标完成再发，她那次选了“跳过”提醒就丢了）；没发出去过 5 分钟再试，
第三次直接发固定的话，四次都不行记成“没发出去”；小屋能看到待提醒和历史、能取消。"""
import asyncio
import time

from aiohttp.test_utils import TestClient, TestServer


def test_reminder_kept_until_sent(env):
    cfg, brain, bot, fake = env
    from ailife.mind.output import Parsed
    from ailife.proactive.scheduler import Scheduler
    sched = Scheduler(cfg, brain, bot)
    rid = brain.db.add_reminder(time.time() - 10, "交实验报告")
    calls = []

    async def skip(reason, must=False):
        calls.append((reason, must))
        return Parsed(skip=True)
    brain.proactive = skip
    asyncio.run(sched._reminders(brain.now()))
    r = brain.db.conn.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
    assert r["done"] == 0 and r["tries"] == 1 and r["next_try"] > time.time() + 200   # 还在，过 5 分钟再试
    assert calls and calls[0][1] is True and "不能跳过" in calls[0][0]
    asyncio.run(sched._reminders(brain.now()))
    assert len(calls) == 1                                                             # 没到重试时间：不再问
    brain.db.conn.execute("UPDATE reminders SET next_try=0, tries=2 WHERE id=?", (rid,))
    n = len(fake.sent)
    asyncio.run(sched._reminders(brain.now()))                                         # 第三次：直接发固定的话
    assert len(fake.sent) > n and any("交实验报告" in str(s[1]) for s in fake.sent[n:])
    r = brain.db.conn.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
    assert r["done"] == 1 and r["sent_at"]
    assert not brain.db.pending_reminders()


def test_reminder_gives_up_after_four(env):
    cfg, brain, bot, fake = env
    from ailife.proactive.scheduler import Scheduler
    sched = Scheduler(cfg, brain, bot)
    rid = brain.db.add_reminder(time.time() - 10, "吃药")
    brain.db.conn.execute("UPDATE reminders SET tries=3 WHERE id=?", (rid,))

    async def boom(p, **kw):
        raise RuntimeError("网断了")
    bot.deliver = boom
    asyncio.run(sched._reminders(brain.now()))
    r = brain.db.conn.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
    assert r["done"] == 2
    ev = brain.db.conn.execute("SELECT content FROM messages WHERE role='event' ORDER BY id DESC LIMIT 1").fetchone()
    assert "一直没发出去" in ev["content"]


def test_missed_reminder_status(env):
    cfg, brain, bot, fake = env
    from ailife.proactive.scheduler import Scheduler
    rid = brain.db.add_reminder(time.time() - 8 * 3600, "交实验报告")
    asyncio.run(Scheduler(cfg, brain, bot)._reminders(brain.now()))
    assert brain.db.conn.execute("SELECT done FROM reminders WHERE id=?", (rid,)).fetchone()["done"] == 3


def test_reminders_api(env):
    cfg, brain, bot, _ = env
    from ailife.web.server import Dashboard
    a = brain.db.add_reminder(time.time() + 3600, "交报告")
    b = brain.db.add_reminder(time.time() - 60, "吃药")
    brain.db.finish_reminder(b, 1)

    async def go():
        async with TestClient(TestServer(Dashboard(cfg, brain, bot).app())) as c:
            d = await (await c.get("/api/reminders")).json()
            assert [x["content"] for x in d["open"]] == ["交报告"]
            assert d["history"][0]["content"] == "吃药" and d["history"][0]["status"] == "发了"
            r = await c.post("/api/reminders/cancel", json={"id": a})
            assert (await r.json())["ok"]
            d = await (await c.get("/api/reminders")).json()
            assert not d["open"] and any(x["status"] == "取消了" for x in d["history"])
    asyncio.run(go())


def _call(name, **args):
    import json
    return {"id": f"c{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def test_yuki_research_flow(env):
    """19.0 有希也会先想一想：判断层调了 research → 研究员搜 → 交卷 → 简报写进聊天记录（一条事件）；“等我看看”只在深查时说。"""
    import json
    cfg, brain, bot, fake = env
    cfg["net"] = {**dict(cfg.get("net") or {}), "agent": True}
    script = [{"role": "assistant", "content": "", "tool_calls": [_call("research", goal="aspas 最近的状态", depth="deep")]},
              {"role": "assistant", "content": "", "reasoning_content": "先搜", "tool_calls": [_call("web_search", query="aspas", days=30)]},
              {"role": "assistant", "content": json.dumps({"answer_points": ["还在 MIBR，合同到 2027"], "premise": "",
                                                          "as_of": "2026-10-21", "sources": [{"site": "vlr.gg"}],
                                                          "confidence": "high", "unknown": ""}, ensure_ascii=False)}]
    seen = []

    async def chat_tools(messages, tools, **kw):
        seen.append({"tools": [t["function"]["name"] for t in tools or []], "thinking": kw.get("thinking"),
                     "msgs": [dict(m) for m in messages]})
        return script.pop(0) if script else {"role": "assistant", "content": "无"}
    brain.llm.tool_profiles = lambda role="helper": ["ds"]
    brain.llm.chat_tools = chat_tools
    rs = bot._researcher()

    async def search(q, n=6, days=0, **kw):
        return [{"title": "aspas MIBR", "url": "https://www.vlr.gg/player/1", "snippet": "合同到 2027", "date": "2026-10-01"}], "Tavily"
    rs._web_().agent_search = search
    brain.db.add_message("user", "aspas 最近怎么样了")
    asyncio.run(bot._maybe_search())
    assert seen[0]["tools"] == ["research"] and seen[1]["thinking"] is True
    assert any(m.get("reasoning_content") == "先搜" for m in seen[2]["msgs"])         # 思考内容原样带回去
    assert any(m["role"] == "tool" and "<资料" in m["content"] for m in seen[2]["msgs"])
    ev = brain.db.conn.execute("SELECT content FROM messages WHERE role='event' ORDER BY id DESC LIMIT 1").fetchone()
    assert "还在 MIBR" in ev["content"] and "你刚在手机上查了一下" in ev["content"]
    from aigroup.agent.prompts import FILLERS
    assert any(s[0] == "text" and s[1][1] in FILLERS for s in fake.sent)                # 深查先说一句“等我看看”
    assert (brain.db.get("agent_log") or [])[0]["rounds"] == 2
    # 闲聊：判断层不调工具 → 什么都不加
    n = brain.db.conn.execute("SELECT COUNT(*) FROM messages WHERE role='event'").fetchone()[0]
    brain.db.add_message("user", "今天好累哦")
    asyncio.run(bot._maybe_search())
    assert brain.db.conn.execute("SELECT COUNT(*) FROM messages WHERE role='event'").fetchone()[0] == n


def test_yuki_research_falls_back(env):
    cfg, brain, bot, fake = env
    cfg["net"] = {**dict(cfg.get("net") or {}), "agent": True}
    brain.llm.tool_profiles = lambda role="helper": ["ds"]

    async def boom(*a, **k):
        raise RuntimeError("400")
    brain.llm.chat_tools = boom
    assert asyncio.run(bot._researcher().think()) is None                          # 调用方退回老的“搜一次”
    cfg["net"]["agent"] = False
    assert not bot._researcher().on()
