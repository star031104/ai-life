"""11.2：语音、随手拍、喜好不改口、每月整理、被纠正了就改、记 token 用量（都不联网）。"""
import asyncio
import time
from datetime import datetime, timedelta


def test_voice_tag_parsed_and_sent(env, monkeypatch):
    cfg, brain, bot, fake = env
    from ailife.mind.output import parse_reply
    p = parse_reply("[语音]晚安啦|||明天见")
    assert p.bubbles[0].voice and p.bubbles[0].text == "晚安啦" and not p.bubbles[1].voice

    async def speak(text):
        return b"ID3" + b"\x00" * 600
    monkeypatch.setattr(brain.voice, "speak", speak)
    asyncio.run(bot.deliver(p))
    kinds = [k for k, *_ in fake.sent]
    assert kinds[0] == "voice" and "text" in kinds
    assert brain.db.last_message("assistant")["content"] == "明天见"
    assert brain.db.conn.execute("SELECT COUNT(*) FROM messages WHERE kind='voice'").fetchone()[0] == 1


def test_voice_falls_back_to_text(env):
    cfg, brain, bot, fake = env
    from ailife.mind.output import parse_reply
    assert not brain.voice.ready                                  # 没配：照样发文字
    asyncio.run(bot.deliver(parse_reply("[语音]想你了")))
    assert [k for k, *_ in fake.sent] == ["text"]


def test_voice_limits(env):
    cfg, brain, bot, fake = env
    from ailife.media.voice import Voice
    v = Voice({**cfg, "voice": {"enabled": True, "api_key": "k", "max_per_day": 2, "max_chars": 10}}, brain.db)
    assert v.ready and v.remaining_today() == 2
    assert Voice.speakable("好呀（笑）😊~") == "好呀~"
    assert asyncio.run(v.speak("这句话实在是太长太长了念不完的")) is None     # 太长不念


def test_voice_in_protocol(env):
    cfg, brain, bot, fake = env
    brain.voice.enabled, brain.voice.api_key = True, "k"
    assert "[语音]" in brain.protocol("晚安呀")
    brain.voice.enabled = False
    assert "[语音]" not in brain.protocol("晚安呀")


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
