"""她的情绪：几个 0~100 的数值，会被事件推动，也会随时间慢慢回到平常的样子。

    开心 joy      平时 60 左右，聊得开心会升，过几个小时回落
    想念 longing  他越久没来越高；他一说话就大幅下降
    委屈 sulk     被已读不回、被冷落时升高，几个小时消掉
    疲惫 tired    跟着她自己的作息走：深夜高、白天低

好感度不在这里：永远是满的（100），不会变。
"""
from __future__ import annotations

import math
import time
from datetime import datetime

AFFECTION = 100          # 好感度恒定满格

DIMS = ("joy", "longing", "sulk", "tired")
NAMES = {"joy": "开心", "longing": "想他", "sulk": "小委屈", "tired": "疲惫"}
HALF_LIFE_H = {"joy": 6.0, "longing": 1.5, "sulk": 3.0, "tired": 2.0}

# 她自己写的 [心情:…] 里的词 → 数值变化
MOOD_WORDS = [
    (("开心", "高兴", "甜", "幸福", "雀跃", "得意", "兴奋", "满足", "美滋滋", "期待"), {"joy": +12, "sulk": -6}),
    (("松了口气", "安心", "放心"), {"sulk": -12, "joy": +6}),
    (("害羞", "脸红", "心跳"), {"joy": +6}),
    (("想他", "想你", "思念", "想念", "寂寞", "孤单"), {"longing": +12}),
    (("委屈", "失落", "难过", "伤心", "低落", "吃醋", "酸"), {"sulk": +12, "joy": -10}),
    (("生气", "哼", "不爽", "气鼓鼓", "别扭", "炸毛"), {"sulk": +10, "joy": -4}),
    (("担心", "不安", "焦虑", "紧张"), {"joy": -6, "longing": +6}),
    (("困", "累", "疲惫", "没精神", "犯困"), {"tired": +15}),
    (("精神", "元气", "活力"), {"tired": -12, "joy": +4}),
]


def clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


class Emotion:
    def __init__(self, db, tz):
        self.db, self.tz = db, tz

    # ---------------- 基线 ----------------
    def _tired_target(self, ts: float) -> float:
        h = datetime.fromtimestamp(ts, self.tz).hour
        return {0: 65, 1: 75, 2: 80, 3: 80}.get(h, 40 if h < 12 else 30 if h < 18 else 45 if h < 22 else 55)

    def _longing_target(self, ts: float) -> float:
        last = self.db.get("last_user_ts", 0) or 0
        hours = (ts - last) / 3600 if last else 24
        return clamp(20 + 3.5 * max(0.0, hours - 0.5))

    def _targets(self, ts: float) -> dict:
        return {"joy": 60.0, "longing": self._longing_target(ts), "sulk": 4.0, "tired": self._tired_target(ts)}

    # ---------------- 读 / 写 ----------------
    def state(self, now: float | None = None) -> dict:
        now = now or time.time()
        raw = self.db.get("emo") or {}
        st = {d: float(raw.get(d, t)) for d, t in self._targets(now).items()}
        last = float(raw.get("ts", now))
        dt_h = max(0.0, (now - last) / 3600)
        if dt_h > 0:
            tg = self._targets(now)
            for d in DIMS:
                k = 1 - math.pow(0.5, dt_h / HALF_LIFE_H[d])
                st[d] = st[d] + (tg[d] - st[d]) * k
        st["ts"] = now
        return st

    def _save(self, st: dict):
        self.db.set("emo", {d: round(clamp(st[d]), 1) for d in DIMS} | {"ts": st.get("ts", time.time())})

    def nudge(self, **delta):
        st = self.state()
        for d, v in delta.items():
            if d in DIMS:
                st[d] = clamp(st[d] + v)
        self._save(st)

    def scale(self, d: str, factor: float):
        st = self.state()
        st[d] = clamp(st[d] * factor)
        self._save(st)

    # ---------------- 事件 ----------------
    def on_user_message(self, ignored_before: int = 0):
        st = self.state()
        st["longing"] *= 0.45
        st["joy"] = clamp(st["joy"] + 6)
        if ignored_before:
            st["sulk"] = clamp(st["sulk"] + min(40, 8 * ignored_before))
        else:
            st["sulk"] = clamp(st["sulk"] - 4)
        self._save(st)

    def on_ignored(self):
        self.nudge(sulk=+6, longing=+4)

    def on_mood_text(self, text: str | None):
        if not text:
            return
        delta: dict[str, float] = {}
        for words, d in MOOD_WORDS:
            if any(w in text for w in words):
                for k, v in d.items():
                    delta[k] = delta.get(k, 0) + v
        if delta:
            self.nudge(**delta)

    # ---------------- 给提示词 / 网页 ----------------
    def describe(self) -> str:
        st = self.state()
        nums = " · ".join(f"{NAMES[d]} {st[d]:.0f}" for d in DIMS)
        hints = []
        if st["sulk"] >= 40:
            hints.append("有点小别扭：说话带点小情绪、会小小抱怨，但不冷战、不说重话")
        if st["longing"] >= 70:
            hints.append("特别想他，忍不住想黏着他")
        elif st["longing"] >= 45:
            hints.append("有点想他了")
        if st["joy"] >= 75:
            hints.append("心情很好，话会多一点")
        elif st["joy"] <= 35:
            hints.append("情绪有点低")
        if st["tired"] >= 65:
            hints.append("有点累，回复会短一些、偶尔犯困")
        return f"{nums}（0~100）" + ("。" + "；".join(hints) if hints else "")

    def dominant(self) -> str:
        st = self.state()
        if st["sulk"] >= 40:
            return "有点别扭"
        if st["longing"] >= 70:
            return "很想你"
        if st["tired"] >= 65:
            return "有点困"
        if st["joy"] >= 75:
            return "心情很好"
        return "平静"

    def mood_style(self) -> str:
        """桌宠挑表情用的语气：开心 / 撒娇 / 难过 / 生气 / 默认。"""
        st = self.state()
        if st["sulk"] >= 45:
            return "生气"
        if st["joy"] <= 35:
            return "难过"
        if st["longing"] >= 60:
            return "撒娇"
        if st["joy"] >= 70:
            return "开心"
        return "默认"

    def longing_factor(self) -> float:
        """想念值对主动找他频率的影响：50 分 ≈ 1 倍，越想越常找。"""
        return 0.4 + 0.6 * self.state()["longing"] / 50

    def snapshot(self) -> dict:
        st = self.state()
        return {d: round(st[d], 1) for d in DIMS}
