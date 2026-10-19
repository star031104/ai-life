"""媒体和运维：动图 / 视频 / 文件 / 位置、引用、表情反馈、大模型兜底与优先级、校历与作息、周报、自检。"""
import asyncio
import subprocess
from datetime import date, datetime
from types import SimpleNamespace




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
    from ailife.media.ffmpeg import video_frames
    data = make_mp4(tmp_path / "v.mp4")
    frames = asyncio.run(video_frames(data, 2))
    assert len(frames) == 2 and all(f[:2] == b"\xff\xd8" for f in frames)


def test_reaction_feedback(env):
    cfg, brain, bot, fake = env
    from ailife.mind.feedback import classify_reaction
    assert classify_reaction(["👍"]) == "pos" and classify_reaction(["👎"]) == "neg" and classify_reaction(["😢"]) == "sad"


def test_llm_priority_and_stats(env):
    from ailife.llm.client import LLM
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
    from ailife.core.health import usage_line
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
    from ailife.mind.rhythm import routine_active
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
    from ailife.mind.report import weekly_report
    text, cur, prev = weekly_report(brain, save=True)
    assert "这一周她的表现" in text and cur["你发的消息"] == 5 and cur["重复率%"] > 0
    from ailife.mind.report import report_for_web
    assert report_for_web(brain)["metrics"]


def test_selftest_passes(env, tmp_path):
    cfg, brain, bot, fake = env
    import shutil
    from fakes import cfg_root
    (tmp_path / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    cfg["has_user_config"] = True
    shutil.copy(cfg_root() / "config.example.yaml", tmp_path / "config.example.yaml")
    from ailife.core.selftest import _run
    out = []
    ok = asyncio.run(_run(cfg, out.append))
    text = "\n".join(out)
    assert "大脑：回复 + 标签" in text and "✅ 大脑" in text, text
    assert "✅ 记忆整理 + 作息学习" in text, text
    assert ok or "❌ 依赖" in text or "❌ 配置文件" in text, text
