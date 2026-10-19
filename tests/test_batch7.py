"""第七批：动图/视频/文件/位置、引用、表情反馈、大模型兜底与优先级、校历与作息、周报、自检、手机电量。"""
import asyncio
import json
import subprocess
import time
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from test_flow import env  # noqa: F401


def drain(bot, coro):
    """跑完处理函数，再等它在后台开的任务（截帧、识别）都做完。"""
    async def go():
        await coro
        while bot._bg:
            await asyncio.gather(*list(bot._bg), return_exceptions=True)
    asyncio.run(go())


def make_mp4(path, seconds=2, audio=True):
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=10:d={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest"]
    args += ["-pix_fmt", "yuv420p", "-y", str(path)]
    subprocess.run(args, check=True)
    return path.read_bytes()


def test_video_frames(tmp_path):
    from ailife.bot_media import video_frames
    data = make_mp4(tmp_path / "v.mp4")
    frames = asyncio.run(video_frames(data, 2))
    assert len(frames) == 2 and all(f[:2] == b"\xff\xd8" for f in frames)


class FakeFile:
    def __init__(self, data):
        self.data = data

    async def download_as_bytearray(self):
        return bytearray(self.data)


def fake_update(owner=42, **msg):
    m = SimpleNamespace(message_id=500, caption=None, reply_to_message=None, quote=None, text=None,
                        animation=None, video=None, video_note=None, document=None, location=None, venue=None,
                        dice=None, contact=None, poll=None, game=None, story=None)
    for k, v in msg.items():
        setattr(m, k, v)
    return SimpleNamespace(message=m, effective_user=SimpleNamespace(id=owner), effective_chat=SimpleNamespace(id=owner))


def test_media_handlers(env, tmp_path, monkeypatch):
    cfg, brain, bot, fake = env
    data = make_mp4(tmp_path / "v.mp4")
    looked = []

    async def describe(b64, hint=""):
        looked.append(hint)
        return "一只猫在跳舞"
    monkeypatch.setattr(type(brain.llm), "vision", True, raising=False)
    brain.llm.describe_image = describe

    async def get_file(fid):
        return FakeFile(data if fid != "txt" else "第一行\n第二行".encode())
    ctx = SimpleNamespace(bot=SimpleNamespace(get_file=get_file))
    replies = []

    async def sched(delay=None):
        replies.append(delay)
    bot.schedule_reply = sched
    drain(bot, bot.on_animation(fake_update(animation=SimpleNamespace(file_id="a", file_size=1000)), ctx))
    drain(bot, bot.on_video(fake_update(video_note=SimpleNamespace(file_id="v", file_size=5000, duration=2)), ctx))
    drain(bot, bot.on_document(fake_update(document=SimpleNamespace(file_id="txt", file_size=20, file_name="笔记.txt",
                                                                     mime_type="text/plain")), ctx))
    drain(bot, bot.on_document(fake_update(document=SimpleNamespace(file_id="z", file_size=10 ** 9, file_name="大.zip",
                                                                     mime_type="application/zip")), ctx))
    drain(bot, bot.on_dice(fake_update(dice=SimpleNamespace(emoji="🎲", value=6)), ctx))
    drain(bot, bot.on_other(fake_update(poll=object()), ctx))
    msgs = [r["content"] for r in brain.db.recent_messages(20) if r["role"] == "user"]
    assert any("动图：一只猫在跳舞" in m for m in msgs)
    assert any("圆形视频" in m and "一只猫在跳舞" in m and "声音你听不了" in m for m in msgs)
    assert any("笔记.txt" in m and "第一行" in m for m in msgs)
    assert any("大.zip" in m and "打不开" in m for m in msgs)
    assert any("骰子" in m and "满分" in m for m in msgs)
    assert any("投票" in m for m in msgs)
    assert len(replies) == 6


def test_location_sets_weather(env, monkeypatch):
    cfg, brain, bot, fake = env

    async def fake_get(url, params):
        return {"address": {"city": "广州市", "suburb": "天河区"}}
    brain.weather._get = fake_get
    called = []

    async def sched(delay=None):
        called.append(1)
    bot.schedule_reply = sched
    upd = fake_update(location=SimpleNamespace(latitude=23.1, longitude=113.3, live_period=None))
    drain(bot, bot.on_location(upd, SimpleNamespace()))
    assert brain.db.get("city") == "广州市" and brain.db.get("geo")["lat"] == 23.1
    assert "广州市天河区" in brain.db.last_message("user")["content"]


def test_quote_context(env):
    cfg, brain, bot, fake = env
    her = SimpleNamespace(text="今天钢琴课好难", caption=None, sticker=None, voice=None, photo=None, message_id=9,
                          from_user=SimpleNamespace(id=999))
    msg = SimpleNamespace(reply_to_message=her, quote=None)
    assert "引用了你说的「今天钢琴课好难」" in bot._quote_ctx(msg)
    mine = SimpleNamespace(text="我明天考试", caption=None, sticker=None, voice=None, photo=None, message_id=10,
                           from_user=SimpleNamespace(id=42))
    assert "他自己之前说的" in bot._quote_ctx(SimpleNamespace(reply_to_message=mine, quote=SimpleNamespace(text="考试")))
    assert bot._quote_ctx(SimpleNamespace(reply_to_message=None, quote=None)) == ""


def test_reaction_feedback(env):
    cfg, brain, bot, fake = env
    from ailife.feedback import classify_reaction
    assert classify_reaction(["👍"]) == "pos" and classify_reaction(["👎"]) == "neg" and classify_reaction(["😢"]) == "sad"
    mid = brain.db.add_message("assistant", "哥哥大人是不是又在打游戏？", "text", 77)
    joy0 = brain.emotion.state()["joy"]
    upd = SimpleNamespace(message_reaction=SimpleNamespace(user=SimpleNamespace(id=42), message_id=77,
                                                           new_reaction=[SimpleNamespace(emoji="👎")]))
    asyncio.run(bot.on_reaction(upd, None))
    assert brain.emotion.state()["joy"] <= joy0
    assert brain.quality.disliked()[0]["text"].startswith("哥哥大人是不是又在打游戏")
    assert "不喜欢" in brain.db.last_message("event")["content"]
    assert "他不喜欢你这样说" in brain.quality.block()
    assert any("不喜欢" in p for p in brain.quality.problems(["哥哥大人是不是又在打游戏呀？"]))
    assert mid


def test_llm_priority_and_stats(env):
    from ailife.llm import LLM
    cfg = env[0]
    llm = LLM(cfg)
    llm.yield_seconds = 0.3
    order = []

    async def fake_chat(messages, role, temperature, max_tokens):
        order.append((role, "start"))
        await asyncio.sleep(0.2)
        order.append((role, "end"))
        llm._record(role, "p", True, ms=200)
        return "ok"
    llm._chat = fake_chat

    async def run():
        fg = asyncio.create_task(llm.chat([{"role": "user", "content": "x"}]))
        await asyncio.sleep(0.05)
        bg = asyncio.create_task(llm.chat([{"role": "user", "content": "y"}], role="memory"))
        await asyncio.gather(fg, bg)
    asyncio.run(run())
    assert order[0] == ("chat", "start") and order.index(("memory", "start")) > order.index(("chat", "end"))
    st = llm.stats()
    assert st["calls"] == 2 and st["fg"] == 1 and st["bg"] == 1
    from ailife.health import usage_line
    assert "今天调用大模型 2 次" in usage_line(llm)


def test_reply_failure_holds_and_retries(env, monkeypatch):
    cfg, brain, bot, fake = env

    async def boom():
        raise RuntimeError("所有 chat 接口都失败了")
    brain.reply = boom
    brain.db.add_message("user", "在吗", "text", 5)
    spawned = []
    done = SimpleNamespace(done=lambda: True, cancel=lambda: None)
    monkeypatch.setattr(bot, "spawn", lambda c: (spawned.append(c), c.close(), done)[-1])
    asyncio.run(bot._reply_after(0))
    texts = [a[1] for k, a, kw in fake.sent if k == "text"]
    assert texts and texts[0] in bot.HOLD_LINES
    assert bot._retry_n == 1 and spawned
    asyncio.run(bot._reply_after(0))                     # 第二次失败：不再重复发“等我一下”
    assert len([1 for k, a, kw in fake.sent if k == "text"]) == 1 and bot._retry_n == 2


def test_school_calendar_and_age(env):
    cfg, brain, bot, fake = env
    rh = brain.rhythm
    brain.db.set("her_anchor", {"enroll_ay": 2026, "age": 19, "since": "2026-10-13"})
    brain.db.set("her_birthday", "03-15")
    assert rh.grade(date(2026, 10, 13)) == "大一" and rh.grade(date(2027, 9, 10)) == "大二"
    assert rh.age(date(2027, 3, 14)) == 19 and rh.age(date(2027, 3, 15)) == 20
    ph = rh.school.phase(date(2027, 1, 25))
    assert ph["vacation"] and ph["term"] == "寒假"
    blk = rh.her_block()
    assert "你的生活节奏" in blk and "岁" in blk
    from ailife.rhythm import routine_active
    assert routine_active("周三 19:00-21:00", datetime(2026, 10, 14, 19, 30))
    assert not routine_active("周三 19:00-21:00", datetime(2026, 10, 15, 19, 30))
    assert routine_active("每天 23:00-01:00", datetime(2026, 10, 15, 0, 30))
    assert rh.add_routine("工作日 09:00-18:00", "上班") and not rh.add_routine("随便", "x")
    assert rh.busy_now(datetime(2026, 10, 14, 10, 0, tzinfo=cfg.tz)) == "上班"
    real_now = brain.now
    brain.now = lambda: datetime(2026, 10, 14, 9, 5, tzinfo=cfg.tz)       # 周三（学的是工作日的时间）
    for i in range(4):
        rh.note_phone_event("到公司/学校了")
    brain.now = real_now
    assert "到公司/学校了" in rh.learned_times()
    assert "他的日常节奏" in brain._world_block()


def test_weekly_report(env):
    cfg, brain, bot, fake = env
    for i in range(5):
        brain.db.add_message("user", f"在干嘛{i}")
        brain.db.add_message("assistant", "刚下课，好累", "text")
        brain.db.add_message("assistant", "你呢", "text")
    from ailife.report import weekly_report
    text, cur, prev = weekly_report(brain, save=True)
    assert "这一周她的表现" in text and cur["你发的消息"] == 5 and cur["重复率%"] > 0
    from ailife.report import report_for_web
    assert report_for_web(brain)["metrics"]


def test_selftest_passes(env, tmp_path):
    cfg, brain, bot, fake = env
    import shutil
    from test_flow import cfg_root
    (tmp_path / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    cfg["has_user_config"] = True
    shutil.copy(cfg_root() / "config.example.yaml", tmp_path / "config.example.yaml")
    from ailife.selftest import _run
    out = []
    ok = asyncio.run(_run(cfg, out.append))
    text = "\n".join(out)
    assert "大脑：回复 + 标签" in text and "✅ 大脑" in text, text
    assert "✅ 记忆整理 + 作息学习" in text, text
    assert ok or "❌ 依赖" in text or "❌ 配置文件" in text, text
