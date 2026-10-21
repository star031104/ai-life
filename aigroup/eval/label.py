"""金标准集：你在小屋里判“这一刻她该不该开口”（13.0 起）。

20.0 改成三档、能批量、能看清她当时是怎么决定的：
- 该接（1）：她不接就像没听见 / 冷场，或者接一句明显让聊天更好；顺手可以选“该怎么接”（接住、回答、追问……）
- 可接可不接（2）：接了不奇怪、不接也正常。不当标签用（以前只有两档，这种时刻多半被判成“该说”，把“该说没说”算高了）
- 不该插（0）：在跟别人说、话没说完、插进去突兀或者抢话
- 看不出来（-1）：上下文不够，跳过

你判的用在四个地方：每晚重新拟合门控权重（权重最高）、训练小判断器（两类各 30 条以上）、
拿不准时问模型的 few-shot 例子、每周“像不像人”的该说没说率 / 不该说却说率。

挑哪些给你判（20.0 改成按比例混着出，免得只看到她没开口的时刻、判不出“不该说”）：
她主动说了的（有人接 / 没人接都有）约 4 成、她差一点就说了的（离门槛近、问过一下的）约 4 成、随便挑的约 2 成。
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime

LABELS = {1: "该接", 2: "可接可不接", 0: "不该插", -1: "看不出来"}
HOW = ["接住", "回答", "追问", "接梗", "纠错", "补充", "关心"]


def _pick_sql(kind: str) -> str:
    base = ("SELECT g.*, e.score, e.text her_text FROM qq_gate_log g LEFT JOIN qq_reply_effect e ON e.gate_id=g.id "
            "WHERE g.mentioned=0 AND g.human IS NULL AND g.ts>? {gid} AND {cond} ORDER BY RANDOM() LIMIT ?")
    cond = {"spoke": "g.spoke=1",
            "near": "g.spoke=0 AND (g.judge!='' OR g.passed=1 OR ABS(g.p-g.threshold)<0.12)",
            "any": "1=1"}[kind]
    return base.replace("{cond}", cond)


def _rows(db, gid: int | None, n: int, days: int, exclude: set[int]) -> list:
    since = time.time() - days * 86400
    want = [("spoke", max(1, round(n * 0.4))), ("near", max(1, round(n * 0.4))), ("any", n)]
    out, seen = [], set(exclude)
    for kind, k in want:
        if len(out) >= n:
            break
        q = _pick_sql(kind).replace("{gid}", "AND g.group_id=?" if gid else "")
        args = [since] + ([gid] if gid else []) + [k * 3]
        for r in db.conn.execute(q, args).fetchall():
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            out.append(r)
            if len(out) >= n or sum(1 for x in out if _kind(x) == kind) >= k and kind != "any":
                break
    random.shuffle(out)
    return out[:n]


def _kind(r) -> str:
    if r["spoke"]:
        return "spoke"
    if r["judge"] or r["passed"] or abs(float(r["p"]) - float(r["threshold"])) < 0.12:
        return "near"
    return "any"


def steps(r) -> list[str]:
    """这一刻她是怎么决定的，按顺序用人话写出来（四层：本地门控 → 小判断器 / 问一下 → 她自己想 → 发出去）。"""
    keys = r.keys() if hasattr(r, "keys") else []
    g = (lambda k, d=None: r[k] if k in keys else d)
    p, thr = float(g("p", 0)), float(g("threshold", 0))
    out = []
    feats = {}
    try:
        feats = json.loads(g("feats") or "{}")
    except ValueError:
        pass
    reasons = feat_words(feats)
    gap = "到了" if p > thr else "没到"
    out.append(f"① 本地门控（不调模型，按这一批消息算）：想说的程度 {p:.2f}，这一小时的门槛 {thr:.2f} → {gap}门槛"
               + (f"。主要因为：{reasons}" if reasons else ""))
    if g("explore"):
        out.append("　（这次是随机放行的一小部分：本来该拦下，留作对照，以后重新拟合时不偏）")
    act, why, judge = g("act", "") or "", g("why", "") or "", g("judge", "") or ""
    if judge:
        jw = {"say": "说", "wait": "等", "skip": "不说"}.get(judge, judge)
        if act or why:
            out.append(f"② 离门槛不远，问了一下便宜模型 → {act or jw}" + (f"（理由：{why}）" if why else ""))
        else:
            out.append(f"② 离门槛不远：小判断器或便宜模型给的结论 → {jw}")
    elif why and not g("passed"):
        out.append(f"② {why}")
    if g("passed"):
        if g("spoke"):
            txt = g("her_text") or ""
            out.append("③ 她自己想了一下，决定说" + (f"：「{txt[:60]}」" if txt else ""))
            sc = g("score")
            if sc is not None:
                out.append("④ 之后：" + ("有人接了" if float(sc) >= 0.6 else "接的人不多" if float(sc) >= 0.2 else "没人接"))
        else:
            out.append("③ 进了她这一步，她自己想了想选了不说（或者写好的话被检查拦下了）")
    elif not judge or judge != "say":
        out.append("→ 所以她没开口")
    return out


FEAT_WORDS = {
    "question": ("有人在问", 0.5), "self_share": ("她刚在聊", 0.3), "attention": ("说话的人刚和她聊过", 0.3),
    "interest": ("聊到她感兴趣的", 0.5), "waited": ("她在等的人开口了", 0.5), "owner": ("主人在说话", 0.5),
    "at_other": ("@了别人", 0.5), "reply_other": ("在回复别人", 0.5), "emoji_only": ("只是表情 / 图", 0.5),
    "busy": ("她在忙", 0.5), "tired": ("她今天说多了", 0.4), "ignored": ("她前面几句没人理", 0.4),
    "cold": ("群里冷了很久", 0.5),
}


def feat_words(feats: dict) -> str:
    out = []
    for k, (w, th) in FEAT_WORDS.items():
        try:
            if float(feats.get(k, 0) or 0) >= th:
                out.append(w)
        except (TypeError, ValueError):
            continue
    try:
        if float(feats.get("n5m", 0) or 0) >= 15:
            out.append("群里正热闹（5 分钟十几条）")
    except (TypeError, ValueError):
        pass
    return "、".join(out[:4])


def _pack(db, r, tz=None) -> dict:
    ctx = list(reversed(db.conn.execute(
        "SELECT ts, name, role, content FROM qq_group_messages WHERE group_id=? AND ts<=? ORDER BY id DESC LIMIT 12",
        (r["group_id"], r["ts"] + 1)).fetchall()))
    lines = [f"[{datetime.fromtimestamp(c['ts'], tz):%m-%d %H:%M}] {'她' if c['role'] == 'assistant' else c['name']}："
             f"{str(c['content'])[:120]}" for c in ctx]
    if r["spoke"] and r["her_text"]:
        did = f"她说了：{r['her_text']}"
    elif r["spoke"]:
        did = "她说了一句"
    elif r["passed"]:
        did = "她想了想没说"
    else:
        did = "她没开口"
    if r["spoke"] and r["score"] is not None:
        did += "（有人接）" if r["score"] >= 0.6 else "（没什么人接）"
    return {"id": r["id"], "gid": r["group_id"], "context": lines, "did": did, "spoke": bool(r["spoke"]),
            "p": round(float(r["p"]), 2), "thr": round(float(r["threshold"]), 2), "steps": steps(r),
            "act": r["act"] if "act" in r.keys() else ""}


def next_moment(db, gid: int | None, tz=None, days: int = 14) -> dict | None:
    rows = _rows(db, gid, 1, days, set())
    return _pack(db, rows[0], tz) if rows else None


def batch(db, gid: int | None, tz=None, n: int = 10, days: int = 14) -> list[dict]:
    return [_pack(db, r, tz) for r in _rows(db, gid, n, days, set())]


def answer(db, gate_id: int, label: int | None, how: str = ""):
    """label：1 该接、2 可接可不接、0 不该插、None 看不出来（标成 -1，不再出现，也不当标签用）。how：该怎么接（可选）。"""
    if label is None:
        v = -1
    else:
        v = int(label)
        v = v if v in (0, 1, 2) else (1 if v else 0)
    how = how if how in HOW and v == 1 else ""
    db.conn.execute("UPDATE qq_gate_log SET human=?, human_act=? WHERE id=?", (v, how, int(gate_id)))
    db.conn.commit()


def weak_labels(db, days: int = 14) -> int:
    """20.0 没人标的时刻，程序自己推一个弱标签（只给小判断器凑数，权重低）：
    她主动说了、10 分钟里没人接（效果分 < 0.2）→ 0；说了、接得很好（≥ 0.8）→ 1。"""
    since = time.time() - days * 86400
    cur = db.conn.execute(
        "UPDATE qq_gate_log SET weak=(SELECT CASE WHEN e.score<0.2 THEN 0 WHEN e.score>=0.8 THEN 1 END "
        "FROM qq_reply_effect e WHERE e.gate_id=qq_gate_log.id AND e.done=1) "
        "WHERE mentioned=0 AND spoke=1 AND human IS NULL AND weak IS NULL AND ts>? "
        "AND EXISTS (SELECT 1 FROM qq_reply_effect e WHERE e.gate_id=qq_gate_log.id AND e.done=1 AND (e.score<0.2 OR e.score>=0.8))",
        (since,))
    db.conn.commit()
    return cur.rowcount or 0


def stats(db) -> dict:
    """标了多少；她做的和你判的一致的比例；分开看“该说的她说了”（召回）和“她说的是该说的”（精确）。“可接可不接”不算进去。"""
    rows = db.conn.execute("SELECT spoke, human FROM qq_gate_log WHERE human IN (0, 1)").fetchall()
    n = len(rows)
    tp = sum(1 for r in rows if r["spoke"] and r["human"] == 1)
    fp = sum(1 for r in rows if r["spoke"] and r["human"] == 0)
    fn = sum(1 for r in rows if not r["spoke"] and r["human"] == 1)
    agree = sum(1 for r in rows if bool(r["spoke"]) == bool(r["human"]))
    try:
        n2 = int(db.conn.execute("SELECT COUNT(*) c FROM qq_gate_log WHERE human=2").fetchone()["c"])
        w = db.conn.execute("SELECT SUM(weak=0) w0, SUM(weak=1) w1 FROM qq_gate_log WHERE human IS NULL").fetchone()
        w0, w1 = int(w["w0"] or 0), int(w["w1"] or 0)
    except Exception:  # noqa: BLE001
        n2 = w0 = w1 = 0
    return {"n": n, "n1": sum(1 for r in rows if r["human"] == 1), "n0": sum(1 for r in rows if r["human"] == 0),
            "n2": n2, "w0": w0, "w1": w1,
            "agree": round(agree / n, 3) if n else None,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None}
