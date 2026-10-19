"""测试用的假东西：假的大模型、假的 Telegram，以及测试人设的位置。（不联网）"""
from pathlib import Path
from types import SimpleNamespace


class FakeLLM:
    vision = False

    def __init__(self):
        self.calls = []

    async def chat(self, messages, helper=False, **kw):
        self.calls.append(messages)
        last = messages[-1]["content"] if isinstance(messages[-1]["content"], str) else ""
        if "故事线" in last and "beats" in last:
            from datetime import date, timedelta
            d0 = date.today()
            return ('{"title":"期中考试周","beats":[' + ",".join(
                '{"day":"%s","text":"%s"}' % ((d0 + timedelta(days=i * 2)).isoformat(), t)
                for i, t in enumerate(["开始复习高数", "和小林去图书馆通宵", "哥哥给我打气", "考完了，感觉还行"])) + ']}')
        if "内心独白" in last:
            return '{"thought":"高数好难，好想找他说说话。","mood":"想他","contact":true,"why":"想他了","say":"问他在干嘛"}'
        if "整理一下你对" in last:
            return "近况：在找工作。\n性格和习惯：夜猫子。"
        if "维护一份记忆库" in last:
            import re as _re
            ids = _re.findall(r"#(\d+)", last)
            return '{"ops":[{"i":0,"op":"update","id":%s,"content":"他养的橘猫叫团子，最近在减肥","importance":4}]}' % ids[0]
        if "整理记忆" in last:
            if "团子" in last:
                return '{"facts":[{"about":"他","content":"他的橘猫团子最近在减肥","importance":3}]}'
            return ('{"facts":[{"about":"他","content":"他下周三考六级","importance":5},'
                    '{"about":"他","content":"他养了一只橘猫叫团子","importance":4},'
                    '{"about":"我","content":"小葵的室友叫小林","importance":3}],'
                    '"dates":[{"date":"2026-10-21","name":"六级考试"}],'
                    '"threads_open":["他说面试结果下周出"],"phrases":[{"phrase":"摸鱼大王","meaning":"她笑他上班摸鱼"}]}')
        if helper and "日记" in last:
            return "今天他没怎么说话，我有点想他。"
        if helper:
            return "09:30-18:00 上班"
        if "主动给他发消息" in last:
            return "在干嘛呀|||[表情:想你]"
        return "[引用]这三天你都没动静|||我一直悬着。[心情:松了口气][提醒:08:00|叫他起床]"

    async def json(self, messages, default=None, role="helper"):
        from ailife.llm.client import parse_json
        return parse_json(await self.chat(messages, helper=True), default)


class FakeTGBot:
    def __init__(self):
        self.sent = []
        self._id = 100

    async def _m(self, kind, *a, **k):
        self._id += 1
        self.sent.append((kind, a, k))
        return SimpleNamespace(message_id=self._id)

    async def send_message(self, *a, **k):
        return await self._m("text", *a, **k)

    async def send_sticker(self, *a, **k):
        return await self._m("sticker", *a, **k)

    async def send_voice(self, *a, **k):
        return await self._m("voice", *a, **k)

    async def send_chat_action(self, *a, **k):
        return None

    async def set_message_reaction(self, *a, **k):
        self.sent.append(("reaction", a, k))

    async def delete_message(self, *a, **k):
        self.sent.append(("delete", a, k))

    async def edit_message_text(self, *a, **k):
        self.sent.append(("edit", a, k))


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "student.md"


def cfg_root():
    from ailife.core.config import ROOT
    return ROOT
