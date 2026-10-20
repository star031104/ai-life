"""测试用的假东西：假的大模型、假的 QQ 发送器，以及测试人设的位置。（不联网）"""
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


class FakeOut:
    """假的 QQ 发送器：和 channels/qq/onebot.py 的 QQOut 方法一样，发出去的都记在 sent 里。
    每条是 (种类, (对方, 内容), 其它参数)。"""
    def __init__(self):
        self.sent = []
        self._id = 100

    def _m(self, kind, *a, **k):
        self._id += 1
        self.sent.append((kind, a, k))
        return SimpleNamespace(message_id=self._id)

    async def send_message(self, chat, text, reply_to_message_id=None, **k):
        return self._m("text", chat, text, reply_to_message_id=reply_to_message_id)

    async def send_photo(self, chat, photo, reply_to_message_id=None, **k):
        return self._m("photo", chat, photo, reply_to_message_id=reply_to_message_id)

    async def send_sticker_image(self, chat, data, reply_to_message_id=None):
        return self._m("sticker", chat, data, reply_to_message_id=reply_to_message_id)

    async def send_face(self, chat, fid, reply_to_message_id=None):
        return self._m("face", chat, fid, reply_to_message_id=reply_to_message_id)

    async def typing(self, chat, speaking=False):
        return None

    async def delete_message(self, chat, message_id):
        self.sent.append(("delete", (chat, message_id), {}))


class FakeQQ:
    """假的 QQ 私聊桥（channels/qq/bridge.py 里 Companion 用到的那几样）。"""
    def __init__(self, bot, owner_id=42):
        self.bot, self.db, self.owner_id = bot, bot.db, owner_id
        self.out = FakeOut()
        self.ob = SimpleNamespace(ready=True, self_id=10001)
        self.stickers = {}

    def private_ready(self):
        return True

    def _user_in(self, content, kind, qq_mid):
        mid = self.db.add_message("user", content, kind, qq_mid, channel="qq")
        self.bot._last_user_msg_id = qq_mid
        self.bot._user_active()
        return mid

    def pick_sticker(self, emotion):
        return self.stickers.get(emotion)

    async def stop(self):
        return None


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "student.md"


def cfg_root():
    from ailife.core.config import ROOT
    return ROOT
