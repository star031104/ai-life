"""按聊天内容挑几段人设里的对话示例（few-shot）：小模型最吃示例，但全塞进去又长又容易被照抄。"""
from __future__ import annotations

import random

USER_LABELS = ("他", "她", "你", "TA", "用户", "我")     # 示例里代表“你”这一方的标签

from .memory import similarity


def pick(persona, query: str, last_user: str = "", k: int = 3, avoid: list[str] | None = None) -> list:
    exs = list(getattr(persona, "examples", None) or [])
    if not exs:
        return []
    q = query or ""
    lu = last_user or ""
    scored = []
    for e in exs:
        s = 3.0 * sum(1 for w in e.keywords if w and w in lu) + 1.0 * sum(1 for w in e.keywords if w and w in q)
        his = " ".join(t for who, t in e.turns if who in USER_LABELS)
        if lu and his:
            s += 2.0 * similarity(lu, his)
        if avoid and e.scene in avoid:
            s -= 0.5                      # 上次刚用过的稍微让一让，免得总是同一段
        scored.append((s + random.random() * 0.1, e))
    scored.sort(key=lambda x: -x[0])
    hit = [e for s, e in scored if s >= 1.0][:k]
    # 没有相关的就随机挑两段，保持语气；有相关的也再补一段别的场景，免得她只会一种反应
    rest = [e for _, e in scored if e not in hit]
    random.shuffle(rest)
    need = max(0, min(k, 2 if not hit else len(hit) + 1) - len(hit))
    return hit + rest[:need] if hit else rest[:2]


def block(persona, chosen: list) -> str:
    if not chosen:
        return ""
    body = "\n\n".join(f"【{e.scene}】\n" + "\n".join(f"{who}：{t}" for who, t in e.turns) for e in chosen)
    return ("# 你平时说话的样子（示例：只学语气、节奏和怎么分条，不要照抄里面的句子和事情，"
            "要根据此刻真实的情况说自己的话）\n" + body)


def her_lines(persona, chosen: list) -> list[str]:
    out = []
    for e in chosen:
        for who, t in e.turns:
            if who not in USER_LABELS:
                out += [x.strip() for x in t.split("|||") if x.strip()]
    return out
