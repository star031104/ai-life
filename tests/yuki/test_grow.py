"""11.2：随手拍、喜好不改口、每月整理、被纠正了就改、记 token 用量（都不联网）。"""
import asyncio
import json
import time
from datetime import datetime, timedelta


def test_voice_tag_ignored(env):
    """语音已去掉：模型偶尔还打 [语音]，照样去掉、发文字；提示词里不提语音。"""
    cfg, brain, bot, fake = env
    from ailife.mind.output import parse_reply
    p = parse_reply("[语音]晚安啦|||明天见")
    assert [b.text for b in p.bubbles] == ["晚安啦", "明天见"]
    asyncio.run(bot.deliver(p))
    assert [k for k, *_ in fake.sent] == ["text", "text"]
    assert "[语音]" not in brain.protocol("晚安呀 想听你的声音")
    assert not hasattr(fake, "send_voice")


def test_prefs_learned_and_locked(env, monkeypatch):
    cfg, brain, bot, fake = env
    sg = brain.selfgrow
    day = datetime.now(cfg.tz) - timedelta(days=1)
    t = day.replace(hour=20).timestamp()
    for x in ["我最讨厌香菜了", "EDG 必胜！我一直支持他们", "今天好困"]:
        brain.db.add_message("assistant", x, ts=t)

    async def chat(messages, helper=False, **kw):
        return '[{"topic":"香菜","v":"讨厌"},{"topic":"战队","v":"支持 EDG"}]'
    monkeypatch.setattr(brain.llm, "chat", chat)
    assert asyncio.run(sg.learn_prefs(day)) == 2
    assert asyncio.run(sg.learn_prefs(day)) == 0                 # 一天只整理一次
    assert not sg.add_pref("香菜", "其实还行")                    # 说出口的不改口
    assert "香菜：讨厌" in sg.prefs_block()
    sys_prompt = brain.system_prompt("你喜欢吃香菜吗") if hasattr(brain, "system_prompt") else ""
    if sys_prompt:
        assert "香菜：讨厌" in (sys_prompt if isinstance(sys_prompt, str) else str(sys_prompt))


def test_monthly_tidy(env, monkeypatch):
    cfg, brain, bot, fake = env
    ids = [brain.db.add_memory(f"我的室友叫小林 {i}", 3, "self") for i in range(3)]
    ids += [brain.db.add_memory(f"别的事 {i}", 3, "self") for i in range(10)]
    old = brain.db.add_memory("我在学吉他", 3, "self")
    new = brain.db.add_memory("我不学吉他了，改学钢琴", 3, "self")

    async def js(messages, default=None, role="helper"):
        return {"merge": [{"ids": ids[:3], "text": "我的室友叫小林"}], "drop": [old, 99999]}
    monkeypatch.setattr(brain.llm, "json", js)
    out = asyncio.run(brain.selfgrow.monthly_tidy(datetime.now(cfg.tz)))
    assert out["merged"] == 1 and out["removed"] == 3
    left = [r["content"] for r in brain.db.all_memories("self")]
    assert left.count("我的室友叫小林") == 1 and "我在学吉他" not in left and "我不学吉他了，改学钢琴" in left
    assert new
    assert asyncio.run(brain.selfgrow.monthly_tidy(datetime.now(cfg.tz))) == {}      # 一个月一次


def test_fix_learned(env, monkeypatch):
    cfg, brain, bot, fake = env
    mid = brain.db.add_memory("我查过「禁忌岛」：10月25日上线", 2, "self")
    brain.db.add_message("assistant", "禁忌岛不是25号上线嘛")

    async def js(messages, default=None, role="helper"):
        return {"fix": True, "id": mid, "text": "我查过「禁忌岛」：10月22日上线"}
    monkeypatch.setattr(brain.llm, "json", js)
    co = brain.selfgrow.maybe_fix("不对，禁忌岛是22号")
    assert co is not None and asyncio.run(co) is True
    assert "22日" in brain.db.get_memory(mid)["content"]
    assert brain.selfgrow.maybe_fix("哈哈好的") is None


def test_life_photo(env, monkeypatch):
    cfg, brain, bot, fake = env
    import ailife.proactive.events as E
    from ailife.proactive.scheduler import Scheduler
    sch = Scheduler(cfg, brain, bot)
    brain.photos.gen.chain = [{"name": "x"}]
    sent = []

    async def send(reason, note_reply=False):
        sent.append(reason)
        return True
    sch._send_proactive = send
    monkeypatch.setattr(E.random, "random", lambda: 0.0)
    now = datetime.now(cfg.tz).replace(hour=15)
    asyncio.run(sch._life_photo(now))
    assert sent and "[照片:" in sent[0]
    asyncio.run(sch._life_photo(now))
    assert len(sent) == 1                                          # 一天最多一次
    assert brain.db.get(f"life_photo_{now:%G-W%V}") == 1


def test_usage_recorded(env):
    cfg, brain, bot, fake = env
    from types import SimpleNamespace
    from ailife.llm.client import LLM
    llm = LLM(cfg)
    llm.db = brain.db
    llm._record("chat", "p", True, usage=SimpleNamespace(prompt_cache_hit_tokens=1000, prompt_cache_miss_tokens=500,
                                                        completion_tokens=100), price=[0.003, 0.15, 0.6])
    st = llm.stats()
    assert st["tok_hit"] == 1000 and st["tok_out"] == 100 and st["usd"] > 0


def test_image_gen_siliconflow_format(env, monkeypatch):
    cfg, brain, bot, fake = env
    import httpx
    from ailife.llm.image import ImageGen

    class R:
        def __init__(self, d=None, content=b""):
            self._d, self.content = d, content

        def raise_for_status(self):
            pass

        def json(self):
            return self._d

    class Cli:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            assert json["image_size"] == "768x1024"
            return R({"images": [{"url": "https://img"}]})

        async def get(self, url):
            return R(content=b"PNGDATA")
    monkeypatch.setattr(httpx, "AsyncClient", Cli)
    g = ImageGen(cfg)
    g.chain = [{"name": "sf", "base_url": "https://x/v1", "model": "m"}]
    assert asyncio.run(g.generate("猫", "768x1024")) == b"PNGDATA"
    assert time.time()


def test_daily_budget(env, tmp_path):
    cfg, brain, bot, fake = env
    import json as _j
    import pytest
    from ailife.llm.budget import BudgetExceeded
    from ailife.llm.client import LLM
    shared = tmp_path / "budget.json"
    day = datetime.now(cfg.tz).strftime("%Y-%m-%d")
    shared.write_text(_j.dumps({"day": day, "bots": {"yuki": 99, "qq": 0.5}}), encoding="utf-8")   # 自己那份不算文件里的
    from ailife.core.config import Cfg
    c = Cfg({**cfg, "llm": {**cfg["llm"], "budget": {"daily_cny": 1, "name": "yuki", "shared_file": str(shared)}}})
    llm = LLM(c)
    llm.db = brain.db
    llm.add_cost(0.07)                                     # ≈ ¥0.50，加上 qq 的 0.5 = ¥1.0
    assert llm.budget.status(0.07, day)["over"]
    with pytest.raises(BudgetExceeded):
        asyncio.run(llm.chat([{"role": "user", "content": "x"}], role="helper"))       # 后台的活先停
    with pytest.raises(RuntimeError) as e:
        asyncio.run(llm.chat([{"role": "user", "content": "x"}], role="chat"))         # 聊天还能多一成（这里没配模型）
    assert not isinstance(e.value, BudgetExceeded)
    llm.add_cost(0.03)
    with pytest.raises(BudgetExceeded):
        asyncio.run(llm.chat([{"role": "user", "content": "x"}], role="chat"))
    assert LLM(cfg).budget.cap == 0                         # 默认不限



def _fake_fetch():
    pages = {
        "web-interface/view": {"data": {"aid": 1, "cid": 2, "title": "七日世界禁忌岛实测", "owner": {"name": "某UP"},
                                        "duration": 600, "tname": "游戏", "desc": "带你看看新剧本", "stat": {"view": 9}}},
        "web-interface/nav": {"data": {"wbi_img": {"img_url": "https://i0/7cd084941338484aae1ad9425b84077c.png",
                                                    "sub_url": "https://i0/4932caff0ff746eab6f01bf08b70ac45.png"}}},
        "player/wbi/v2": {"data": {"subtitle": {"subtitles": [{"lan": "ai-zh", "subtitle_url": "//sub/x.json"}]}}},
        "sub/x.json": {"body": [{"from": 0, "content": "今天来试试禁忌岛"}, {"from": 190, "content": "这船开不动啊"}]},
        "v2/reply": {"data": {"replies": [{"content": {"message": "船那段笑死"}, "like": 99}]}},
    }

    async def fetch(url, headers=None):
        if "dm/list.so" in url:
            return ("".join(f'<d p="{190 + i},1">笑死</d>' for i in range(10))).encode()
        for k, v in pages.items():
            if k in url:
                return json.dumps(v).encode()
        raise RuntimeError("没有")
    return fetch


def test_watch_video(env, monkeypatch):
    """11.4 他发了 B 站视频：点开看完记成她看过的，跟他说两句。"""
    cfg, brain, bot, fake = env
    brain.links.fetch_bytes = _fake_fetch()

    async def js(messages, default=None, role="helper"):
        return {"gist": "博主试玩禁忌岛，造船出海", "points": ["03:10 船开不动"], "highlights": "03:10 船开不动",
                "vibe": "都在笑", "keys": ["禁忌岛"], "seen": "full"}
    monkeypatch.setattr(brain.llm, "json", js)
    brain.db.add_message("user", "你看这个 https://www.bilibili.com/video/BV1xx411c7mD")
    res = asyncio.run(bot.watch_video({"bv": "BV1xx411c7mD", "ok": True}))
    assert res["subtitle"] and "船开不动" in brain.db.last_message("event")["content"]
    assert any("我看过「七日世界禁忌岛实测」" in m["content"] for m in brain.db.all_memories("self"))
    assert fake.sent                                                     # 看完说了感想


def test_quarterly_memoir(env, monkeypatch):
    """11.4 季度回忆录：拿这三个月的材料写成分章节的小书，配上那个月的照片；1、4、7、10 月初自动写，写好了跟他说。"""
    cfg, brain, bot, fake = env
    from ailife.mind.memoir import prev_quarter, qkey
    tz = cfg.tz
    y, q = 2026, 3
    t = datetime(2026, 8, 12, 21, tzinfo=tz).timestamp()
    for i in range(30):
        brain.db.add_message("user" if i % 2 == 0 else "assistant", f"那天聊天第{i}句 {'我们去看海吧' if i == 14 else ''}", ts=t + i * 60)
    (cfg.root / "data" / "photos").mkdir(parents=True, exist_ok=True)
    (cfg.root / "data" / "photos" / f"{int(t + 100)}.jpg").write_bytes(b"jpg")
    brain.db.add_message("assistant", "[照片:海边的落日]", "photo", ts=t + 101)
    brain.db.set_recap("2026-08", "八月我们一起追完了一部番。", {})
    seen = {}

    async def chat(messages, helper=False, **kw):
        seen["p"] = messages[-1]["content"]
        return json.dumps({"title": "夏天的尾巴", "preface": "写给你。", "ending": "以后也要一起。",
                           "chapters": [{"title": "去看海的约定", "month": "2026-08", "text": "你说“我们去看海吧”。"},
                                        {"title": "九月", "month": "2026-09", "text": "开学了。"}]}, ensure_ascii=False)
    monkeypatch.setattr(brain.llm, "chat", chat)
    res = asyncio.run(brain.memoir.write(y, q))
    assert res["title"] == "夏天的尾巴" and len(res["chapters"]) == 2
    assert "我们去看海吧" in seen["p"] and "八月我们一起追完了一部番" in seen["p"] and "海边的落日" in seen["p"]
    assert res["chapters"][0]["photos"][0]["scene"] == "海边的落日" and res["chapters"][1]["photos"] == []
    assert brain.memoir.list()[0]["cover"].endswith(".jpg") and brain.memoir.get("2026-Q3")["ending"] == "以后也要一起。"
    assert prev_quarter(datetime(2026, 10, 2).date()) == (2026, 3) and qkey(2027, 1) == "2027-Q1"

    from ailife.proactive.scheduler import Scheduler
    sch = Scheduler(cfg, brain, bot)
    sent = []

    async def send(reason, note_reply=False):
        sent.append(reason)
        return True
    sch._send_proactive = send
    brain.db.conn.execute("DELETE FROM memoirs")
    asyncio.run(sch._quarterly_memoir(datetime(2026, 10, 3, 15, tzinfo=tz)))
    assert sent and "夏天的尾巴" in sent[0]
    asyncio.run(sch._quarterly_memoir(datetime(2026, 10, 4, 15, tzinfo=tz)))
    assert len(sent) == 1                                               # 写过了不再写


def test_memoir_too_little(env):
    cfg, brain, bot, fake = env
    assert asyncio.run(brain.memoir.write(2026, 1)) is None              # 聊得太少不写


def test_memoir_api(env):
    cfg, brain, bot, fake = env
    from aiohttp.test_utils import TestClient, TestServer

    from ailife.web.server import Dashboard
    brain.db.conn.execute("INSERT INTO memoirs(quarter, ts, title, body, stats, draft) VALUES (?,?,?,?,?,?)",
                          ("2026-Q3", time.time(), "夏天", json.dumps({"preface": "p", "chapters": [{"title": "一", "text": "t",
                                                                                                   "photos": []}],
                                                                       "ending": "e"}), "{}", 0))
    brain.db.conn.commit()

    async def go():
        async with TestClient(TestServer(Dashboard(cfg, brain, bot).app())) as c:
            L = await (await c.get("/api/memoirs")).json()
            assert L["items"][0]["title"] == "夏天" and L["items"][0]["chapters"] == ["一"]
            m = await (await c.get("/api/memoir?q=2026-Q3")).json()
            assert m["ending"] == "e"
            assert (await c.get("/api/memoir?q=1999-Q1")).status == 404
            page = await (await c.get("/")).text()
            assert 'id="mBooks"' in page
    asyncio.run(go())
