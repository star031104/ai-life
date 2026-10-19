"""有状态的小游戏：输赢由程序判定（她没法耍赖），她只负责在开局和结束时有反应。

    井字棋      你是 ✕，她是 ○；她大多数时候认真下，偶尔失误
    石头剪刀布   她先在心里决定好，你再出
    猜数字      她想一个 1~100 的数，你直接在聊天里发数字猜
"""
from __future__ import annotations

import random
import secrets
import time

LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
RPS = {"r": "✊ 石头", "s": "✌️ 剪刀", "p": "✋ 布"}
BEATS = {"r": "s", "s": "p", "p": "r"}


def winner(b: list[str]) -> str | None:
    for a, c, d in LINES:
        if b[a] and b[a] == b[c] == b[d]:
            return b[a]
    return "draw" if all(b) else None


def minimax(b: list[str], me: str, turn: str) -> tuple[int, int | None]:
    w = winner(b)
    if w == me:
        return 1, None
    if w == "draw":
        return 0, None
    if w:
        return -1, None
    best = (-2, None) if turn == me else (2, None)
    for i in range(9):
        if not b[i]:
            b[i] = turn
            s, _ = minimax(b, me, "X" if turn == "O" else "O")
            b[i] = ""
            if (turn == me and s > best[0]) or (turn != me and s < best[0]):
                best = (s, i)
    return best


class Games:
    def __init__(self, db, mistake_rate: float = 0.25):
        self.db = db
        self.mistake = mistake_rate

    @property
    def state(self) -> dict | None:
        return self.db.get("game")

    def _save(self, st: dict | None):
        self.db.set("game", st)

    # ---------- 开局 ----------
    def start(self, kind: str) -> tuple[str, list | None]:
        gid, ts = secrets.token_hex(3), time.time()
        if kind == "ttt":
            st = {"kind": "ttt", "board": [""] * 9, "gid": gid, "ts": ts}
            self._save(st)
            return "井字棋开局！你是 ✕ 先手，点格子落子。", self.ttt_keyboard(st["board"], gid)
        if kind == "rps":
            st = {"kind": "rps", "her": random.choice("rsp"), "score": [0, 0], "round": 1, "gid": gid, "ts": ts}
            self._save(st)
            return "石头剪刀布，三局两胜！她已经想好出什么了～", self.rps_keyboard(gid)
        if kind == "guess":
            st = {"kind": "guess", "n": random.randint(1, 100), "tries": 0, "gid": gid, "ts": ts}
            self._save(st)
            return "她心里想好了一个 1~100 的数，直接在聊天里发数字猜吧（最多 7 次）。", None
        return "", None

    def stop(self):
        self._save(None)

    # ---------- 键盘 ----------
    @staticmethod
    def ttt_keyboard(b: list[str], gid: str = "") -> list:
        sym = {"X": "✕", "O": "○", "": "·"}
        return [[(sym[b[r * 3 + c]], f"g:ttt:{gid}:{r * 3 + c}") for c in range(3)] for r in range(3)]

    @staticmethod
    def rps_keyboard(gid: str = "") -> list:
        return [[(v, f"g:rps:{gid}:{k}") for k, v in RPS.items()]]

    @staticmethod
    def board_text(b: list[str]) -> str:
        sym = {"X": "✕", "O": "○", "": "·"}
        return "\n".join(" ".join(sym[b[r * 3 + c]] for c in range(3)) for r in range(3))

    # ---------- 落子 ----------
    def ttt_move(self, idx: int) -> tuple[str, list | None, str | None]:
        """返回 (提示文字, 新键盘, 结束时给她的事件描述)。"""
        st = self.state
        if not st or st.get("kind") != "ttt":
            return "这局已经结束啦，发 /game 再开一局。", None, None
        b = st["board"]
        if not (0 <= idx < 9) or b[idx]:
            return "这格有子了，换一格～", self.ttt_keyboard(b, st.get("gid", "")), None
        b[idx] = "X"
        w = winner(b)
        if not w:
            empties = [i for i in range(9) if not b[i]]
            if random.random() < self.mistake:
                mv = random.choice(empties)
            else:
                mv = minimax(b, "O", "O")[1]
                mv = mv if mv is not None else random.choice(empties)
            b[mv] = "O"
            w = winner(b)
        self._save(st if not w else None)
        if not w:
            st["ts"] = time.time()
            self._save(st)
            return "轮到你了。", self.ttt_keyboard(b, st.get("gid", "")), None
        board = self.board_text(b)
        if w == "X":
            return "你赢了！", None, f"井字棋结束：他赢了你。最终棋盘：\n{board}"
        if w == "O":
            return "她赢了！", None, f"井字棋结束：你赢了他。最终棋盘：\n{board}"
        return "平局！", None, f"井字棋结束：平局。最终棋盘：\n{board}"

    def rps_play(self, his: str) -> tuple[str, list | None, str | None]:
        st = self.state
        if not st or st.get("kind") != "rps" or his not in RPS:
            return "这局已经结束啦，发 /game 再开一局。", None, None
        her = st["her"]
        if his == her:
            res = "平"
        elif BEATS[his] == her:
            res = "你赢"
            st["score"][0] += 1
        else:
            res = "她赢"
            st["score"][1] += 1
        line = f"第 {st['round']} 局：你 {RPS[his]}，她 {RPS[her]} → {res}（{st['score'][0]}:{st['score'][1]}）"
        if max(st["score"]) >= 2:
            self._save(None)
            who = "他赢了你" if st["score"][0] > st["score"][1] else "你赢了他"
            return line, None, f"石头剪刀布三局两胜结束：{who}，比分 {st['score'][0]}:{st['score'][1]}"
        st["round"] += 1
        st["her"] = random.choice("rsp")
        self._save(st)
        return line + "\n下一局，她又想好了～", self.rps_keyboard(st.get("gid", "")), None

    def guess(self, n: int) -> tuple[str, str | None]:
        st = self.state
        if not st or st.get("kind") != "guess":
            return "", None
        st["tries"] += 1
        if n == st["n"]:
            self._save(None)
            return f"猜中了！就是 {n}（第 {st['tries']} 次）", f"猜数字结束：他第 {st['tries']} 次就猜中了你想的 {n}"
        if st["tries"] >= 7:
            self._save(None)
            return f"7 次用完啦，答案是 {st['n']}", f"猜数字结束：他 7 次都没猜中，答案是 {st['n']}，你赢了"
        st["ts"] = time.time()
        self._save(st)
        return (f"{n}？再{'大' if n < st['n'] else '小'}一点（还剩 {7 - st['tries']} 次）"), None
