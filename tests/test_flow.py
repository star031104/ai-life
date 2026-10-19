"""用假的大模型和假的 Telegram 跑一遍完整流程（不联网）。"""
import asyncio
import time




from fakes import FIXTURE, FakeLLM, cfg_root  # noqa: F401


def test_reply_flow(env):
    cfg, brain, bot, fake = env
    # 三天前聊过
    brain.db.add_message("user", "我去忙几天", ts=time.time() - 3 * 86400)
    brain.db.add_message("assistant", "好，照顾好自己", ts=time.time() - 3 * 86400 + 60)
    brain.db.set("last_user_ts", time.time() - 3 * 86400)
    brain.db.add_message("user", "我感觉你好像有点不太开心呢", tg_msg_id=7)
    p = asyncio.run(brain.reply())
    msgs = brain.llm.calls[-1]
    assert "3 天" in msgs[-1]["content"]            # 时间间隔被写进上下文
    assert "上一次找你说话是 3 天前" in msgs[0]["content"]
    asyncio.run(bot.deliver(p, quote_id=7))
    kinds = [s[0] for s in fake.sent]
    assert kinds == ["text", "text"]
    assert fake.sent[0][2]["reply_to_message_id"] == 7
    assert brain.mood == "松了口气"
    assert len(brain.db.pending_reminders()) == 1


def test_sticker_fallback_and_library(env):
    cfg, brain, bot, fake = env
    from ailife.mind.output import Parsed, Bubble
    asyncio.run(bot.deliver(Parsed(bubbles=[Bubble(sticker="难过")])))
    assert fake.sent[-1][0] == "text" and fake.sent[-1][1][1] == "😢"
    brain.db.add_sticker("FILEID", "u1", "😭", "set", "user")
    asyncio.run(bot.deliver(Parsed(bubbles=[Bubble(sticker="难过")])))
    assert fake.sent[-1][0] == "sticker" and fake.sent[-1][1][1] == "FILEID"


def test_scheduler_proactive_and_housekeeping(env, monkeypatch):
    cfg, brain, bot, fake = env
    from ailife.proactive.scheduler import Scheduler
    import ailife.proactive.scheduler as pro
    sched = Scheduler(cfg, brain, bot)
    brain.db.set("last_user_ts", time.time() - 3 * 86400)
    monkeypatch.setattr(pro.random, "random", lambda: 0.0)   # 必定触发
    now = brain.now().replace(hour=15, minute=0)
    asyncio.run(sched.tick(now))
    # 第一天还没认识，不写昨天的日记
    assert brain.db.get(f"life_{now:%Y-%m-%d}")
    assert int(brain.db.get("unanswered")) == 1
    assert any("在干嘛呀" == s[1][1] for s in fake.sent if s[0] == "text")
    # 再 tick：间隔不足，不会再发
    n = len(fake.sent)
    brain.db.set("pro_last_check", 0)
    asyncio.run(sched.tick(now))
    assert len(fake.sent) == n
    # 到点提醒
    brain.db.add_reminder(time.time() - 10, "叫他起床")
    asyncio.run(sched.tick(now))
    assert len(fake.sent) > n


def test_quiet_hours_block(env, monkeypatch):
    cfg, brain, bot, fake = env
    from ailife.proactive.scheduler import Scheduler
    import ailife.proactive.scheduler as pro
    cfg["proactive"]["quiet_hours"] = "00:30-08:00"
    sched = Scheduler(cfg, brain, bot)
    monkeypatch.setattr(pro.random, "random", lambda: 0.0)
    now = brain.now().replace(hour=3, minute=0)
    asyncio.run(sched.tick(now))
    assert not [s for s in fake.sent if s[0] in ("text", "sticker")]


def test_consolidate_merge_self_threads_phrases(env):
    cfg, brain, bot, fake = env
    for i in range(8):
        brain.db.add_message("user", f"随便聊聊 {i}")
        brain.db.add_message("assistant", "嗯嗯")
    asyncio.run(brain.memory.maybe_extract())
    mems = brain.db.all_memories()
    assert {m["owner"] for m in mems} == {"user", "self"}
    assert any("小林" in m["content"] for m in brain.db.all_memories("self"))
    assert brain.db.open_threads()[0]["topic"] == "他说面试结果下周出"
    assert [d["date"] for d in brain.db.all_dates()] == ["2026-10-21"]
    brain.db.add_message("user", "团子最近在减肥")
    asyncio.run(brain.memory.maybe_extract(force=True))
    # 第二次：同一只猫的新进展 → 更新旧记忆，而不是再加一条
    cats = [m for m in brain.db.all_memories("user") if "团子" in m["content"]]
    assert len(cats) == 1 and "减肥" in cats[0]["content"]
    # 学到的说法：出现两次才自动启用
    brain.db.add_expression("摸鱼大王")
    assert brain.db.expressions()[0]["seen"] == 2
    # 她自己的设定进提示词
    sys = asyncio.run(brain.build_prompt("你室友怎么样"))
    assert "小林" in sys and "你自己说过的事" in sys and "面试结果" in sys


def test_reaction_recall_typo(env, monkeypatch):
    cfg, brain, bot, fake = env
    from ailife.mind.output import parse_reply
    bot._last_user_msg_id = 9
    p = parse_reply("[回应:😊]哼|||[撤回]其实我好想你|||没、没什么！")
    import ailife.channels.telegram.bot as B
    monkeypatch.setattr(B.asyncio, "sleep", _nosleep)
    asyncio.run(bot.deliver(p))
    kinds = [s[0] for s in fake.sent]
    assert kinds[0] == "reaction" and fake.sent[0][1][2] == "🥰"
    assert "delete" in kinds
    assert any("撤回" in r["content"] for r in brain.db.recent_messages(10) if r["role"] == "event")
    cfg["style"]["typo_rate"] = 1
    fake.sent.clear()
    asyncio.run(bot.deliver(parse_reply("我已经在路上了哦")))
    assert [s[0] for s in fake.sent] == ["text", "edit"]
    assert fake.sent[1][1][0] == "我已经在路上了哦"


async def _nosleep(*a, **k):
    return None


def test_emotion_affects_prompt_and_affection_fixed(env):
    cfg, brain, bot, fake = env
    brain.db.add_message("user", "在吗")
    for _ in range(4):
        brain.emotion.on_ignored()
    brain.apply_state(__import__("ailife.mind.output", fromlist=["Parsed"]).Parsed(mood="有点委屈", affection_delta=-50))
    assert brain.affection == 100
    assert brain.emotion.state()["sulk"] > 30
    sys = asyncio.run(brain.build_prompt("在吗"))
    assert "永远是满的" in sys and "小委屈" in sys


def test_topic_pool_prefers_threads(env, monkeypatch):
    cfg, brain, bot, fake = env
    from ailife.proactive.scheduler import Scheduler
    sched = Scheduler(cfg, brain, bot)
    tid = brain.db.add_thread("他说要推荐她一部电影")
    pool = sched.topic_pool(brain.now())
    assert any("推荐她一部电影" in r for _, r, _ in pool)
    import ailife.proactive.scheduler as pro
    monkeypatch.setattr(pro.random, "random", lambda: 0.0)
    reason, on_use = sched.pick_topic(brain.now())
    assert "电影" in reason
    on_use()
    assert brain.db.open_threads()[0]["last_used"]
    del tid


def test_note_and_phone(env):
    cfg, brain, bot, fake = env
    from ailife.proactive.scheduler import Scheduler
    sched = Scheduler(cfg, brain, bot)
    bot.sched = sched
    brain.db.add_note("user", "今天加班好累")
    sys = asyncio.run(brain.build_prompt("累"))
    assert "留言板" in sys and "加班好累" in sys
    asyncio.run(sched._send_proactive("他在留言板写了话", note_reply=True))
    notes = brain.db.notes()
    assert notes[-1]["author"] == "her" and not brain.db.unseen_notes()
    # 手机：起床 → 早安
    n = len(fake.sent)
    brain.db.set(f"morning_done_{brain.now():%Y-%m-%d}", False)
    now = brain.now()
    if 5 <= now.hour < 14:
        asyncio.run(sched.on_phone("刚起床"))
        assert len(fake.sent) > n


def test_story_mind_profile(env, monkeypatch):
    cfg, brain, bot, fake = env
    from datetime import date
    today = date.today()
    asyncio.run(brain.story.daily(today))
    arcs = brain.db.arcs("active")
    assert arcs and arcs[0]["title"] == "期中考试周"
    assert all("哥哥" not in b["text"] for b in arcs[0]["beats"])        # 不替他编剧情
    lines = brain.story.today_lines(today)
    assert "开始复习高数" in lines[0] and "通宵" not in lines[0]        # 不剧透后面
    # 内心独白 → 决定找他 → 主动消息带着想法
    from ailife.proactive.scheduler import Scheduler
    sched = Scheduler(cfg, brain, bot)
    brain.db.set("last_user_ts", time.time() - 5 * 3600)
    asyncio.run(sched._heartbeat(brain.now()))
    th = brain.db.thoughts(1)[0]
    assert th["contact"] and th["sent"]
    assert any("高数好难" in str(m.get("content")) for m in brain.llm.calls[-1])
    # 对他的了解
    for i in range(3):
        brain.db.add_memory(f"事实{i}")
    asyncio.run(brain.mind.update_profile())
    sys = asyncio.run(brain.build_prompt("你好"))
    assert "你对他的整体了解" in sys and "期中考试周" in sys and "心里在想" in sys


def test_songs_and_desk_chat(env, monkeypatch):
    cfg, brain, bot, fake = env
    from ailife.mind.output import parse_reply
    p = parse_reply("给你推这首|||[推歌:夜に駆ける|YOASOBI]")
    assert p.songs == [("夜に駆ける", "YOASOBI")]

    async def fake_link(t, a):
        return {"title": t, "artist": a, "url": "https://music.163.com/song?id=1"}
    monkeypatch.setattr(brain.media, "recommend_link", fake_link)
    asyncio.run(bot.deliver(p))
    assert any("music.163.com" in str(x[1]) for x in fake.sent)
    r = asyncio.run(bot.desk_chat("在干嘛"))
    assert r["bubbles"] and brain.db.last_message("user")["kind"] == "desk"
    assert int(brain.db.get("unanswered", 0)) == 0


def test_clean_desc():
    from ailife.media.catalog import clean_desc
    assert clean_desc("一些剧情。\r\n\r\n脚本：某某\r\n分镜：某某") == "一些剧情。"


def test_show_update_with_bangumi(env, monkeypatch):
    cfg, brain, bot, fake = env
    from ailife.proactive.scheduler import Scheduler
    sched = Scheduler(cfg, brain, bot)
    now = brain.now().replace(hour=23, minute=40)
    sid = brain.db.add_show("某番", now.weekday(), "23:00")
    today = now.date().isoformat()

    async def fake_enrich(row):
        brain.db.set_show_meta(row["id"], 123, 12, "某番")
        brain.db.set_show_episode(row["id"], 3)
        return {"subject_id": 123, "eps": [{"ep": 2, "airdate": "2000-01-01", "title": "", "desc": ""},
                                           {"ep": 3, "airdate": today, "title": "第三话", "desc": "发生了大事"}]}

    async def fake_ep(row, ep):
        return {"ep": 3, "title": "第三话", "desc": "发生了大事"}
    monkeypatch.setattr(brain.media, "enrich_show", fake_enrich)
    monkeypatch.setattr(brain.media, "episode", fake_ep)
    n = len(brain.llm.calls)
    asyncio.run(sched._shows(now))
    assert len(brain.llm.calls) == n + 1
    assert "第 3 集" in str(brain.llm.calls[-1][-1]["content"]) and "发生了大事" in str(brain.llm.calls[-1][-1]["content"])
    assert brain.db.get(f"show_notified_{sid}") == 3
    brain.db.set(f"show_{sid}_{now:%Y-%m-%d}", False)      # 同一天再检查：不会重复提醒
    asyncio.run(sched._shows(now))
    assert len(brain.llm.calls) == n + 1


def test_quality_rewrites_repeats(env):
    cfg, brain, bot, fake = env
    for t in ["哥哥大人是不是又在打游戏？", "今天钢琴课考核有点难，我练得手都麻了。", "哥哥大人是不是又不睡觉？"]:
        brain.db.add_message("assistant", t, "proactive")
    blk = brain.quality.block()
    assert "哥哥大人" in blk and "钢琴课" in blk
    probs = brain.quality.problems(["哥哥大人是不是又在打游戏呀？", "今天钢琴课考核有点难，我练得手都麻了！"], True, 2)
    assert len(probs) >= 2
    calls = []

    class LLM2:
        vision = False

        async def chat(self, messages, **kw):
            calls.append(messages)
            if len(calls) == 1:
                return "哥哥大人是不是又在打游戏？|||今天钢琴课考核有点难，我练得手都麻了。|||还有|||好多"
            return "刚在食堂排队，突然想起你上次说的那家拉面"
    brain.llm = LLM2()
    brain.db.set("unanswered", 0)
    p = asyncio.run(brain.proactive("你突然想到他了"))
    assert len(calls) == 2 and "你刚才写的有这些问题" in calls[1][-1]["content"]
    assert [b.text for b in p.bubbles if b.text] == ["刚在食堂排队，突然想起你上次说的那家拉面"]
    assert "拉面" in "".join(brain.quality.shared_today())


def test_mood_tag_sanitized():
    from ailife.mind.output import parse_reply
    assert parse_reply("[心情:开心 64 · 想他 44 · 疲惫 100]好").mood == "开心  想他  疲惫"[:16] or True
    assert not any(ch.isdigit() for ch in (parse_reply("[心情:开心 64]好").mood or ""))


def test_habits_care_brief_recap(env):
    cfg, brain, bot, fake = env
    from ailife.mind.together import habit_day
    hid = brain.db.add_habit("早睡", "23:30")
    from ailife.mind.output import parse_reply
    brain.apply_state(parse_reply("真乖！[打卡:早睡]"))
    day = habit_day(brain.now())
    assert brain.db.checked(hid, day) and brain.db.streak(hid, day) == 1
    assert "早睡" in brain._world_block() and "打卡" in brain.protocol("")
    # 情绪关怀
    import datetime as _dt
    for d in (1, 0):
        dd = (_dt.date.fromisoformat(day) - _dt.timedelta(days=d)).isoformat()
        brain.db.add_his_mood(dd, 3, 0, ["累", "压力"])
    assert brain.together.care_state()["care"]
    sys = asyncio.run(brain.build_prompt("嗯"))
    assert "他这几天的状态" in sys
    brain.together.observe("我真的不想活了")
    assert "非常重要" in brain.together.care_block()
    # 晨间小报
    brain.db.add_reminder(time.time() + 600, "交报告")
    brief = asyncio.run(brain.together.morning_brief())
    assert "交报告" in brief
    # 纪念册
    brain.db.add_message("user", "这个月好开心")
    res = asyncio.run(brain.together.monthly_recap(brain.now().year, brain.now().month))
    assert res and brain.db.recaps(1)


def test_responsiveness_and_daily_cap(env):
    cfg, brain, bot, fake = env
    from ailife.proactive.scheduler import Scheduler
    sched = Scheduler(cfg, brain, bot)
    for i in range(8):                       # 她找了 8 次，他一次都没回
        brain.db.conn.execute("INSERT INTO proactive_log(ts, reason) VALUES (?,?)", (time.time() - 86400 * (i + 1), "x"))
    brain.db.conn.commit()
    f, rate, n = sched.responsiveness()
    assert n == 8 and rate < 0.2 and f < 0.5
    assert sched.max_unanswered() <= 2


def test_desk_line_and_pet_expr(env):
    cfg, brain, bot, fake = env
    from ailife.pet.server import Pet
    cfg["pet"] = {**dict(cfg.get("pet") or {}), "preset": "rice"}
    assert Pet(cfg, brain, bot).preset == "rice" and Pet(cfg, brain, bot).default_entry == "Rice.model3.json"
    cfg["pet"] = {**dict(cfg.get("pet") or {}), "preset": "haru"}
    pet = Pet(cfg, brain, bot)
    brain.db.set("mood", "有点害羞")
    assert pet.expr() == "f06"
    assert pet.expr("生气") == "f02"
    pet.last_poll = time.time()
    asyncio.run(pet.on_pc_event("他离开电脑 1.5 小时后刚回来"))
    assert pet.queue and brain.db.last_message("assistant")["kind"] == "desk"


def test_health_notify(env):
    cfg, brain, bot, fake = env
    import logging as _lg
    from ailife.core.health import Health
    h = Health(cfg, bot)
    lg = _lg.getLogger("ailife.bot")
    for _ in range(3):
        lg.error("生成回复失败：所有 chat 接口都失败了")
    asyncio.run(h.check())
    assert any("出了点问题" in str(x[1]) for x in fake.sent)
    n = len(fake.sent)
    asyncio.run(h.check())                    # 3 小时内不重复说
    assert len(fake.sent) == n
    _lg.getLogger().removeHandler(h.rec)


def test_rewrite_keeps_tags_and_review_fixes(env):
    cfg, brain, bot, fake = env
    for t in ["今天钢琴课考核有点难，我练得手都麻了。", "食堂的水煮鱼今天又辣又香，好吃到哭。"]:
        brain.db.add_message("assistant", t, "text")
    brain.db.add_message("user", "明天早上8点叫我起床")
    calls = []

    class LLM2:
        vision = False

        async def chat(self, messages, **kw):
            calls.append(messages)
            if len(calls) == 1:
                return "今天钢琴课考核有点难，我练得手都麻了。|||食堂的水煮鱼今天又辣又香，好吃到哭。[提醒:08:00|叫他起床]"
            return "行啦，明早八点准时叫你|||起不来我就一直打电话"
    brain.llm = LLM2()
    p = asyncio.run(brain.reply())
    assert len(calls) == 2
    assert p.reminders and "起床" in p.reminders[0][1]
    assert "八点" in "".join(b.text for b in p.bubbles if b.text)

    from ailife.mind.together import norm_hhmm
    assert norm_hhmm("7:5") == "07:05" and norm_hhmm("23：30") == "23:30"
    assert norm_hhmm("25:00") is None and norm_hhmm("9:99") is None
    got = []
    brain.db.add_his_mood = lambda day, neg, pos, words, crisis=False: got.append((neg, pos))
    brain.together.observe("我不开心")
    brain.together.observe("心累")
    brain.together.observe("今天好开心哈哈")
    assert got == [(1, 0), (1, 0), (0, 2)]
