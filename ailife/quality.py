"""说话质量：不重复自己、主动消息别太长、称呼和口头禅别用太满。

真实聊天里发现的问题：同一天三次主动找他都在说同一件事；好几条都用同一个开头；
人设里“哥哥大人”只在装正经时用，实际几乎每条都在用；一次主动消息发了四条加一张照片。

做法：
1. 生成前：把她最近说过的话、用过的开头、今天已经分享过的事列进提示词，明确不许再用。
2. 生成后检查：和最近的话太像、开头重复、称呼超额、主动消息太长 → 带着具体问题让模型重写一次；
   还不行就删掉有问题的那几条（至少留一条），再按上限截断。
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter

from .memory import similarity

log = logging.getLogger("ailife.quality")

DEFAULT_PHRASES: list[str] = []          # 口头禅写在人设的 catchphrases 里（或 config 的 quality.catchphrases）


def opener(text: str, n: int = 5) -> str:
    t = re.sub(r"^[\s，。！？…~、“”\"'（）()\[\]【】]+", "", text or "")
    return t[:n]


class Quality:
    def __init__(self, cfg, db, persona):
        c = dict(cfg.get("quality") or {})
        self.enabled = bool(c.get("enabled", True))
        self.sim_threshold = float(c.get("similarity", 0.62))
        self._cfg_phrases = list(c.get("catchphrases") or DEFAULT_PHRASES)
        self.db, self.persona = db, persona
        self.tz = cfg.get("tz")
        self.persona = persona
        self.ref_lines: list[str] = []          # 这次提示词里给的示例句子（不许照抄）

    # ---------------- 最近说过的话 ----------------
    def recent_her(self, hours: float = 48, n: int = 40) -> list[str]:
        rows = self.db.conn.execute(
            "SELECT content FROM messages WHERE role='assistant' AND kind IN ('text','proactive','voice','desk') "
            "AND ts>=? ORDER BY id DESC LIMIT ?", (time.time() - hours * 3600, n)).fetchall()
        out = []
        for r in rows:
            t = re.sub(r"^\[语音\]\s*", "", r["content"]).strip()
            if t and not t.startswith("["):
                out.append(t)
        return out

    # ---------------- 他不喜欢的说法（点了 👎 之类） ----------------
    def disliked(self, days: float = 45) -> list[dict]:
        return [d for d in (self.db.get("disliked") or []) if time.time() - d.get("ts", 0) < days * 86400]

    def dislike(self, text: str, emoji: str = ""):
        t = re.sub(r"^\[语音\]\s*", "", text or "").strip()
        if not t or t.startswith("["):
            return
        items = [d for d in self.disliked() if d.get("text") != t[:80]]
        items.append({"text": t[:80], "emoji": emoji, "ts": time.time()})
        self.db.set("disliked", items[-30:])

    def shared_today(self) -> list[str]:
        return list(self.db.get(f"shared_{self._day()}") or [])

    def remember_shared(self, text: str):
        key = f"shared_{self._day()}"
        items = list(self.db.get(key) or [])
        items.append(text[:60])
        self.db.set(key, items[-12:])

    # 跟着人设走（人设热更新后马上生效）
    @property
    def phrases(self) -> list[str]:
        return list(getattr(self.persona, "catchphrases", None) or self._cfg_phrases)

    @property
    def formal(self) -> str:
        p = self.persona
        f = getattr(p, "formal_name", "") or ""
        if not f and p.call_name and p.call_name != "你" and (p.call_name + "大人") in p.body:
            f = p.call_name + "大人"
        return f

    def _day(self) -> str:
        from datetime import datetime
        return datetime.now(self.tz).strftime("%Y-%m-%d")

    def block(self) -> str:
        """提示词里的“别重复”区块。"""
        if not self.enabled:
            return ""
        recent = self.recent_her()
        if not recent and not self.disliked():
            return ""
        lines = []
        last = recent[:8]
        if last:
            lines.append("你最近已经说过这些（别再说同样的内容，也别换个说法再说一遍）：" + " / ".join(f"“{t[:30]}”" for t in last))
        ops = Counter(opener(t, 4) for t in recent[:20] if len(t) >= 4)
        used = [o for o, c in ops.most_common(6) if c >= 1 and o]
        if used:
            lines.append("最近用过的开头（这次每条都换个开头）：" + "、".join(f"“{o}…”" for o in used))
        if self.formal:
            n_formal = sum(self.formal in t for t in recent[:10])
            if n_formal >= 1:
                lines.append(f"“{self.formal}”最近已经用了 {n_formal} 次：这次别用，平时就叫“{self.persona.call_name}”，"
                             f"或者干脆不叫（只有装正经、闹别扭时才偶尔用“{self.formal}”）")
        over = [p for p in self.phrases if sum(p in t for t in recent[:10]) >= 2]
        if over:
            lines.append("这些口头禅最近用太多了，这次别用：" + "、".join(f"“{p}”" for p in over))
        bad = self.disliked()
        if bad:
            lines.append("他不喜欢你这样说（点过 👎 之类的，别再用类似的话和语气）：" +
                         " / ".join(f"“{d['text'][:30]}”" for d in bad[-5:]))
        shared = self.shared_today()
        if shared:
            lines.append("今天已经跟他分享过的事（别再提）：" + "；".join(shared[-6:]))
        return "# 别重复自己（很重要）\n" + "\n".join(f"- {x}" for x in lines)

    # ---------------- 检查 ----------------
    def problems(self, texts: list[str], proactive: bool = False, max_bubbles: int = 4) -> list[str]:
        if not self.enabled or not texts:
            return []
        recent = self.recent_her()
        out = []
        bad = self.disliked()
        for t in texts:
            if len(t) < 6:
                continue
            hit = next((d for d in bad if similarity(t, d["text"]) >= self.sim_threshold), None)
            if hit:
                out.append(f"“{t[:24]}”很像他不喜欢的那句“{hit['text'][:24]}”，换一种说法和语气")
                continue
            old = next((o for o in recent[:15] if similarity(t, o) >= self.sim_threshold), None)
            if old:
                out.append(f"“{t[:24]}”和你之前说过的“{old[:24]}”几乎一样")
                continue
            if any(similarity(t, ex) >= self.sim_threshold for ex in self.ref_lines):
                out.append(f"“{t[:24]}”照抄了说话示例，要说你自己此刻的话")
        if proactive:                           # 开头重复只管主动消息（回复里“哥哥，你…”很自然）
            ops = Counter(opener(t, 4) for t in recent[:12] if len(t) >= 4)
            last_op = opener(recent[0], 4) if recent else ""
            for t in texts:
                o = opener(t, 4)
                if len(o) == 4 and (ops.get(o, 0) >= 2 or o == last_op):
                    out.append(f"“{t[:16]}”的开头“{o}”最近已经用过")
        if self.formal:
            recent_formal = sum(self.formal in t for t in recent[:6])
            now_formal = sum(self.formal in t for t in texts)
            if now_formal and (recent_formal >= 1 or now_formal >= 2):
                out.append(f"“{self.formal}”用太多了，这次改叫“{self.persona.call_name}”或者不叫")
        for p in self.phrases:
            if sum(p in t for t in recent[:10]) >= 2 and any(p in t for t in texts):
                out.append(f"口头禅“{p}”最近用太多了")
        if len(texts) > max_bubbles:
            out.append(f"太长了，{'主动找他时' if proactive else ''}最多 {max_bubbles} 条")
        return list(dict.fromkeys(out))

    def dup_flags(self, texts: list[str]) -> list[bool]:
        refs = self.recent_her()[:15] + self.ref_lines + [d["text"] for d in self.disliked()]
        return [len(t) >= 6 and any(similarity(t, o) >= self.sim_threshold for o in refs) for t in texts]

    def prune(self, p, proactive: bool = False, max_bubbles: int = 4):
        """重写后还有问题：删掉和最近重复的气泡（至少留一条），再按上限截断。"""
        recent = self.recent_her()
        refs = recent[:15] + self.ref_lines + [d["text"] for d in self.disliked()]
        kept_text = 0
        new = []
        for b in p.bubbles:
            if b.text:
                dup = len(b.text) >= 6 and any(similarity(b.text, o) >= self.sim_threshold for o in refs)
                if dup and any(x.text for x in p.bubbles if x is not b) and kept_text + 1 <= max_bubbles:
                    continue
                if kept_text >= max_bubbles:
                    continue
                if self.formal and self.formal in b.text and any(self.formal in t for t in recent[:6]):
                    b.text = b.text.replace(self.formal, self.persona.call_name)
                kept_text += 1
            new.append(b)
        if not any(b.text for b in new) and p.bubbles:
            first = next((b for b in p.bubbles if b.text), None)
            if first:
                new.insert(0, first)
        if proactive:                                # 主动消息：最多一张图 / 一个表情
            seen_media = 0
            pruned = []
            for b in new:
                if b.photo or b.sticker:
                    seen_media += 1
                    if seen_media > 1:
                        continue
                pruned.append(b)
            new = pruned
        p.bubbles = new
        return p
