"""她的生活相关的数据表：她身边的人、她的长期目标、愿望清单、时间胶囊。"""
from __future__ import annotations

import json
import time

SCHEMA_LIFE = """
CREATE TABLE IF NOT EXISTS cast_people (   -- 她身边固定的人（室友、朋友、老师、家人……）
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT '',
    descr TEXT NOT NULL DEFAULT '',
    core INTEGER NOT NULL DEFAULT 0,        -- 1：常驻（每次聊天都知道）；0：后来出现的
    mentions INTEGER NOT NULL DEFAULT 0,
    first_ts REAL,
    last_ts REAL
);

CREATE TABLE IF NOT EXISTS goals (          -- 她自己的长期目标（几个月）
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    why TEXT,
    start_day TEXT NOT NULL,
    end_day TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',  -- active / done
    plan TEXT NOT NULL,                     -- JSON [{day, text, progress}]
    outcome TEXT,
    created REAL
);

CREATE TABLE IF NOT EXISTS wishes (         -- 想一起做的事
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    by TEXT NOT NULL DEFAULT 'user',        -- user / her
    created REAL NOT NULL,
    done_ts REAL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS capsules (       -- 时间胶囊：到那天才能打开
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    by TEXT NOT NULL DEFAULT 'user',        -- user：他写给她；her：她写给他
    created REAL NOT NULL,
    open_day TEXT NOT NULL,
    opened_ts REAL,
    reply TEXT
);
"""


class LifeDBMixin:
    conn = None

    # ---------- 她身边的人 ----------
    def cast_all(self, core_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM cast_people" + (" WHERE core=1" if core_only else "") + " ORDER BY core DESC, mentions DESC, id"
        return [dict(r) for r in self.conn.execute(sql)]

    def cast_add(self, name: str, role: str = "", descr: str = "", core: bool = False) -> int | None:
        name = (name or "").strip()[:12]
        if not name:
            return None
        row = self.conn.execute("SELECT id FROM cast_people WHERE name=?", (name,)).fetchone()
        if row:
            return None
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO cast_people(name, role, descr, core, first_ts, last_ts) VALUES (?,?,?,?,?,?)",
            (name, (role or "")[:20], (descr or "")[:80], 1 if core else 0, now, now))
        self.conn.commit()
        return cur.lastrowid

    def cast_touch(self, names: list[str]):
        now = time.time()
        for n in names:
            self.conn.execute("UPDATE cast_people SET mentions=mentions+1, last_ts=? WHERE name=?", (now, n))
        self.conn.commit()

    def cast_del(self, pid: int):
        self.conn.execute("DELETE FROM cast_people WHERE id=?", (pid,))
        self.conn.commit()

    # ---------- 她的目标 ----------
    def goals(self, status: str | None = "active", limit: int = 10) -> list[dict]:
        if status:
            rows = self.conn.execute("SELECT * FROM goals WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        else:
            rows = self.conn.execute("SELECT * FROM goals ORDER BY id DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["plan"] = json.loads(d["plan"] or "[]")
            except (TypeError, ValueError):
                d["plan"] = []
            out.append(d)
        return out

    def add_goal(self, title: str, why: str, start_day: str, end_day: str, plan: list[dict]) -> int:
        cur = self.conn.execute(
            "INSERT INTO goals(title, why, start_day, end_day, plan, created) VALUES (?,?,?,?,?,?)",
            (title, why, start_day, end_day, json.dumps(plan, ensure_ascii=False), time.time()))
        self.conn.commit()
        return cur.lastrowid

    def finish_goal(self, gid: int, outcome: str):
        self.conn.execute("UPDATE goals SET status='done', outcome=? WHERE id=?", (outcome, gid))
        self.conn.commit()

    # ---------- 愿望清单 ----------
    def wishes(self, include_done: bool = True) -> list[dict]:
        sql = "SELECT * FROM wishes" + ("" if include_done else " WHERE done_ts IS NULL") + \
              " ORDER BY (done_ts IS NOT NULL), COALESCE(done_ts, created) DESC"
        return [dict(r) for r in self.conn.execute(sql)]

    def add_wish(self, text: str, by: str = "user") -> int | None:
        text = (text or "").strip()[:60]
        if not text:
            return None
        row = self.conn.execute("SELECT id FROM wishes WHERE text=? AND done_ts IS NULL", (text,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute("INSERT INTO wishes(text, by, created) VALUES (?,?,?)", (text, by, time.time()))
        self.conn.commit()
        return cur.lastrowid

    def done_wish(self, wid: int, note: str | None = None) -> bool:
        cur = self.conn.execute("UPDATE wishes SET done_ts=?, note=? WHERE id=? AND done_ts IS NULL",
                                (time.time(), (note or "")[:120] or None, wid))
        self.conn.commit()
        return cur.rowcount > 0

    def del_wish(self, wid: int):
        self.conn.execute("DELETE FROM wishes WHERE id=?", (wid,))
        self.conn.commit()

    def wishes_done_between(self, t0: float, t1: float) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM wishes WHERE done_ts>=? AND done_ts<? ORDER BY done_ts", (t0, t1))]

    # ---------- 时间胶囊 ----------
    def capsules(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM capsules ORDER BY open_day, id")]

    def add_capsule(self, content: str, open_day: str, by: str = "user") -> int:
        cur = self.conn.execute("INSERT INTO capsules(content, by, created, open_day) VALUES (?,?,?,?)",
                                (content[:2000], by, time.time(), open_day))
        self.conn.commit()
        return cur.lastrowid

    def due_capsules(self, today: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM capsules WHERE opened_ts IS NULL AND open_day<=? ORDER BY open_day, id", (today,))]

    def open_capsule(self, cid: int, reply: str | None = None):
        self.conn.execute("UPDATE capsules SET opened_ts=?, reply=? WHERE id=?", (time.time(), reply, cid))
        self.conn.commit()

    def del_capsule(self, cid: int):
        self.conn.execute("DELETE FROM capsules WHERE id=? AND opened_ts IS NULL", (cid,))
        self.conn.commit()
