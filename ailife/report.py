"""真实聊天的质量周报：用你们这一周真实的聊天算指标，和上周比，变差了一眼能看出来。

    重复率      她的话里有多少条和别的几乎一样
    开头重复    同一个开头用了好几次
    称呼超额    “哥哥大人”这类正式称呼出现的比例
    出戏        说自己是 AI / 程序
    格式问题    markdown、括号动作、整句引号
    每次回复几条 / 平均每条字数
    主动找你    一共几次、你回了几次
    你点了 👎    几次（她会避开那些说法）
    大模型      调用次数、限流次数、“等我一下”兜底了几次
"""
from __future__ import annotations

import time
from datetime import datetime

from .evaluate import _metrics

LOWER_BETTER = {"重复率%", "开头重复", "称呼超额%", "出戏", "格式问题", "你点了👎", "限流次数", "网络兜底"}
HIGHER_BETTER = {"你回她的比例%"}


def _rows(db, t0: float, t1: float, brain=None) -> list[dict]:
    """把聊天记录分成一轮一轮：他说完后她的那几条算一次回复，她主动发的连着几条算一次主动消息。"""
    rows, cur = [], None
    holds = _hold_lines(brain)
    for m in db.messages_between(t0, t1):
        if m["role"] == "user":
            cur = None
            continue
        if m["role"] != "assistant" or m["kind"] not in ("text", "proactive", "voice"):
            continue
        kind = "proactive" if m["kind"] == "proactive" else "reply"
        text = m["content"].removeprefix("[语音] ").strip()
        if not text or text.startswith("[") or text in holds:
            continue
        if cur is None or cur["kind"] != kind:
            cur = {"kind": kind, "bubbles": [], "tags": []}
            rows.append(cur)
        cur["bubbles"].append(text)
    return rows


def _own_db(brain):
    """在后台线程里算的时候用自己的数据库连接（WAL 模式下可以和主程序同时读）。"""
    from .db import DB
    return DB(brain.db.path)


def week_metrics(brain, end: float | None = None, days: int = 7, db=None) -> dict:
    db = db or brain.db
    end = end or time.time()
    t0 = end - days * 86400
    rows = _rows(db, t0, end, brain)[-150:]                         # 只看最近 150 轮（重复率是两两比较，太多会很慢）
    m = _metrics(rows, brain.quality.formal) if rows else {}
    m.pop("乱打标签", None)                                   # 真实聊天里不统计标签
    span = int((time.time() - t0) / 86400) + 2               # 往前看到 t0 为止（上周也要整周的数据）
    pro = [r for r in db.proactive_history(span) if t0 <= r["ts"] < end]
    replied = sum(1 for r in pro if r["replied_ts"])
    m["主动找你"] = len(pro)
    if pro:
        m["你回她的比例%"] = round(100 * replied / len(pro))
    m["你发的消息"] = sum(1 for r in db.messages_between(t0, end) if r["role"] == "user")
    m["你点了👎"] = sum(1 for d in (db.get("disliked") or []) if t0 <= d.get("ts", 0) < end)
    calls = limited = holds = 0
    for i in range(days):
        day = datetime.fromtimestamp(end - i * 86400, brain.cfg.tz).strftime("%Y-%m-%d")      # 和 llm.py 一样按配置时区
        st = db.get(f"llm_stats_{day}") or {}
        calls += st.get("calls", 0)
        limited += st.get("limited", 0)
    holds = sum(1 for r in db.messages_between(t0, end) if r["role"] == "assistant"
                and r["content"] in _hold_lines(brain))
    m["大模型调用"] = calls
    m["限流次数"] = limited
    m["网络兜底"] = holds
    return m


def _hold_lines(brain=None):
    from .bot_common import HOLD_LINES
    extra = getattr(getattr(brain, "persona", None), "hold_lines", None) or []
    return set(HOLD_LINES) | set(extra)


def trend(k: str, v, pv) -> str:
    if not isinstance(v, (int, float)) or not isinstance(pv, (int, float)) or v == pv:
        return ""
    if k in LOWER_BETTER:
        return "↓好了" if v < pv else "↑变差"
    if k in HIGHER_BETTER:
        return "↑好了" if v > pv else "↓变差"
    return "↑" if v > pv else "↓"


def weekly_report(brain, save: bool = True, own_db: bool = False) -> tuple[str, dict, dict]:
    now = time.time()
    db = _own_db(brain) if own_db else brain.db
    try:
        cur = week_metrics(brain, now, db=db)
        prev = week_metrics(brain, now - 7 * 86400, db=db)
        if save:
            wk = datetime.fromtimestamp(now, brain.cfg.tz).strftime("%G-W%V")
            db.set(f"quality_week_{wk}", {"ts": now, "metrics": cur})
    finally:
        if own_db:
            db.conn.close()
    if not cur.get("你发的消息") and not cur.get("主动找你"):
        return "这一周还没怎么聊天，没什么可统计的。", cur, prev
    keys = ["重复率%", "开头重复", "称呼超额%", "出戏", "格式问题", "每次回复几条", "平均每条字数",
            "主动找你", "你回她的比例%", "你发的消息", "你点了👎", "大模型调用", "限流次数", "网络兜底"]
    lines = []
    for k in keys:
        if k not in cur:
            continue
        pv = prev.get(k)
        t = trend(k, cur[k], pv)
        lines.append(f"{k}：{cur[k]}" + (f"（上周 {pv} {t}）" if pv is not None and t else ""))
    worse = [k for k in keys if trend(k, cur.get(k), prev.get(k)).endswith("变差")]
    head = "📊 这一周她的表现（只统计，不是她在说话）\n"
    tail = ""
    if any(cur.get(k, 0) for k in ("出戏", "格式问题")) or cur.get("重复率%", 0) >= 5:
        tail = "\n\n有点走样：可以在 人设文件的“对话示例”里多写几段她该怎么说，或者跑 python run.py eval 看看。"
    elif worse:
        tail = f"\n\n比上周变差的：{'、'.join(worse)}"
    return head + "\n".join(lines) + tail, cur, prev


_cache: dict = {}


def report_for_web(brain, own_db: bool = False) -> dict:
    """小屋网页每分钟刷新一次：结果缓存 30 分钟，免得每次都重算。"""
    hit = _cache.get(id(brain))
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    db = _own_db(brain) if own_db else brain.db
    try:
        cur = week_metrics(brain, db=db)
        prev = week_metrics(brain, time.time() - 7 * 86400, db=db)
    finally:
        if own_db:
            db.conn.close()
    out = {"metrics": [{"k": k, "v": v, "prev": prev.get(k), "trend": trend(k, v, prev.get(k))} for k, v in cur.items()]}
    _cache[id(brain)] = (time.time(), out)
    return out
