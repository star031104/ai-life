"""她的“脑子”里的小零件：检索、情绪、钟点习惯、小游戏、Mini App 签名、节日、旧库升级、感知、天气。"""
import hashlib
import hmac
import json
import shutil
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import numpy as np

TZ = ZoneInfo("Asia/Shanghai")


def test_bm25_and_hybrid():
    from ailife.mind.retrieval import BM25, hybrid_rank, tokens
    assert "橘猫" in tokens("他养了一只橘猫")
    docs = ["他养了一只橘猫叫团子", "他喜欢吃辣", "他下周三考六级"]
    sc = BM25(docs).scores("团子今天怎么样")
    assert sc[0] > 0 and sc[1] == 0
    # 语义：关键词对不上，但向量相近
    v = [np.array([1, 0, 0], np.float32), np.array([0, 1, 0], np.float32), np.array([0, 0, 1], np.float32)]
    r = hybrid_rank("宠物", docs, v, np.array([0.9, 0.1, 0], np.float32))
    assert r[0][0] == 0


def test_emotion_decay_and_events(tmp_path):
    from ailife.core.db import DB
    from ailife.mind.emotion import AFFECTION, Emotion
    db = DB(tmp_path / "e.db")
    e = Emotion(db, TZ)
    db.set("last_user_ts", time.time())
    e.nudge(sulk=+60)
    s0 = e.state()["sulk"]
    raw = db.get("emo")
    raw["ts"] -= 6 * 3600          # 6 小时后
    db.set("emo", raw)
    assert e.state()["sulk"] < s0 / 2
    db.set("last_user_ts", time.time() - 20 * 3600)
    db.set("emo", {"joy": 60, "longing": 20, "sulk": 0, "tired": 30, "ts": time.time() - 12 * 3600})
    assert e.state()["longing"] > 60            # 他很久没来，越来越想
    e.on_user_message(ignored_before=3)
    st = e.state()
    assert st["longing"] < 50 and st["sulk"] >= 20
    assert AFFECTION == 100
    assert 0.4 <= e.longing_factor() <= 1.6


def test_hour_factor():
    from datetime import datetime
    from ailife.proactive.scheduler import hour_factor
    base = datetime(2026, 10, 1, 21, 0, tzinfo=TZ).timestamp()
    rows = [{"ts": base + d * 86400, "replied_ts": base + d * 86400 + 300} for d in range(10)]
    rows += [{"ts": base - 12 * 3600 + d * 86400, "replied_ts": None} for d in range(10)]     # 早上 9 点从不回
    users = [base + d * 86400 + 60 for d in range(10)]
    assert hour_factor(rows, users, 21, TZ) > 1.3
    assert hour_factor(rows, users, 9, TZ) < 0.8
    assert hour_factor([], [], 9, TZ) == 1.0


def test_games(tmp_path):
    from ailife.core.db import DB
    from ailife.mind.games import Games, winner
    g = Games(DB(tmp_path / "g.db"), mistake_rate=0)
    for _ in range(5):                    # 她不失误时，你最多打平
        g.start("ttt")
        ended = None
        order = [4, 0, 2, 6, 8, 1, 3, 5, 7]
        while not ended:
            b = g.state["board"]
            mv = next(i for i in order if not b[i])
            _, _, ended = g.ttt_move(mv)
        assert "他赢了你" not in ended
    assert winner(["X", "X", "X", "", "", "", "", "", ""]) == "X"
    g.start("guess")
    n = g.state["n"]
    msg, ended = g.guess(n)
    assert ended and "猜中" in msg
    g.start("rps")
    for _ in range(10):
        if not g.state:
            break
        line, kb, ended = g.rps_play("r")
    assert g.state is None


def test_verify_init_data():
    from ailife.web.server import verify_init_data
    token = "123:ABC"
    fields = {"auth_date": str(int(time.time())), "query_id": "x", "user": json.dumps({"id": 42})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    data = urlencode(fields)
    assert verify_init_data(data, token) == 42
    assert verify_init_data(data, "999:other") is None
    assert verify_init_data(data.replace("42", "43"), token) is None


def test_festival():
    from datetime import date
    from ailife.proactive.scheduler import festival_today
    assert festival_today(date(2026, 2, 14)) .startswith("情人节")
    assert festival_today(date(2026, 3, 3)) in (None, "") or isinstance(festival_today(date(2026, 3, 3)), str)


def test_old_db_upgrade(tmp_path):
    from ailife.core.config import ROOT
    from ailife.core.db import DB
    src = ROOT / "data" / "ailife.db"
    if not src.exists():
        return
    dst = tmp_path / "old.db"
    shutil.copy(src, dst)
    db = DB(dst)
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(memories)")}
    assert "owner" in cols
    db.add_memory("测试", 3, "self")
    assert db.all_memories("self")
    p = db.backup(tmp_path / "bk")
    assert p.exists()


def test_sense_helpers():
    from ailife.sense.sensors import classify, normalize_phone
    assert classify(r"C:\Games\Genshin Impact\YuanShen.exe", "原神") == "打游戏"
    assert classify(r"C:\x\Code.exe", "bot.py - ai-life") == "写代码"
    assert classify(r"C:\x\msedge.exe", "【某视频】- 哔哩哔哩") == "看视频"
    assert normalize_phone("闹钟关了") == "刚起床"
    assert normalize_phone("到家啦") == "刚到家"


def test_parse_new_tags():
    from ailife.mind.output import parse_reply
    p = parse_reply("[回应:❤]好|||[撤回]其实我想你")
    assert p.reaction == "❤" and p.bubbles[1].recall and not p.bubbles[0].recall


def test_relative_dates():
    from datetime import datetime
    from ailife.mind.output import normalize_date, parse_when, resolve_day
    b = datetime(2026, 10, 12, 15, 0, tzinfo=TZ)      # 周一
    assert str(resolve_day("下周二", b)) == "2026-10-20"
    assert str(resolve_day("这周五", b)) == "2026-10-16"
    assert str(resolve_day("5号", b)) == "2026-11-05"
    assert str(resolve_day("三天后", b)) == "2026-10-15"
    assert parse_when("下周二 10:00", b).day == 20
    assert normalize_date("明天", b) == "2026-10-13"


def test_weather_zh():
    from ailife.mind.world import zh_weather
    assert zh_weather("Smoky haze", "143") == "霾"
    assert zh_weather("Patchy rain nearby", "176") == "局部阵雨"
    assert zh_weather("Thunderstorm in vicinity") == "雷雨"
    assert zh_weather("晴") == "晴"
