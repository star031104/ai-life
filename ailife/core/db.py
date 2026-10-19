"""SQLite 存储：聊天记录、长期记忆、日记、状态、提醒、贴纸库。"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from ailife.core.db_life import SCHEMA_LIFE, LifeDBMixin

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    role TEXT NOT NULL,            -- user / assistant / event
    kind TEXT NOT NULL DEFAULT 'text',
    content TEXT NOT NULL,
    tg_msg_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    content TEXT NOT NULL,
    importance INTEGER NOT NULL DEFAULT 3,
    hits INTEGER NOT NULL DEFAULT 0,
    last_used REAL
);

CREATE TABLE IF NOT EXISTS diary (
    day TEXT PRIMARY KEY,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    due_ts REAL NOT NULL,
    content TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,            -- YYYY-MM-DD（一次性/有年份）或 MM-DD（每年）
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'event',   -- anniversary 纪念日 / birthday 生日 / event 考试面试等
    remind_before INTEGER NOT NULL DEFAULT 1,
    created REAL
);

CREATE TABLE IF NOT EXISTS shows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    weekday INTEGER NOT NULL,      -- 0=周一 … 6=周日
    time TEXT NOT NULL,            -- HH:MM 更新时间
    episode INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS mood_log (
    ts REAL NOT NULL,
    mood TEXT,
    affection INTEGER
);

CREATE TABLE IF NOT EXISTS stickers (
    file_id TEXT PRIMARY KEY,
    unique_id TEXT,
    emoji TEXT,
    set_name TEXT,
    source TEXT                    -- set / user
);

CREATE TABLE IF NOT EXISTS mem_vec (
    id INTEGER PRIMARY KEY,        -- = memories.id
    model TEXT NOT NULL,
    vec BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (     -- 还没聊完的话题 / 在等结果的事
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    topic TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', -- open / closed
    last_used REAL
);

CREATE TABLE IF NOT EXISTS proactive_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    reason TEXT,
    replied_ts REAL
);

CREATE TABLE IF NOT EXISTS expressions (  -- 从聊天里学到的口头禅、你们的梗
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    phrase TEXT NOT NULL,
    meaning TEXT,
    seen INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending / approved / rejected
    weight REAL NOT NULL DEFAULT 1.0,
    uses INTEGER NOT NULL DEFAULT 0,
    last_used REAL
);

CREATE TABLE IF NOT EXISTS notes (        -- 小屋留言板（他和她都能写）
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    author TEXT NOT NULL,                 -- user / her
    content TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS arcs (          -- 她自己的故事线（一周到一个月的连续事件）
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    start_day TEXT NOT NULL,
    end_day TEXT NOT NULL,
    beats TEXT NOT NULL,                  -- JSON: [{"day":"2026-10-14","text":"…"}]
    status TEXT NOT NULL DEFAULT 'active',-- active / done
    outcome TEXT,
    created REAL
);

CREATE TABLE IF NOT EXISTS thoughts (      -- 她的内心独白
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    thought TEXT NOT NULL,
    mood TEXT,
    contact INTEGER NOT NULL DEFAULT 0,
    why TEXT,
    sent INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS profiles (      -- 她每周整理的“对你的了解”
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS music_log (     -- 你听过的歌 / 她推荐的歌
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    title TEXT NOT NULL,
    artist TEXT,
    album TEXT,
    song_id INTEGER,
    source TEXT NOT NULL DEFAULT 'pc'     -- pc / her
);

CREATE TABLE IF NOT EXISTS recaps (        -- 每月纪念册
    month TEXT PRIMARY KEY,               -- 2026-10
    ts REAL NOT NULL,
    content TEXT NOT NULL,
    stats TEXT
);

CREATE TABLE IF NOT EXISTS habits (        -- 一起打卡的目标
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    remind_time TEXT,                     -- HH:MM，到点没打卡她会来催
    active INTEGER NOT NULL DEFAULT 1,
    created REAL
);

CREATE TABLE IF NOT EXISTS checkins (
    habit_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (habit_id, day)
);

CREATE TABLE IF NOT EXISTS his_mood (      -- 他每天的情绪（从文字粗略统计）
    day TEXT PRIMARY KEY,
    neg INTEGER NOT NULL DEFAULT 0,
    pos INTEGER NOT NULL DEFAULT 0,
    n INTEGER NOT NULL DEFAULT 0,
    words TEXT,
    crisis INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS letters (
    week TEXT PRIMARY KEY,                -- 2026-W41
    ts REAL NOT NULL,
    content TEXT NOT NULL
);
"""

# 老数据库升级：缺哪列补哪列（不会动已有数据）
MIGRATIONS = [
    ("memories", "owner", "ALTER TABLE memories ADD COLUMN owner TEXT NOT NULL DEFAULT 'user'"),
    ("stickers", "emotion", "ALTER TABLE stickers ADD COLUMN emotion TEXT"),
    ("shows", "subject_id", "ALTER TABLE shows ADD COLUMN subject_id INTEGER"),
    ("shows", "total_eps", "ALTER TABLE shows ADD COLUMN total_eps INTEGER"),
    ("shows", "name_cn", "ALTER TABLE shows ADD COLUMN name_cn TEXT"),
    ("stickers", "thumb", "ALTER TABLE stickers ADD COLUMN thumb TEXT"),
    ("stickers", "label_tried", "ALTER TABLE stickers ADD COLUMN label_tried INTEGER NOT NULL DEFAULT 0"),
    ("mood_log", "joy", "ALTER TABLE mood_log ADD COLUMN joy REAL"),
    ("mood_log", "longing", "ALTER TABLE mood_log ADD COLUMN longing REAL"),
    ("mood_log", "sulk", "ALTER TABLE mood_log ADD COLUMN sulk REAL"),
    ("mood_log", "tired", "ALTER TABLE mood_log ADD COLUMN tired REAL"),
    ("messages", "channel", "ALTER TABLE messages ADD COLUMN channel TEXT"),   # tg / qq（旧记录为空＝tg）
]


class DB(LifeDBMixin):
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        try:        # WAL：网页、后台任务同时读写也不容易“数据库被锁”
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            pass
        self.conn.executescript(SCHEMA)
        self.conn.executescript(SCHEMA_LIFE)
        for table, col, sql in MIGRATIONS:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.conn.execute(sql)
        self.conn.commit()
        self.path = Path(path)

    # ---------- messages ----------
    def add_message(self, role: str, content: str, kind: str = "text",
                    tg_msg_id: int | None = None, ts: float | None = None, channel: str | None = None) -> int:
        """tg_msg_id：消息在聊天软件里的编号（QQ 的也存这里，用 channel 区分是哪边的）。"""
        cur = self.conn.execute(
            "INSERT INTO messages(ts, role, kind, content, tg_msg_id, channel) VALUES (?,?,?,?,?,?)",
            (ts or time.time(), role, kind, content, tg_msg_id, channel),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_message(self, mid: int, content: str):
        self.conn.execute("UPDATE messages SET content=? WHERE id=?", (content, mid))
        self.conn.commit()

    def recent_messages(self, n: int) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return list(reversed(rows))

    def messages_between(self, t0: float, t1: float) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM messages WHERE ts>=? AND ts<? ORDER BY id", (t0, t1)
        ).fetchall()

    def messages_after_id(self, mid: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM messages WHERE id>? ORDER BY id", (mid,)
        ).fetchall()

    def last_message(self, role: str | None = None):
        if role:
            return self.conn.execute(
                "SELECT * FROM messages WHERE role=? ORDER BY id DESC LIMIT 1", (role,)
            ).fetchone()
        return self.conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 1").fetchone()

    # ---------- memories ----------
    def add_memory(self, content: str, importance: int = 3, owner: str = "user") -> int:
        cur = self.conn.execute(
            "INSERT INTO memories(ts, content, importance, owner) VALUES (?,?,?,?)",
            (time.time(), content, int(importance), owner if owner in ("user", "self", "us") else "user"),
        )
        self.conn.commit()
        return cur.lastrowid

    def all_memories(self, owner: str | None = None) -> list[sqlite3.Row]:
        """owner: None=全部；'user'=关于他的（含你们共同的 us）；'self'=她自己说过的。"""
        if owner == "self":
            return self.conn.execute("SELECT * FROM memories WHERE owner='self' ORDER BY id").fetchall()
        if owner == "user":
            return self.conn.execute("SELECT * FROM memories WHERE owner!='self' ORDER BY id").fetchall()
        return self.conn.execute("SELECT * FROM memories ORDER BY id").fetchall()

    def get_memory(self, mid: int):
        return self.conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()

    # ---------- 记忆向量 ----------
    def get_vecs(self, model: str) -> dict[int, bytes]:
        return {r["id"]: r["vec"] for r in
                self.conn.execute("SELECT id, vec FROM mem_vec WHERE model=?", (model,))}

    def set_vec(self, mid: int, model: str, vec: bytes):
        self.conn.execute("INSERT OR REPLACE INTO mem_vec(id, model, vec) VALUES (?,?,?)", (mid, model, vec))
        self.conn.commit()

    def update_memory(self, mid: int, content: str | None = None, importance: int | None = None):
        if content is not None:
            self.conn.execute("UPDATE memories SET content=? WHERE id=?", (content, mid))
            self.conn.execute("DELETE FROM mem_vec WHERE id=?", (mid,))      # 内容变了，向量重算
        if importance is not None:
            self.conn.execute("UPDATE memories SET importance=? WHERE id=?", (importance, mid))
        self.conn.commit()

    def delete_memory(self, mid: int):
        self.conn.execute("DELETE FROM memories WHERE id=?", (mid,))
        self.conn.execute("DELETE FROM mem_vec WHERE id=?", (mid,))
        self.conn.commit()

    def touch_memories(self, ids: list[int]):
        now = time.time()
        self.conn.executemany(
            "UPDATE memories SET hits=hits+1, last_used=? WHERE id=?", [(now, i) for i in ids]
        )
        self.conn.commit()

    # ---------- diary ----------
    def set_diary(self, day: str, content: str):
        self.conn.execute("INSERT OR REPLACE INTO diary(day, content) VALUES (?,?)", (day, content))
        self.conn.commit()

    def recent_diary(self, n: int = 3) -> list[sqlite3.Row]:
        rows = self.conn.execute("SELECT * FROM diary ORDER BY day DESC LIMIT ?", (n,)).fetchall()
        return list(reversed(rows))

    def get_diary(self, day: str):
        return self.conn.execute("SELECT * FROM diary WHERE day=?", (day,)).fetchone()

    # ---------- state ----------
    def get(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any):
        self.conn.execute(
            "INSERT OR REPLACE INTO state(key, value) VALUES (?,?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self.conn.commit()

    # ---------- reminders ----------
    def add_reminder(self, due_ts: float, content: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO reminders(due_ts, content) VALUES (?,?)", (due_ts, content)
        )
        self.conn.commit()
        return cur.lastrowid

    def due_reminders(self, now: float) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM reminders WHERE done=0 AND due_ts<=? ORDER BY due_ts", (now,)
        ).fetchall()

    def pending_reminders(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM reminders WHERE done=0 ORDER BY due_ts"
        ).fetchall()

    def finish_reminder(self, rid: int):
        self.conn.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
        self.conn.commit()

    # ---------- stickers ----------
    def add_sticker(self, file_id: str, unique_id: str, emoji: str | None,
                    set_name: str | None, source: str, thumb: str | None = None):
        exists = self.conn.execute(
            "SELECT 1 FROM stickers WHERE unique_id=?", (unique_id,)
        ).fetchone()
        if exists:
            return
        self.conn.execute(
            "INSERT OR IGNORE INTO stickers(file_id, unique_id, emoji, set_name, source, thumb) VALUES (?,?,?,?,?,?)",
            (file_id, unique_id, emoji or "", set_name or "", source, thumb),
        )
        self.conn.commit()

    # 贴纸表里同时有 Telegram 的贴纸和从 QQ 学来的表情包（file_id 以 "qq:" 开头），各用各的
    @staticmethod
    def _plat(qq: bool) -> str:
        return "file_id LIKE 'qq:%'" if qq else "file_id NOT LIKE 'qq:%'"

    def stickers_with_emoji(self, emojis: list[str], qq: bool = False) -> list[sqlite3.Row]:
        if not emojis:
            return []
        q = " OR ".join(["emoji LIKE ?"] * len(emojis))
        return self.conn.execute(
            f"SELECT * FROM stickers WHERE ({q}) AND {self._plat(qq)}", [f"%{e}%" for e in emojis]
        ).fetchall()

    def stickers_with_emotion(self, emotion: str, qq: bool = False) -> list[sqlite3.Row]:
        if not emotion:
            return []
        rows = self.conn.execute(f"SELECT * FROM stickers WHERE emotion=? AND {self._plat(qq)}", (emotion,)).fetchall()
        if rows:
            return rows
        return [r for r in self.conn.execute(
                    f"SELECT * FROM stickers WHERE emotion IS NOT NULL AND emotion!='' AND {self._plat(qq)}")
                if r["emotion"] in emotion or emotion in r["emotion"]]

    def unlabeled_stickers(self, n: int = 5, qq: bool = False) -> list[sqlite3.Row]:
        return self.conn.execute(
            f"SELECT * FROM stickers WHERE (emotion IS NULL OR emotion='') AND label_tried<3 AND {self._plat(qq)} "
            "ORDER BY source='user' DESC LIMIT ?", (n,)).fetchall()

    def set_sticker_emotion(self, file_id: str, emotion: str | None):
        if emotion:
            self.conn.execute("UPDATE stickers SET emotion=? WHERE file_id=?", (emotion, file_id))
        else:
            self.conn.execute("UPDATE stickers SET label_tried=label_tried+1 WHERE file_id=?", (file_id,))
        self.conn.commit()

    def sticker_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM stickers").fetchone()["c"]

    # ---------- dates ----------
    def add_date(self, date: str, name: str, kind: str = "event", remind_before: int = 1) -> int:
        for row in self.conn.execute("SELECT id, name FROM dates WHERE date=?", (date,)).fetchall():
            if row["name"] in name or name in row["name"]:
                return row["id"]
        cur = self.conn.execute(
            "INSERT INTO dates(date, name, kind, remind_before, created) VALUES (?,?,?,?,?)",
            (date, name, kind, int(remind_before), time.time()))
        self.conn.commit()
        return cur.lastrowid

    def all_dates(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM dates ORDER BY date").fetchall()

    def delete_date(self, did: int):
        self.conn.execute("DELETE FROM dates WHERE id=?", (did,))
        self.conn.commit()

    # ---------- shows ----------
    def add_show(self, name: str, weekday: int, hhmm: str, episode: int = 0) -> int:
        dup = self.conn.execute("SELECT id FROM shows WHERE name=? AND active=1", (name,)).fetchone()
        if dup:
            self.conn.execute("UPDATE shows SET weekday=?, time=? WHERE id=?", (weekday, hhmm, dup["id"]))
            self.conn.commit()
            return dup["id"]
        cur = self.conn.execute("INSERT INTO shows(name, weekday, time, episode) VALUES (?,?,?,?)",
                                (name, int(weekday), hhmm, int(episode)))
        self.conn.commit()
        return cur.lastrowid

    def active_shows(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM shows WHERE active=1 ORDER BY weekday, time").fetchall()

    def bump_show(self, sid: int):
        self.conn.execute("UPDATE shows SET episode=episode+1 WHERE id=?", (sid,))
        self.conn.commit()

    def delete_show(self, sid: int):
        self.conn.execute("UPDATE shows SET active=0 WHERE id=?", (sid,))
        self.conn.commit()

    # ---------- mood log ----------
    def log_mood(self, mood: str, affection: int, emo: dict | None = None):
        """心情曲线：每 30 分钟最多记一个点（心情文字变了立刻记）。"""
        last = self.conn.execute("SELECT * FROM mood_log ORDER BY ts DESC LIMIT 1").fetchone()
        if last and last["mood"] == mood and time.time() - last["ts"] < 30 * 60:
            return
        e = emo or {}
        self.conn.execute(
            "INSERT INTO mood_log(ts, mood, affection, joy, longing, sulk, tired) VALUES (?,?,?,?,?,?,?)",
            (time.time(), mood, int(affection), e.get("joy"), e.get("longing"), e.get("sulk"), e.get("tired")))
        self.conn.commit()

    def mood_history(self, days: int = 30) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM mood_log WHERE ts>=? ORDER BY ts",
                                 (time.time() - days * 86400,)).fetchall()

    # ---------- 未完话题 ----------
    def add_thread(self, topic: str) -> int:
        for r in self.open_threads(50):
            if r["topic"] in topic or topic in r["topic"]:
                self.conn.execute("UPDATE threads SET topic=?, ts=? WHERE id=?", (topic, time.time(), r["id"]))
                self.conn.commit()
                return r["id"]
        cur = self.conn.execute("INSERT INTO threads(ts, topic) VALUES (?,?)", (time.time(), topic))
        self.conn.commit()
        return cur.lastrowid

    def open_threads(self, n: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM threads WHERE status='open' ORDER BY ts DESC LIMIT ?", (n,)).fetchall()

    def close_thread(self, tid: int):
        self.conn.execute("UPDATE threads SET status='closed' WHERE id=?", (tid,))
        self.conn.commit()

    def close_threads_matching(self, topic: str):
        for r in self.open_threads(50):
            if r["topic"] in topic or topic in r["topic"]:
                self.close_thread(r["id"])

    def use_thread(self, tid: int):
        self.conn.execute("UPDATE threads SET last_used=? WHERE id=?", (time.time(), tid))
        self.conn.commit()

    def expire_threads(self, days: float = 14):
        self.conn.execute("UPDATE threads SET status='closed' WHERE status='open' AND ts<?",
                          (time.time() - days * 86400,))
        self.conn.commit()

    # ---------- 主动消息回复统计 ----------
    def log_proactive(self, reason: str) -> int:
        cur = self.conn.execute("INSERT INTO proactive_log(ts, reason) VALUES (?,?)", (time.time(), reason[:200]))
        self.conn.commit()
        return cur.lastrowid

    def mark_proactive_replied(self, ts: float, within: float = 6 * 3600):
        self.conn.execute("UPDATE proactive_log SET replied_ts=? WHERE replied_ts IS NULL AND ts>=? AND ts<=?",
                          (ts, ts - within, ts))
        self.conn.commit()

    def proactive_history(self, days: int = 60) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM proactive_log WHERE ts>=? ORDER BY ts",
                                 (time.time() - days * 86400,)).fetchall()

    def user_message_times(self, days: int = 60) -> list[float]:
        return [r["ts"] for r in self.conn.execute(
            "SELECT ts FROM messages WHERE role='user' AND ts>=? ORDER BY ts", (time.time() - days * 86400,))]

    # ---------- 表达 / 梗 ----------
    def add_expression(self, phrase: str, meaning: str = "") -> int:
        row = self.conn.execute("SELECT * FROM expressions WHERE phrase=?", (phrase,)).fetchone()
        if row:
            self.conn.execute("UPDATE expressions SET seen=seen+1, meaning=COALESCE(NULLIF(?, ''), meaning) "
                              "WHERE id=?", (meaning, row["id"]))
            self.conn.commit()
            return row["id"]
        cur = self.conn.execute("INSERT INTO expressions(ts, phrase, meaning) VALUES (?,?,?)",
                                (time.time(), phrase, meaning))
        self.conn.commit()
        return cur.lastrowid

    def expressions(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.conn.execute("SELECT * FROM expressions WHERE status=? ORDER BY weight DESC, seen DESC",
                                     (status,)).fetchall()
        return self.conn.execute("SELECT * FROM expressions ORDER BY status, weight DESC, seen DESC").fetchall()

    def set_expression(self, eid: int, **kw):
        for k, v in kw.items():
            if k in ("status", "weight", "uses", "last_used", "meaning", "phrase"):
                self.conn.execute(f"UPDATE expressions SET {k}=? WHERE id=?", (v, eid))
        self.conn.commit()

    def delete_expression(self, eid: int):
        self.conn.execute("DELETE FROM expressions WHERE id=?", (eid,))
        self.conn.commit()

    # ---------- 留言板 / 来信 ----------
    def add_note(self, author: str, content: str) -> int:
        cur = self.conn.execute("INSERT INTO notes(ts, author, content, seen) VALUES (?,?,?,?)",
                                (time.time(), author, content, 1 if author == "her" else 0))
        self.conn.commit()
        return cur.lastrowid

    def notes(self, n: int = 50) -> list[sqlite3.Row]:
        rows = self.conn.execute("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return list(reversed(rows))

    def unseen_notes(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM notes WHERE author='user' AND seen=0 ORDER BY id").fetchall()

    def mark_notes_seen(self):
        self.conn.execute("UPDATE notes SET seen=1 WHERE author='user'")
        self.conn.commit()

    def set_letter(self, week: str, content: str):
        self.conn.execute("INSERT OR REPLACE INTO letters(week, ts, content) VALUES (?,?,?)",
                          (week, time.time(), content))
        self.conn.commit()

    def get_letter(self, week: str):
        return self.conn.execute("SELECT * FROM letters WHERE week=?", (week,)).fetchone()

    def letters(self, n: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM letters ORDER BY week DESC LIMIT ?", (n,)).fetchall()

    def diary_between(self, d0: str, d1: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM diary WHERE day>=? AND day<=? ORDER BY day", (d0, d1)).fetchall()

    # ---------- 备份 ----------
    def backup(self, folder: str | Path, keep: int = 7) -> Path:
        """用 SQLite 自带的在线备份复制一份，只保留最近 keep 份。"""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        stem = self.path.stem if self.path.stem != "ailife" else "ailife"     # 其他角色的库：<角色>-日期.db
        dst = folder / f"{stem}-{time.strftime('%Y%m%d')}.db"
        out = sqlite3.connect(str(dst))
        try:
            self.conn.backup(out)
        finally:
            out.close()
        import re as _re
        olds = sorted(p for p in folder.glob(f"{stem}-*.db") if _re.fullmatch(rf"{_re.escape(stem)}-\d{{8}}\.db", p.name))
        for old in olds[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
        return dst

    # ---------- 故事线 ----------
    def add_arc(self, title: str, start_day: str, end_day: str, beats: list[dict]) -> int:
        cur = self.conn.execute("INSERT INTO arcs(title, start_day, end_day, beats, created) VALUES (?,?,?,?,?)",
                                (title, start_day, end_day, json.dumps(beats, ensure_ascii=False), time.time()))
        self.conn.commit()
        return cur.lastrowid

    def arcs(self, status: str | None = None, n: int = 20) -> list[dict]:
        q = "SELECT * FROM arcs" + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT ?"
        rows = self.conn.execute(q, ((status, n) if status else (n,))).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["beats"] = json.loads(r["beats"])
            except (TypeError, ValueError):
                d["beats"] = []
            out.append(d)
        return out

    def finish_arc(self, aid: int, outcome: str):
        self.conn.execute("UPDATE arcs SET status='done', outcome=? WHERE id=?", (outcome, aid))
        self.conn.commit()

    # ---------- 内心独白 ----------
    def add_thought(self, thought: str, mood: str = "", contact: bool = False, why: str = "") -> int:
        cur = self.conn.execute("INSERT INTO thoughts(ts, thought, mood, contact, why) VALUES (?,?,?,?,?)",
                                (time.time(), thought, mood, int(bool(contact)), why))
        self.conn.commit()
        return cur.lastrowid

    def thoughts(self, n: int = 10) -> list[sqlite3.Row]:
        rows = self.conn.execute("SELECT * FROM thoughts ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return list(reversed(rows))

    def mark_thought_sent(self, tid: int):
        self.conn.execute("UPDATE thoughts SET sent=1 WHERE id=?", (tid,))
        self.conn.commit()

    # ---------- 对他的了解 ----------
    def add_profile(self, content: str):
        self.conn.execute("INSERT INTO profiles(ts, content) VALUES (?,?)", (time.time(), content))
        self.conn.commit()

    def latest_profile(self):
        return self.conn.execute("SELECT * FROM profiles ORDER BY id DESC LIMIT 1").fetchone()

    # ---------- 听歌 ----------
    def log_music(self, title: str, artist: str = "", album: str = "", song_id: int | None = None,
                  source: str = "pc") -> int:
        last = self.conn.execute("SELECT * FROM music_log WHERE source=? ORDER BY id DESC LIMIT 1", (source,)).fetchone()
        if last and last["title"] == title and last["artist"] == artist and time.time() - last["ts"] < 3600:
            return last["id"]
        cur = self.conn.execute("INSERT INTO music_log(ts, title, artist, album, song_id, source) VALUES (?,?,?,?,?,?)",
                                (time.time(), title, artist, album, song_id, source))
        self.conn.commit()
        return cur.lastrowid

    def music(self, n: int = 30, source: str | None = None) -> list[sqlite3.Row]:
        if source:
            return self.conn.execute("SELECT * FROM music_log WHERE source=? ORDER BY id DESC LIMIT ?",
                                     (source, n)).fetchall()
        return self.conn.execute("SELECT * FROM music_log ORDER BY id DESC LIMIT ?", (n,)).fetchall()

    def top_artists(self, days: int = 60, n: int = 5) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            "SELECT artist, COUNT(*) c FROM music_log WHERE source='pc' AND ts>=? AND artist!='' "
            "GROUP BY artist ORDER BY c DESC LIMIT ?", (time.time() - days * 86400, n)).fetchall()
        return [(r["artist"], r["c"]) for r in rows]

    # ---------- 追番（Bangumi） ----------
    def set_show_meta(self, sid: int, subject_id: int | None, total_eps: int | None, name_cn: str | None):
        self.conn.execute("UPDATE shows SET subject_id=?, total_eps=?, name_cn=? WHERE id=?",
                          (subject_id, total_eps, name_cn, sid))
        self.conn.commit()

    def set_show_episode(self, sid: int, ep: int):
        self.conn.execute("UPDATE shows SET episode=? WHERE id=?", (int(ep), sid))
        self.conn.commit()

    # ---------- 纪念册 ----------
    def set_recap(self, month: str, content: str, stats: dict):
        self.conn.execute("INSERT OR REPLACE INTO recaps(month, ts, content, stats) VALUES (?,?,?,?)",
                          (month, time.time(), content, json.dumps(stats, ensure_ascii=False)))
        self.conn.commit()

    def recaps(self, n: int = 12) -> list[dict]:
        out = []
        for r in self.conn.execute("SELECT * FROM recaps ORDER BY month DESC LIMIT ?", (n,)):
            d = dict(r)
            try:
                d["stats"] = json.loads(r["stats"] or "{}")
            except ValueError:
                d["stats"] = {}
            out.append(d)
        return out

    def get_recap(self, month: str):
        return self.conn.execute("SELECT * FROM recaps WHERE month=?", (month,)).fetchone()

    # ---------- 打卡 ----------
    def add_habit(self, name: str, remind_time: str | None = None) -> int:
        row = self.conn.execute("SELECT id FROM habits WHERE name=? AND active=1", (name,)).fetchone()
        if row:
            self.conn.execute("UPDATE habits SET remind_time=? WHERE id=?", (remind_time, row["id"]))
            self.conn.commit()
            return row["id"]
        cur = self.conn.execute("INSERT INTO habits(name, remind_time, created) VALUES (?,?,?)",
                                (name, remind_time, time.time()))
        self.conn.commit()
        return cur.lastrowid

    def habits(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM habits WHERE active=1 ORDER BY id").fetchall()

    def del_habit(self, hid: int):
        self.conn.execute("UPDATE habits SET active=0 WHERE id=?", (hid,))
        self.conn.commit()

    def checkin(self, hid: int, day: str) -> bool:
        cur = self.conn.execute("INSERT OR IGNORE INTO checkins(habit_id, day, ts) VALUES (?,?,?)",
                                (hid, day, time.time()))
        self.conn.commit()
        return cur.rowcount > 0

    def checked(self, hid: int, day: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM checkins WHERE habit_id=? AND day=?", (hid, day)).fetchone())

    def checkin_days(self, hid: int, n: int = 60) -> list[str]:
        return [r["day"] for r in self.conn.execute(
            "SELECT day FROM checkins WHERE habit_id=? ORDER BY day DESC LIMIT ?", (hid, n))]

    def streak(self, hid: int, today: str) -> int:
        from datetime import date, timedelta
        days = set(self.checkin_days(hid, 400))
        d = date.fromisoformat(today)
        if today not in days:
            d -= timedelta(days=1)              # 今天还没打卡，不算断
        n = 0
        while d.isoformat() in days:
            n += 1
            d -= timedelta(days=1)
        return n

    # ---------- 他的情绪 ----------
    def add_his_mood(self, day: str, neg: int, pos: int, words: list[str], crisis: bool = False):
        row = self.conn.execute("SELECT * FROM his_mood WHERE day=?", (day,)).fetchone()
        if row:
            ws = list(dict.fromkeys(((row["words"] or "").split("、") if row["words"] else []) + words))[:12]
            self.conn.execute("UPDATE his_mood SET neg=neg+?, pos=pos+?, n=n+1, words=?, crisis=MAX(crisis, ?) "
                              "WHERE day=?", (neg, pos, "、".join(ws), int(crisis), day))
        else:
            self.conn.execute("INSERT INTO his_mood(day, neg, pos, n, words, crisis) VALUES (?,?,?,?,?,?)",
                              (day, neg, pos, 1, "、".join(words[:12]), int(crisis)))
        self.conn.commit()

    def his_moods(self, days: int = 7) -> list[sqlite3.Row]:
        rows = self.conn.execute("SELECT * FROM his_mood ORDER BY day DESC LIMIT ?", (days,)).fetchall()
        return list(reversed(rows))
