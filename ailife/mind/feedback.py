"""他对她说的话的反馈：表情回应分正面 / 负面 / 心疼。"""
from __future__ import annotations

POS = set("👍 ❤ ❤️ 🔥 🥰 👏 😁 🤩 🎉 😍 💯 🤣 🏆 😘 💘 🤗 😇 🙏 👌 ✍ 🫡 😎 🍓 💋 ⚡ 🆒 🤭 😆 😂 🥳".split())
NEG = set("👎 💩 🤮 🤡 🥱 😐 🤨 🙄 😒 😡 🤬 🗿 💔".split()) - {"💔"}
SAD = set("😢 😭 💔 🥺 😨 😱".split())


def classify_reaction(emojis: list[str]) -> str:
    """'pos' / 'neg' / 'sad'。多个表情时负面优先（他特意点了不喜欢）。"""
    es = [e.replace("️", "") for e in emojis]
    if any(e in NEG for e in es):
        return "neg"
    if any(e in SAD for e in es):
        return "sad"
    return "pos"
