"""每种“活”用哪些模型：路由（routes）里按用途列模型名，前一个出错就换下一个。

没配的用途按 FALLBACK 往回找（比如没单独配“整理记忆”，就用“其它小活”，再没有就用“聊天”）。
看图、画图、记忆检索没配就是不做（不会拿聊天模型硬顶）。
"""
from __future__ import annotations

import re

ROLES: dict[str, str] = {
    "chat": "聊天、主动消息",
    "helper": "其它小活（打标签、做判断）",
    "memory": "整理记忆",
    "night": "写日记、排日程、周日来信",
    "vision": "看你发的图",
    "image": "画图（她的照片）",
    "image_ref": "画她自己（带 data/avatar/ 里的参考图）",
    "embed": "记忆检索（文字转向量）",
}
TEXT_ROLES = ("chat", "helper", "memory", "night")
BACKGROUND_ROLES = ("helper", "memory", "night")
FALLBACK: dict[str, tuple[str, ...]] = {
    "helper": ("chat",),
    "memory": ("helper", "chat"),
    "night": ("helper", "chat"),
}


def chain(routes: dict, role: str) -> list[str]:
    """这个用途实际按顺序用哪些模型（考虑 FALLBACK）。"""
    for r in (role, *FALLBACK.get(role, ())):
        names = [n for n in (routes.get(r) or []) if n]
        if names:
            return names
    return []


_EMBED = re.compile(r"embed|bge-|e5-|gte-|m3e|text-embedding", re.I)
_IMAGE = re.compile(r"cogview|flux|sdxl|stable-?diffusion|dall-?e|kolors|image|seedream|wanx|hunyuan-?image", re.I)
_VISION = re.compile(r"vision|-vl|vl-|[-_]v$|\dv-|4v|omni|gpt-4o|gemini|claude", re.I)
_NOT_CHAT = re.compile(r"rerank|tts|whisper|speech|audio|asr|moderation|video", re.I)


def guess_kind(model: str) -> str:
    """从模型名猜它是干什么的：chat / vision / image / embed / other。"""
    m = model or ""
    if _EMBED.search(m):
        return "embed"
    if _IMAGE.search(m):
        return "image"
    if _NOT_CHAT.search(m):
        return "other"
    if _VISION.search(m):
        return "vision"
    return "chat"


def default_roles(model: str) -> list[str]:
    """新加一个模型时，默认勾上哪些用途。"""
    return {"embed": ["embed"], "image": ["image"], "vision": ["chat", "vision"], "other": []}.get(
        guess_kind(model), ["chat"])
