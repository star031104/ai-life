"""电脑关机 / 睡眠前后：道别、回来后说说这段时间在干嘛、补上错过的事。"""
import asyncio
import time
from datetime import datetime, timedelta

from test_flow import env  # noqa: F401


def _back(brain, hours, plan=None):
    """假装程序 hours 小时前最后一次活着，现在刚开机。"""
    now = brain.now()
    if plan:
        brain.db.set(f"life_{now:%Y-%m-%d}", plan)
    brain.db.set("alive_ts", time.time() - hours * 3600)
    return brain.away.check()


def test_short_gap_does_nothing(env):
    cfg, brain, bot, fake = env
    assert brain.away.check() is None                       # 第一次启动：没有“上次”
    brain.db.set("alive_ts", time.time() - 600)
    assert brain.away.check() is None and brain.away.current() is None
    assert abs(brain.db.get("alive_ts") - time.time()) < 5


def test_life_during_picks_overlapping_lines(env):
    cfg, brain, bot, fake = env
    tz = cfg.tz
    d = datetime(2026, 10, 14, tzinfo=tz)
    brain.db.set("life_2026-10-14", "08:00-10:00 高数课\n12:00-13:00 和室友吃饭\n19:00-21:00 钢琴练习")
    got = brain.away.life_during(d.replace(hour=11), d.replace(hour=14))
    assert any("吃饭" in x for x in got) and not any("高数" in x or "钢琴" in x for x in got)


def test_reply_mentions_away_once(env):
    cfg, brain, bot, fake = env
    rec = _back(brain, 5, plan="00:00-23:59 在图书馆复习")
    assert rec and "图书馆" in "".join(rec["doing"])
    brain.db.add_message("user", "人呢？")
    brain.db.set("last_user_ts", time.time())
    asyncio.run(brain.reply())
    last = brain.llm.calls[-1][-1]["content"]
    assert "没看手机" in last and "5 个小时" in last and "图书馆" in last
    assert brain.away.current()["used"]
    asyncio.run(brain.reply())                               # 只说一次
    assert "没看手机" not in brain.llm.calls[-1][-1]["content"]
    from ailife.proactive import Scheduler
    sched = Scheduler(cfg, brain, bot)
    rec = brain.away.current()
    rec["to"] -= 600
    brain.db.set("away", rec)
    asyncio.run(brain.away.tick(sched, brain.now()))
    assert brain.away.current()["done"] and not [s for s in fake.sent if s[0] == "text"]


def test_catch_up_proactive_with_missed_reminder(env):
    cfg, brain, bot, fake = env
    cfg["proactive"]["quiet_hours"] = ""
    from ailife.proactive import Scheduler
    sched = Scheduler(cfg, brain, bot)
    _back(brain, 10, plan="00:00-23:59 社团活动")
    brain.db.add_reminder(time.time() - 8 * 3600, "交实验报告")
    asyncio.run(sched._reminders(brain.now()))
    assert not fake.sent                                     # 错过太久的提醒不单独发
    assert "交实验报告" in brain.away.current()["missed"][0]
    asyncio.run(brain.away.tick(sched, brain.now()))
    assert not fake.sent                                     # 刚开机：先等一会儿
    rec = brain.away.current()
    rec["to"] -= 120
    brain.db.set("away", rec)
    asyncio.run(brain.away.tick(sched, brain.now()))
    instr = brain.llm.calls[-1][-1]["content"]
    assert "主动给他发消息" in instr and "社团活动" in instr and "交实验报告" in instr and "10 个小时" in instr
    assert [s for s in fake.sent if s[0] == "text"]
    assert brain.away.current()["done"]
    n = len(fake.sent)
    asyncio.run(brain.away.tick(sched, brain.now()))
    assert len(fake.sent) == n                               # 不重复


def test_catch_up_waits_for_quiet_hours(env):
    cfg, brain, bot, fake = env
    cfg["proactive"]["quiet_hours"] = "00:00-23:59"
    from ailife.proactive import Scheduler
    sched = Scheduler(cfg, brain, bot)
    _back(brain, 4)
    rec = brain.away.current()
    rec["to"] -= 300
    brain.db.set("away", rec)
    asyncio.run(brain.away.tick(sched, brain.now()))
    assert not fake.sent and not brain.away.current()["done"]


def test_missed_days_festival_and_birthday(env):
    cfg, brain, bot, fake = env
    from datetime import date
    brain.db.add_date("10-15", "他的生日", "birthday")
    out = brain.away._missed_days(date(2026, 10, 14), date(2026, 10, 17))
    assert any("他的生日" in x and "10-15" in x for x in out)
    assert brain.away._missed_days(date(2026, 10, 14), date(2026, 10, 17)) == []     # 补过一次就不再补
    out = brain.away._missed_days(date(2026, 9, 30), date(2026, 10, 2))
    assert any("国庆" in x for x in out)


def test_missed_letter(env):
    cfg, brain, bot, fake = env
    tz = cfg.tz
    sun = datetime(2026, 10, 18, 12, tzinfo=tz)              # 星期日，来信 21:00-22:30
    iso = brain.away._missed_letter(sun.timestamp(), (sun + timedelta(hours=14)).timestamp())
    assert iso.startswith("2026-10-18T21:00")
    assert brain.away._missed_letter(sun.timestamp(), (sun + timedelta(hours=5)).timestamp()) == ""


class _Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def _fake_httpx(monkeypatch, posted):
    import httpx

    class C:
        def __init__(self, **kw):
            posted.append(("init", kw))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            posted.append((url, json))
            return _Resp({"ok": True, "result": {"message_id": 99}})
    monkeypatch.setattr(httpx, "Client", C)


def test_goodbye_sync(env, monkeypatch):
    cfg, brain, bot, fake = env
    cfg["proactive"]["quiet_hours"] = ""
    posted = []
    _fake_httpx(monkeypatch, posted)
    brain.db.set("bye_lines", {"day": "我去上课啦，下课找你", "night": "我先睡啦，晚安"})
    assert brain.away.goodbye_sync()
    url, body = posted[-1]
    assert url.endswith("/sendMessage") and body["chat_id"] == 42 and body["text"] in ("我去上课啦，下课找你", "我先睡啦，晚安")
    assert brain.db.last_message("assistant")["content"] == body["text"]
    assert not brain.away.goodbye_sync()                     # 同一次退出只说一次
    brain.away._bye_done = False
    assert not brain.away.goodbye_sync()                     # 3 小时内不再道别
    # 下次开机：知道自己走之前说过什么
    brain.db.set("alive_ts", time.time() - 5 * 3600)
    bye = brain.db.get("bye")
    bye["ts"] = time.time() - 5 * 3600 - 30
    brain.db.set("bye", bye)
    rec = brain.away.check()
    assert rec["bye"] == body["text"]
    assert "走之前你跟他说过" in brain.away.note(rec, replying=False)


def test_goodbye_quiet_hours_and_disabled(env, monkeypatch):
    cfg, brain, bot, fake = env
    posted = []
    _fake_httpx(monkeypatch, posted)
    cfg["proactive"]["quiet_hours"] = "00:00-23:59"
    brain.db.set("last_user_ts", time.time() - 3600)
    assert not brain.away.goodbye_sync()                     # 半夜他早不在了：不吵他
    brain.db.set("last_user_ts", time.time() - 60)
    cfg["away"]["goodbye"] = False
    assert not brain.away.goodbye_sync()
    cfg["away"]["goodbye"] = True
    assert brain.away.goodbye_sync()                         # 他刚刚还在聊：说一声
    assert len([p for p in posted if p[0] != "init"]) == 1


def test_bye_lines_default_and_refresh(env):
    cfg, brain, bot, fake = env
    lines = brain.away.bye_lines()
    assert lines["day"] and "晚安" in lines["night"]

    async def chat(messages, **kw):
        return '好的 {"day": "我去社团了，晚点聊", "night": "困了，先睡啦"}'
    brain.llm.chat = chat
    asyncio.run(brain.away.refresh_bye_lines("2026-10-18"))
    assert brain.away.bye_lines()["day"] == "我去社团了，晚点聊"
