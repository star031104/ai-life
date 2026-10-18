"""第六批：示例挑选、密钥搬家、她身边的人、长期目标、愿望清单、时间胶囊、睡前。"""
import asyncio
import json
from datetime import date, timedelta

from test_flow import FakeLLM, env  # noqa: F401


def test_examples_parsed_and_picked():
    from ailife.persona import load_persona
    from ailife import examples as ex
    from test_flow import FIXTURE
    p = load_persona(FIXTURE)
    assert len(p.examples) >= 10 and "# 对话示例" not in p.body
    got = [e.scene for e in ex.pick(p, "今天被老板骂了", "今天被老板骂了")]
    assert got[0] == "他累了难过" and len(got) <= 3
    blk = ex.block(p, ex.pick(p, "你是不是AI", "你是不是AI"))
    assert "不要照抄" in blk and "问是不是AI" in blk


def test_examples_in_prompt_and_copy_flag(env):
    cfg, brain, bot, fake = env
    brain.db.add_message("user", "你是不是AI啊")
    sp = brain.system_prompt("你是不是AI啊")
    assert "你平时说话的样子" in sp and "【问是不是AI】" in sp
    probs = brain.quality.problems(["AI 会半夜吃辣条被辣哭吗？会被你气到想咬人吗？"])
    assert any("照抄" in x for x in probs)


def test_secret_migration(tmp_path):
    from ailife.secrets import migrate, read_env
    from ailife.config import load_config
    (tmp_path / "config.yaml").write_text(
        'keys:\n  zhipu: "abcdefgh12345678"  # 注释保留\n  groq: ""\ntelegram:\n  bot_token: 123:ABCDEFGHIJ\n',
        encoding="utf-8")
    moved = migrate(tmp_path)
    assert set(moved) == {"ZHIPU_KEY", "TELEGRAM_BOT_TOKEN"}
    txt = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "abcdefgh" not in txt and "# 注释保留" in txt and '"${ZHIPU_KEY}"' in txt
    assert read_env(tmp_path)["TELEGRAM_BOT_TOKEN"] == "123:ABCDEFGHIJ"
    c = load_config(tmp_path / "config.yaml")
    assert c["keys"]["zhipu"] == "abcdefgh12345678" and c.telegram.bot_token == "123:ABCDEFGHIJ"
    assert migrate(tmp_path) == []                 # 第二次什么都不做


def test_redaction():
    import logging
    from ailife.secrets import Redactor
    r = Redactor(["sk-secret-123456"])
    rec = logging.LogRecord("x", logging.WARNING, "", 0, "bad url %s", ("https://a/sk-secret-123456/x",), None)
    r.filter(rec)
    assert "sk-secret-123456" not in rec.getMessage()


class WorldLLM(FakeLLM):
    async def chat(self, messages, helper=False, **kw):
        last = messages[-1]["content"]
        if "固定会出现的 5~7 个人" in last:
            return json.dumps([{"name": "林晓", "role": "室友", "desc": "东北人，嗓门大"},
                               {"name": "哥哥", "role": "恋人", "desc": "不该出现"},
                               {"name": "周老师", "role": "钢琴老师", "desc": "很严格"}], ensure_ascii=False)
        if "现实生活里的具体的人" in last:
            return json.dumps([{"name": "阿杰", "role": "社团学长", "desc": "爱讲冷笑话"},
                               {"name": "没出现的人", "role": "x", "desc": "y"}], ensure_ascii=False)
        if "长期目标" in last and "plan" in last:
            d0 = date.today()
            plan = [{"day": (d0 + timedelta(days=i * 10)).isoformat(), "text": t, "progress": p}
                    for i, (t, p) in enumerate([("报名了钢琴八级", 5), ("练《月光》第三乐章", 30),
                                                ("模拟考没过，有点丧", 25), ("考完了", 100)])]
            return json.dumps({"title": "钢琴八级", "why": "想给妈妈一个惊喜", "plan": plan}, ensure_ascii=False)
        if "时间胶囊" in last:
            return "一年后的哥哥，你还会嫌我矮吗？"
        if "说一句晚安" in last:
            return "笨蛋，快睡吧，晚安。（小声）"
        if "睡前小故事" in last:
            return "好啦，就讲一个。从前有一只不会飞的萤火虫……"
        return await super().chat(messages, helper=helper, **kw)


def test_her_world_cast_and_goals(env):
    cfg, brain, bot, fake = env
    brain.llm = WorldLLM()
    hw = brain.herworld
    assert asyncio.run(hw.ensure_cast()) == 2                     # “哥哥”被过滤
    assert "林晓（室友）" in hw.bible() and "哥哥（" not in hw.bible()
    today = brain.now().date()
    brain.db.add_message("assistant", "今天阿杰学长又讲冷笑话了，林晓笑到拍桌子")
    added = asyncio.run(hw.learn(today))
    assert added == ["阿杰"]
    gid = asyncio.run(hw.new_goal(today))
    g = brain.db.goals("active")[0]
    assert g["id"] == gid and g["title"] == "钢琴八级"
    prog, past, now = hw.progress(g, today)
    assert prog == 5 and now["text"] == "报名了钢琴八级"
    assert "钢琴八级" in hw.goals_block(today) and "模拟考" not in hw.goals_block(today)   # 不剧透
    topics = hw.milestone_topics(today)
    assert topics and "钢琴八级" in topics[0][1]
    sp = brain.system_prompt("林晓最近怎么样")
    assert "你身边的人" in sp and "你自己的长期目标" in sp
    web = hw.for_web(today)
    assert web["goals"][0]["progress"] == 5 and any(p["name"] == "阿杰" for p in web["people"])
    # 到期结束 → 进她自己的记忆
    asyncio.run(hw.daily(today + timedelta(days=70)))          # 计划不到 40 天会被拉长到 50 天
    assert brain.db.goals("done") and any("钢琴八级" in m["content"] for m in brain.db.all_memories("self"))


def test_wishes_tags_and_capsule(env):
    cfg, brain, bot, fake = env
    brain.llm = WorldLLM()
    from ailife.output import parse_reply
    p = parse_reply("好呀，那就说定了[愿望:一起去看海]")
    assert p.wishes == ["一起去看海"] and p.bubbles[0].text == "好呀，那就说定了"
    brain.apply_state(p)
    assert brain.db.wishes()[0]["by"] == "her"
    assert "一起去看海" in brain.moments.wishes_block()
    brain.apply_state(parse_reply("终于！[完成愿望:看海]"))
    assert brain.db.wishes()[0]["done_ts"]
    assert "✓ 一起去看海" in brain.moments.wishes_block()
    # 时间胶囊
    from ailife.moments import parse_open_day
    t = date(2026, 10, 13)
    assert parse_open_day("一年", t) == date(2027, 10, 13)
    assert parse_open_day("3个月", t) == date(2027, 1, 13)
    assert parse_open_day("2027-01-01", t) == date(2027, 1, 1)
    assert parse_open_day("30天", t) == date(2026, 11, 12)
    assert parse_open_day("1月1日", t) == date(2027, 1, 1)
    today = brain.now().date()
    cid, mine = asyncio.run(brain.moments.create_capsule("给一年后的我们", today + timedelta(days=1)))
    assert mine and len(brain.db.capsules()) == 2
    web = brain.moments.capsules_for_web()
    assert [c["content"] for c in web if c["by"] == "her"] == [None]          # 她的没到日子看不到
    # 到日子打开
    from ailife.proactive import Scheduler
    s = Scheduler(cfg, brain, bot)
    from datetime import datetime
    when = datetime.combine(today + timedelta(days=1), datetime.min.time()).replace(hour=12, tzinfo=cfg.tz)
    asyncio.run(s._capsules(when))
    texts = [a[1] for k, a, kw in fake.sent if k == "text"]
    assert any("给一年后的我们" in x for x in texts) and any("一年后的哥哥" in x for x in texts)
    assert not brain.db.due_capsules((today + timedelta(days=1)).isoformat())


def test_bedtime_story_and_goodnight(env):
    cfg, brain, bot, fake = env
    brain.llm = WorldLLM()
    from ailife.output import parse_reply
    p = parse_reply("好啦好啦，就讲一个[睡前故事:萤火虫]")
    assert p.story == "萤火虫"
    bot.tts = type("T", (), {"synth": staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no tts")))})()
    asyncio.run(bot._bedtime(p, "讲个故事"))
    assert any("萤火虫" in a[1] for k, a, kw in fake.sent if k == "text")       # 语音失败时改发文字
    mo = brain.moments
    mo.window = "00:00-23:59"
    assert mo.wants_goodnight("那我睡了，晚安")
    assert not mo.wants_goodnight("今天吃了什么")
    line = asyncio.run(mo.goodnight_line())
    assert "（" not in line and "晚安" in line
    mo.mark_goodnight()
    assert not mo.wants_goodnight("晚安")
    assert "睡前故事" in brain.protocol("给我讲个睡前故事吧")


def test_secret_migration_with_existing_refs(tmp_path):
    from ailife.secrets import migrate, write_env
    write_env(tmp_path, {"ZHIPU_KEY": "abcdefgh12345678"})
    (tmp_path / "config.yaml").write_text(
        'keys:\n  zhipu: "${ZHIPU_KEY}"\n  siliconflow: sk-newnewnewnew\n', encoding="utf-8")
    assert migrate(tmp_path) == ["SILICONFLOW_KEY"]
    assert "sk-newnew" not in (tmp_path / "config.yaml").read_text(encoding="utf-8")
