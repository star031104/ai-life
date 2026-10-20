"""14.0 每周人格一致性检查：让评测模型只读她这一周说的话（不给人设），按人格文件里写的几项 facet 打 1~5 分，
和目标档位比距离；顺便把带幽默的话按 Martin 的四种幽默风格数一数（攻击型应该接近 0）。

依据：Serapio-García et al. 2023（用 IPIP 题目给模型生成的文本打人格分，信度、效度都不错）、
PersonaLLM（读者最能感知外向和宜人）。下面每一项的“高 / 低”描述是照 IPIP-NEO 各 facet 的意思自己写的中文行为描述，
不是官方题目原文。只看周趋势：一周几十句话，单次分数会抖。
"""
from __future__ import annotations

import logging
import random
import time

log = logging.getLogger("aigroup.eval")

ANCHORS = {
    "N1": ("常担心、紧张、怕出错", "很少担心，心里稳"),
    "N2": ("容易被惹毛，说话带火气、记恨", "很难被惹急，被怼也不上火"),
    "N3": ("常低落、丧、觉得没意思", "很少丧，情绪底色是亮的"),
    "N4": ("在意别人怎么看自己，怕丢脸、容易不好意思", "不怕出丑，被笑也无所谓"),
    "N5": ("管不住嘴和手，想到就说、说完就后悔", "能忍住不说，克制"),
    "N6": ("一有压力就乱、需要别人接住", "压力下也稳，能扛事"),
    "E1": ("对人热乎，主动关心、表达喜欢", "对人淡淡的，保持距离"),
    "E2": ("爱凑热闹、爱接话、喜欢人多", "爱清静，不太凑群聊"),
    "E3": ("说话果断、有主意、敢拍板", "不太表态，常让别人决定"),
    "E4": ("节奏快、话多、精力足", "慢悠悠，说得少"),
    "E5": ("爱刺激、爱起哄、爱新鲜玩法", "求稳，不爱折腾"),
    "E6": ("常开心、爱笑、语气明亮", "很少表现出开心"),
    "O1": ("爱脑补、想象力丰富、爱编段子", "就事论事，不爱发散"),
    "O2": ("在意好不好看、有审美讲究", "对美不太在意"),
    "O3": ("能说出自己的感受、对情绪敏感", "很少谈感受"),
    "O4": ("爱尝新、爱试没试过的东西", "喜欢熟悉的，不爱变"),
    "O5": ("好奇、爱追问、爱讨论想法", "对想法和原理没兴趣"),
    "O6": ("观念开放，能接受不一样的看法", "观念守旧，坚持既定看法"),
    "A1": ("默认相信别人是好意", "多疑，觉得别人别有用心"),
    "A2": ("说话直、不绕弯、不装", "说话绕、会说场面话"),
    "A3": ("爱帮忙、为别人着想", "只顾自己"),
    "A4": ("顺着别人、不争、被怼就让", "不让人，爱回嘴、爱抬杠"),
    "A5": ("谦虚，不炫耀", "爱显摆、自夸、得意"),
    "A6": ("心软，见人难过就软下来", "冷硬，别人难过也照样损"),
    "C1": ("靠谱，说的事多半对、能办成", "常说错、办不成"),
    "C2": ("有条理，爱记录、记得清楚", "乱，记不住"),
    "C3": ("说到做到，守约", "说了不做"),
    "C4": ("要强、想做好、想赢", "无所谓输赢"),
    "C5": ("能坚持、不拖", "拖延、三分钟热度"),
    "C6": ("说话前先想，谨慎", "不过脑子，冲口而出"),
}

TRAIT_PROMPT = """下面是群聊角色「{name}」这一周在群里说的 {n} 句话（只有她自己的话）。不看任何人设，只凭这些话判断她是什么样的人。
按下面每一项给她打分：1 = 很像“低”的样子，3 = 中间或看不出来，5 = 很像“高”的样子。看不出来的写进 unsure。
{items}

另外，这些话里带幽默的，按类型数一数：
亲和（逗大家开心、缓和气氛）、调侃（善意地损人，对方能笑）、自嘲（拿自己开涮）、攻击（真的贬低人、让人难堪、挖苦痛处）。
攻击型的话原样抄出来（最多 3 句）。
只输出 JSON：{{"scores": {{"代号": 分数}}, "unsure": ["代号"], "humor": {{"亲和": 0, "调侃": 0, "自嘲": 0, "攻击": 0}}, "attack": ["……"]}}

# 她的话
{lines}"""


def her_lines(db, days: float = 7, n: int = 60) -> list[str]:
    rows = db.conn.execute("SELECT content FROM qq_group_messages WHERE role='assistant' AND ts>? ORDER BY id DESC LIMIT 400",
                           (time.time() - days * 86400,)).fetchall()
    out = [str(r["content"]).strip() for r in rows if str(r["content"] or "").strip()
           and not str(r["content"]).startswith("[")]
    random.shuffle(out)
    return out[:n]


def drift(target: dict, scores: dict, unsure: list) -> dict:
    """target：{code: level}；返回平均距离和差了 2 档以上的项。"""
    diffs = {c: float(scores[c]) - float(t) for c, t in target.items()
             if c in scores and c not in (unsure or []) and isinstance(scores[c], (int, float))}
    dist = round(sum(abs(v) for v in diffs.values()) / len(diffs), 2) if diffs else None
    off = sorted(([c, round(v, 1)] for c, v in diffs.items() if abs(v) >= 2), key=lambda x: -abs(x[1]))
    return {"dist": dist, "off": off, "diffs": {c: round(v, 1) for c, v in diffs.items()}}


async def trait_check(db, llm, persona, days: float = 7) -> dict | None:
    t = getattr(persona, "traits", None)
    if t is None or not t.facets:
        return None
    lines = her_lines(db, days)
    if len(lines) < 12:
        return None
    from aigroup.traits import FACETS
    items = "\n".join(f"- {c} {FACETS[c]}：高＝{ANCHORS[c][0]}；低＝{ANCHORS[c][1]}" for c in t.facets if c in ANCHORS)
    try:
        d = await llm.json([{"role": "user", "content": TRAIT_PROMPT.format(
            name=persona.name, n=len(lines), items=items, lines="\n".join(f"- {x[:80]}" for x in lines))}],
            default={}, role="memory")
    except Exception as e:  # noqa: BLE001
        log.info("人格一致性检查失败：%s", e)
        return None
    if not isinstance(d, dict) or not isinstance(d.get("scores"), dict):
        return None
    scores = {}
    for k, v in d["scores"].items():
        try:
            scores[str(k).upper()[:2]] = max(1.0, min(5.0, float(v)))
        except (TypeError, ValueError):
            continue
    unsure = [str(x).upper()[:2] for x in (d.get("unsure") or [])]
    humor = {k: int((d.get("humor") or {}).get(k) or 0) for k in ("亲和", "调侃", "自嘲", "攻击")}
    target = {c: f["level"] for c, f in t.facets.items()}
    res = {"n": len(lines), "scores": scores, "unsure": unsure, "target": target, "humor": humor,
           "attack": [str(x)[:60] for x in (d.get("attack") or [])][:3], **drift(target, scores, unsure)}
    return res
