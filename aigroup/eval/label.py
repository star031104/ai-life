"""13.0 金标准集：你在小屋里一条条判“这一刻她该不该开口”，用来评门控、也直接进每晚的重新拟合（权重最高）。

挑哪些给你判：最近 14 天、没被叫到的判断里，优先挑最有信息量的——她说了但没人接的、问了一下“说 / 等 / 不”的、
离阈值很近的；每条给你看那一刻之前的十来句和她当时做了什么。
"""
from __future__ import annotations

import time
from datetime import datetime


def next_moment(db, gid: int | None, tz=None, days: int = 14) -> dict | None:
    q = ("SELECT g.*, e.score, e.text her_text FROM qq_gate_log g LEFT JOIN qq_reply_effect e ON e.gate_id=g.id "
         "WHERE g.mentioned=0 AND g.human IS NULL AND g.ts>?")
    args: list = [time.time() - days * 86400]
    if gid:
        q += " AND g.group_id=?"
        args.append(gid)
    q += (" ORDER BY (CASE WHEN g.spoke=1 AND IFNULL(e.score, 1)<0.2 THEN 0 WHEN g.judge!='' THEN 1 "
          "WHEN ABS(g.p-g.threshold)<0.1 THEN 2 ELSE 3 END), RANDOM() LIMIT 1")
    r = db.conn.execute(q, args).fetchone()
    if not r:
        return None
    ctx = list(reversed(db.conn.execute(
        "SELECT ts, name, role, content FROM qq_group_messages WHERE group_id=? AND ts<=? ORDER BY id DESC LIMIT 12",
        (r["group_id"], r["ts"] + 1)).fetchall()))
    lines = [f"[{datetime.fromtimestamp(c['ts'], tz):%m-%d %H:%M}] {'她' if c['role'] == 'assistant' else c['name']}："
             f"{str(c['content'])[:120]}" for c in ctx]
    asked = {"say": "说", "wait": "等", "skip": "不"}.get(r["judge"], r["judge"])
    if r["spoke"] and r["her_text"]:
        did = f"她说了：{r['her_text']}"
    elif r["passed"]:
        did = "她想了想没说"
    else:
        did = "她没开口" + (f"（拿不准，问了一下，回答是“{asked}”）" if r["judge"] else "")
    if r["spoke"] and r["score"] is not None:
        did += "（有人接）" if r["score"] >= 0.6 else "（没什么人接）"
    return {"id": r["id"], "gid": r["group_id"], "context": lines, "did": did, "spoke": bool(r["spoke"]),
            "p": round(float(r["p"]), 2), "thr": round(float(r["threshold"]), 2)}


def answer(db, gate_id: int, label: int | None):
    """label：1 该说、0 不该说、None 跳过（标成 -1，不再出现，也不当标签用）。"""
    v = -1 if label is None else (1 if int(label) else 0)
    db.conn.execute("UPDATE qq_gate_log SET human=? WHERE id=?", (v, int(gate_id)))
    db.conn.commit()


def stats(db) -> dict:
    """标了多少；她做的和你判的一致的比例；分开看“该说的她说了”（召回）和“她说的是该说的”（精确）。"""
    rows = db.conn.execute("SELECT spoke, human FROM qq_gate_log WHERE human IN (0, 1)").fetchall()
    n = len(rows)
    tp = sum(1 for r in rows if r["spoke"] and r["human"] == 1)
    fp = sum(1 for r in rows if r["spoke"] and r["human"] == 0)
    fn = sum(1 for r in rows if not r["spoke"] and r["human"] == 1)
    agree = sum(1 for r in rows if bool(r["spoke"]) == bool(r["human"]))
    return {"n": n, "agree": round(agree / n, 3) if n else None,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None}
