"""一条命令（他在私聊里发的 /status、/habit 3……）：和渠道无关。

命令处理函数只看这几样：完整文字、参数、消息编号，回一句话用 reply()。
QQ 那边把收到的“/命令”包成 Command 交给 Companion 的 cmd_xxx；以后接别的渠道也一样。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass
class Command:
    text: str                                       # 完整的一句，比如“/habit add 早睡 23:30”
    reply_fn: Callable[[str], Awaitable] | None = None
    message_id: int | None = None
    args: list[str] = field(default_factory=list)   # 命令名后面的部分，按空格切开

    @classmethod
    def parse(cls, text: str, reply_fn=None, message_id=None) -> tuple[str, "Command"]:
        """“/打卡 3” → ("打卡", Command(args=["3"]))。名字去掉开头的 / 和 @后缀，转小写。"""
        parts = (text or "").strip().lstrip("/／").split()
        name = parts[0].split("@")[0].lower() if parts else ""
        return name, cls(text=text or "", reply_fn=reply_fn, message_id=message_id, args=parts[1:])

    @property
    def rest(self) -> str:
        """命令名后面的原文（保留里面的空格和换行）。"""
        return self.text.partition(" ")[2].strip()

    async def reply(self, text: str):
        if self.reply_fn is None:
            return None
        from ailife.chat.common import ui
        return await self.reply_fn(ui(text))
