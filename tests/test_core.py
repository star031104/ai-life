"""不连网的核心逻辑测试：python -m pytest tests -q"""
import asyncio
import math
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from ailife.config import load_config
from ailife.output import emotion_to_emojis, parse_reply, parse_when
from ailife.proactive import fire_probability, in_quiet, parse_range
from ailife.llm import parse_json

TZ = ZoneInfo("Asia/Shanghai")


def test_parse_reply_bubbles_and_tags():
    raw = ("[引用]有点。不是你的错——这三天你没动静，我心里一直悬着。[心情:松了口气]|||"
           "刚才你回我那几句，我一下子松了口气。[表情:委屈]|||[语音]你人好好的就够。[好感:+1]"
           "[提醒:2026-10-13 08:00|叫他起床]")
    p = parse_reply(raw, "林汐")
    assert p.quote and p.mood == "松了口气" and p.affection_delta == 1
    assert [b.sticker for b in p.bubbles] == [None, None, "委屈", None]
    assert p.bubbles[0].text.startswith("有点")
    assert p.bubbles[3].text == "你人好好的就够。" and "语音" not in p.plain_text      # 语音去掉了：标签照样删掉
    assert p.reminders == [("2026-10-13 08:00", "叫他起床")]


def test_parse_reply_skip_and_name_prefix():
    assert parse_reply("[跳过]").skip
    p = parse_reply("林汐：在吗", "林汐")
    assert p.bubbles[0].text == "在吗"
    assert parse_reply("哼（脸红）才没有|||(把手机架在键盘上)").bubbles[0].text == "哼才没有"
    p = parse_reply("【表情：开心】")
    assert p.bubbles[0].sticker == "开心"


def test_emotions():
    assert "😢" in emotion_to_emojis("难过")
    assert emotion_to_emojis("有点难过")
    assert emotion_to_emojis("🥺") == ["🥺"]


def test_parse_when():
    now = datetime(2026, 10, 12, 23, 0, tzinfo=TZ)
    assert parse_when("08:00", now) == datetime(2026, 10, 13, 8, 0, tzinfo=TZ)
    assert parse_when("2026-10-14 09:30", now).day == 14
    assert parse_when("胡说", now) is None


def test_quiet_and_ranges():
    d = datetime(2026, 10, 12, 2, 0, tzinfo=TZ)
    assert in_quiet(d, "00:30-08:00")
    assert not in_quiet(d.replace(hour=9), "00:30-08:00")
    assert in_quiet(d.replace(hour=23, minute=30), "23:00-07:00")
    assert in_quiet(d.replace(hour=3), "23:00-07:00")
    s, e = parse_range("23:00-01:00", d)
    assert e - s == timedelta(hours=2)


def test_fire_probability_grows_with_absence():
    cfg = load_config()
    p1 = fire_probability(cfg.proactive, 1, 10)
    p2 = fire_probability(cfg.proactive, 72, 10)
    assert 0 < p1 < p2 < 1
    # 每天期望次数大致符合 base_per_day
    per_day = -math.log(1 - p1) * 6 * 16
    assert abs(per_day - cfg.proactive.base_per_day) < 0.2


def test_parse_json():
    assert parse_json('好的```json\n{"add":[{"content":"a"}]}\n```') == {"add": [{"content": "a"}]}
    assert parse_json('[1,2]') == [1, 2]
    assert parse_json("nothing", 5) == 5


def test_llm_fallback_and_cooldown():
    from types import SimpleNamespace
    from ailife.llm import LLM
    cfg = load_config('/nonexistent.yaml')   # 只用示例配置
    llm = LLM(cfg)

    def client(fn):
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fn)))

    async def boom(**kw):
        raise RuntimeError("Error code: 429 rate limit")

    async def ok(**kw):
        assert kw["extra_body"] == {"reasoning_effort": "none"}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="<think>x</think>好呀"))])

    llm.profiles["glm_flash"].client = client(boom)
    llm.profiles["groq_qwen"].client = client(ok)
    assert asyncio.run(llm.chat([{"role": "user", "content": "hi"}])) == "好呀"
    assert not llm.profiles["glm_flash"].available()
    assert [p.name for p in llm._chain("chat")][0] == "groq_qwen"


def test_persona_files_load():
    from ailife.persona import load_persona
    from ailife.config import ROOT
    y = load_persona(ROOT / "tests" / "fixtures" / "student.md")
    assert y.name == "小葵" and y.call_name == "哥哥" and "傲娇" in y.body


def test_parser_cleanup():
    p = parse_reply("今天晴哦[天气:上海 18°C]\n||\n哼，才不是特意的\n||\n[语音]记得带伞|||一|||二|||三|||四")
    texts = [b.text for b in p.bubbles]
    assert "||" not in texts and all("天气" not in t for t in texts)
    assert len([t for t in texts if t]) == 4


def test_date_dedupe(tmp_path):
    from ailife.db import DB
    db = DB(tmp_path / "x.db")
    db.add_date("2026-12-20", "哥哥考研")
    db.add_date("2026-12-20", "考研")
    assert len(db.all_dates()) == 1
