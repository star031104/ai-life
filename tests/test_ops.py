"""11.2 服务器小管家 deploy/server/ops.py：备份、体检、掉线提醒（假的目录和接口，不联网）。"""
import asyncio
import importlib.util
import json
import sqlite3
import tarfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(tmp_path, monkeypatch):
    monkeypatch.setenv("AILIFE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AILIFE_WWW", str(tmp_path / "www"))
    spec = importlib.util.spec_from_file_location("ops", ROOT / "deploy" / "server" / "ops.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _fake_home(home: Path):
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")
    stats = json.dumps({"calls": 10, "ok": 9, "tok_hit": 1000, "tok_miss": 200, "tok_out": 50, "usd": 0.012})
    d = home / "ai-life" / "data"
    d.mkdir(parents=True)
    c = sqlite3.connect(d / "ailife.db")
    c.executescript("CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT);"
                    "CREATE TABLE messages(id INTEGER PRIMARY KEY, ts REAL, role TEXT, content TEXT);"
                    "CREATE TABLE memories(id INTEGER PRIMARY KEY, ts REAL, content TEXT, owner TEXT);")
    c.execute("INSERT INTO state VALUES (?,?)", (f"llm_stats_{today}", stats))
    c.executemany("INSERT INTO messages(ts, role, content) VALUES (?,?,?)",
                  [(time.time(), "user", "hi"), (time.time(), "assistant", "嗯")])
    c.commit()
    c.close()
    (home / "ai-life" / "config.yaml").write_text("qq:\n  ws_url: ws://127.0.0.1:1\n  owner_id: 42\n", encoding="utf-8")
    (home / "ai-life" / ".env").write_text("SECRET=1\n", encoding="utf-8")
    for name in ("ai-life-group", "ai-life-wechat"):
        g = home / name / "data"
        g.mkdir(parents=True)
        c = sqlite3.connect(g / "group.db")
        c.executescript("CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT);"
                        "CREATE TABLE qq_group_messages(id INTEGER PRIMARY KEY, ts REAL, role TEXT, content TEXT);"
                        "CREATE TABLE qq_knowledge(id INTEGER PRIMARY KEY, ts REAL);"
                        "CREATE TABLE qq_plans(id INTEGER PRIMARY KEY, fired REAL);")
        c.execute("INSERT INTO kv VALUES (?,?)", (f"llm_stats_{today}", stats))
        c.execute("INSERT INTO kv VALUES (?,?)", ("qq_shares", json.dumps([{"ts": time.time(), "kind": "news"}])))
        c.execute("INSERT INTO qq_knowledge(ts) VALUES (?)", (time.time(),))
        c.executemany("INSERT INTO qq_group_messages(ts, role, content) VALUES (?,?,?)",
                      [(time.time(), "user", "a"), (time.time(), "user", "b"), (time.time(), "assistant", "c")])
        c.commit()
        c.close()
        (home / name / "persona").mkdir()
        (home / name / "persona" / "local_x.md").write_text("人设", encoding="utf-8")


def test_backup_and_report(tmp_path, monkeypatch):
    ops = _load(tmp_path, monkeypatch)
    _fake_home(tmp_path / "home")
    monkeypatch.setattr(ops, "journal_errors", lambda svc, since="7 days ago": 3)
    dst = ops.backup()
    with tarfile.open(dst) as t:
        names = t.getnames()
    assert any(n.endswith("ai-life/data/ailife.db") for n in names)
    assert any(n.endswith("ai-life-group/persona/local_x.md") for n in names)
    assert not any(n.endswith(".env") for n in names)                 # 密钥不进备份
    assert (tmp_path / "home" / "backups" / "latest.tar.gz").exists()
    r = ops.report()
    yuki, qq = r["bots"][0], r["bots"][1]
    assert yuki["calls"][-1] == 10 and yuki["fail"][-1] == 1 and yuki["her"][-1] == 1
    assert qq["msgs"][-1] == 2 and qq["shares"] == 1 and qq["learned"] == 1 and qq["errors"] == 3
    assert r["total_usd"] == 0.036
    assert json.loads((tmp_path / "www" / "report.json").read_text(encoding="utf-8"))["days"][-1] == r["days"][-1]
    sp = ops.spend_today()
    assert sp["bots"] == {"yuki": 0.0864, "qq": 0.0864, "wx": 0.0864} and sp["cap"] == 0
    assert json.loads((tmp_path / "home" / "budget.json").read_text(encoding="utf-8"))["total"] == sp["total"]


def test_watch_alerts_after_two_fails(tmp_path, monkeypatch):
    ops = _load(tmp_path, monkeypatch)
    _fake_home(tmp_path / "home")
    sent = []

    async def notify(text):
        sent.append(text)
        return True

    async def check(b):
        ok = b["key"] != "qq"
        return {"key": b["key"], "label": b["label"], "kind": b["kind"], "path": b["path"], "ok": ok,
                "why": "" if ok else "QQ 掉线了", "today": 1, "her_today": 0, "name": b["label"], "service": True}
    monkeypatch.setattr(ops, "notify", notify)
    monkeypatch.setattr(ops, "check_bot", check)
    asyncio.run(ops.watch())
    assert sent == []                                               # 第一次不正常：先不吵
    asyncio.run(ops.watch())
    assert len(sent) == 1 and "QQ 群的小满掉线了" in sent[0]
    asyncio.run(ops.watch())
    assert len(sent) == 1                                           # 已经说过了：6 小时内不再说
    st = json.loads((tmp_path / "www" / "status.json").read_text(encoding="utf-8"))
    assert [b["ok"] for b in st["bots"]] == [True, False, True] and st["alerts"]
    monkeypatch.setattr(ops, "check_bot", _ok)
    asyncio.run(ops.watch())
    assert "恢复了" in sent[-1]


async def _ok(b):
    return {"key": b["key"], "label": b["label"], "kind": b["kind"], "path": b["path"], "ok": True, "why": "",
            "today": 0, "her_today": 0, "name": b["label"], "service": True}
