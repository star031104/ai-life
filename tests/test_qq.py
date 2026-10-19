"""QQ 接入：纯函数 + 用假的 NapCat（本机 WebSocket）跑一遍私聊和群聊（不联网）。"""
import asyncio
import json
import time


from test_flow import FakeLLM, env  # noqa: F401  （env 是 fixture）

from ailife.qq import flatten
from ailife.qq_group import DEFAULTS, decide, parse_group_reply, to_segments

SELF, OWNER, FRIEND, GROUP = 10001, 55555, 88888, 777777


# ---------------- 纯函数 ----------------
def test_flatten_segments():
    msg = [{"type": "reply", "data": {"id": "321"}},
           {"type": "at", "data": {"qq": str(SELF)}},
           {"type": "text", "data": {"text": " 看这个 "}},
           {"type": "at", "data": {"qq": "88888"}},
           {"type": "image", "data": {"url": "https://x/1.jpg"}},
           {"type": "text", "data": {"text": "[CQ:at,qq=all]"}}]
    f = flatten(msg, SELF, {"88888": "小王"})
    assert f.mentioned and f.reply_id == 321
    assert f.images == ["https://x/1.jpg"]
    assert "@小王" in f.text and "[图片]" in f.text
    assert "[CQ:at,qq=all]" in f.text            # 只是文字
    assert flatten("纯文字[CQ:face,id=1]", SELF).text == "纯文字[CQ:face,id=1]"


def test_decide():
    g = dict(DEFAULTS)
    now = time.time()
    kw = dict(now=now, last_reply_ts=0, prev_msg_ts=now - 30, replies_10min=0, quiet=False, g=g)
    assert decide(mentioned=True, owner_spoke=False, rand=0.99, **kw)[0]
    assert decide(mentioned=True, owner_spoke=False, rand=0.99, **{**kw, "quiet": True})[0]   # 半夜叫她也回
    assert not decide(mentioned=True, owner_spoke=False, rand=0, **{**kw, "replies_10min": 12})[0]
    assert not decide(mentioned=False, owner_spoke=False, rand=0.5, **kw)[0]
    assert decide(mentioned=False, owner_spoke=True, rand=0.2, **kw)[0]             # 0.3
    assert not decide(mentioned=False, owner_spoke=True, rand=0.2, **{**kw, "quiet": True})[0]
    assert not decide(mentioned=False, owner_spoke=True, rand=0, **{**kw, "last_reply_ts": now - 10})[0]   # 冷却
    # 话题热：概率翻倍；冷场后第一句：降低
    assert decide(mentioned=False, owner_spoke=False, rand=0.1, **{**kw, "last_reply_ts": now - 300})[0]
    assert not decide(mentioned=False, owner_spoke=True, rand=0.2, **{**kw, "prev_msg_ts": now - 3600})[0]


def test_parse_and_segments():
    assert parse_group_reply("[跳过]", "小葵", 2) is None
    out = parse_group_reply("小葵：[@小王] 哈哈哈[表情:大笑]|||下次叫我[语音]|||第三条", "小葵", 2)
    assert out[0].startswith("[@小王] 哈哈哈") and "[语音]" not in out[1] and len(out) == 2
    segs = to_segments("[@小王] 你好 [@不认识] [CQ:at,qq=all]", {"小王": 88888})
    assert segs[0] == {"type": "at", "data": {"qq": "88888"}}
    assert all(s["type"] in ("at", "text") for s in segs)
    assert sum(1 for s in segs if s["type"] == "at") == 1
    assert "[CQ:at,qq=all]" in "".join(s["data"].get("text", "") for s in segs)


# ---------------- 假 NapCat ----------------
class FakeNapCat:
    def __init__(self):
        self.actions: list[dict] = []
        self.ws = None
        self.mid = 5000
        self.runner = None
        self.port = 0
        self.friends = [OWNER]

    async def start(self):
        from aiohttp import web

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            self.ws = ws
            async for msg in ws:
                d = json.loads(msg.data)
                self.actions.append(d)
                data = {}
                if d["action"] == "get_login_info":
                    data = {"user_id": SELF, "nickname": "她的小号"}
                elif d["action"] in ("send_private_msg", "send_group_msg"):
                    self.mid += 1
                    data = {"message_id": self.mid}
                elif d["action"] == "get_group_info":
                    data = {"group_name": "摸鱼小分队"}
                elif d["action"] == "get_friend_list":
                    data = [{"user_id": u, "nickname": "x"} for u in self.friends]
                elif d["action"] == "get_forward_msg":
                    data = {"messages": [
                        {"sender": {"nickname": "老板"}, "message": [{"type": "text", "data": {"text": "明天八点开会"}}]},
                        {"sender": {"nickname": "同事"}, "message": [{"type": "text", "data": {"text": "收到"}}]}]}
                await ws.send_str(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": d["echo"]}))
            return ws

        app = web.Application()
        app.router.add_get("/", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def push(self, ev: dict):
        await self.ws.send_str(json.dumps({"self_id": SELF, "time": int(time.time()), **ev}))

    def sent(self, action):
        return [a for a in self.actions if a["action"] == action]

    async def wait_sent(self, action, n=1, timeout=5.0):
        t0 = time.time()
        while len(self.sent(action)) < n:
            if time.time() - t0 > timeout:
                raise AssertionError(f"{action} 没发出来：{self.actions}")
            await asyncio.sleep(0.02)
        return self.sent(action)


class GroupLLM(FakeLLM):
    async def chat(self, messages, helper=False, **kw):
        last = messages[-1]["content"]
        if "最近的群聊记录" in last:
            self.calls.append(messages)
            return "[@小王] 哈哈哈你也太惨了|||下次叫上我"
        if "更新你对每个群友的印象" in last:
            return f"{FRIEND}|爱吐槽的老朋友，最近在赶论文"
        return await super().chat(messages, helper=helper, **kw)


def _setup_qq(cfg, bot, port, groups=()):
    cfg["qq"] = {**cfg.get("qq", {}), "enabled": True, "ws_url": f"ws://127.0.0.1:{port}/", "owner_id": OWNER,
                 "groups": list(groups),
                 "group": {**(cfg.get("qq", {}).get("group") or {}), "merge_wait_seconds": 0.05}}
    from ailife.qq_bridge import QQ, wait_ready
    q = QQ(cfg, bot)
    bot.qq = q
    q.start()
    return q, wait_ready


def _private(uid, text, mid):
    return {"post_type": "message", "message_type": "private", "user_id": uid, "message_id": mid,
            "sender": {"nickname": "x"}, "message": [{"type": "text", "data": {"text": text}}]}


def _group(gid, uid, name, segs, mid):
    return {"post_type": "message", "message_type": "group", "group_id": gid, "user_id": uid, "message_id": mid,
            "sender": {"nickname": name, "card": name}, "message": segs}


def test_private_qq_shares_brain_and_routes_back(env):  # noqa: F811
    cfg, brain, bot, fake_tg = env
    cfg["style"]["merge_wait_seconds"] = 0.05

    async def go():
        nap = FakeNapCat()
        await nap.start()
        q, wait_ready = _setup_qq(cfg, bot, nap.port)
        assert await wait_ready(q)
        await asyncio.sleep(0.1)
        assert q.ob.self_id == SELF
        await nap.push(_private(999, "陌生人", 1))                     # 不是主人：不理
        await nap.push(_private(OWNER, "我感觉你好像有点不太开心呢", 2))
        sent = await nap.wait_sent("send_private_msg", 2)
        assert all(a["params"]["user_id"] == OWNER for a in sent)
        segs = sent[0]["params"]["message"]
        assert segs[0] == {"type": "reply", "data": {"id": "2"}}       # [引用] → 回复他那条
        assert isinstance(segs, list) and all(isinstance(s, dict) for s in segs)
        assert not [s for s in fake_tg.sent if s[0] == "text"]          # 一条都没发到 Telegram
        rows = brain.db.recent_messages(10)
        assert [r["channel"] for r in rows] == ["qq", "qq", "qq"]
        assert rows[0]["content"] == "我感觉你好像有点不太开心呢"
        assert any("用 QQ 聊天" in c[0]["content"] for c in brain.llm.calls)
        # 他回到 Telegram 说话后：发回 Telegram
        brain.db.set("last_channel", "tg")
        from ailife.output import Bubble, Parsed
        await bot.deliver(Parsed(bubbles=[Bubble(text="在呢")]))
        assert fake_tg.sent[-1][0] == "text" and fake_tg.sent[-1][1][1] == "在呢"
        # NapCat 断了：就算他最后在 QQ 说话，也退回 Telegram
        brain.db.set("last_channel", "qq")
        await q.stop()
        await asyncio.sleep(0.05)
        await bot.deliver(Parsed(bubbles=[Bubble(text="还在吗")]))
        assert fake_tg.sent[-1][1][1] == "还在吗"
        await nap.runner.cleanup()
    asyncio.run(go())


def test_group_chat(env, monkeypatch):  # noqa: F811
    cfg, brain, bot, fake_tg = env
    brain.llm = GroupLLM()
    brain.memory.llm = brain.llm
    brain.db.add_memory("他下周三考六级", 5, "user")                  # 关于他的私密记忆：群里不能用
    import ailife.qq_group as qg

    async def go():
        nap = FakeNapCat()
        await nap.start()
        q, wait_ready = _setup_qq(cfg, bot, nap.port, groups=[GROUP])
        assert await wait_ready(q)
        await asyncio.sleep(0.1)
        q.group.g["impression_every"] = 2
        # 白名单外的群、自己发的消息：都不理
        await nap.push(_group(999, FRIEND, "小王", [{"type": "at", "data": {"qq": str(SELF)}}], 1))
        await nap.push(_group(GROUP, SELF, "她", [{"type": "text", "data": {"text": "我自己"}}], 2))
        # 小王 @ 她
        await nap.push(_group(GROUP, FRIEND, "小王",
                              [{"type": "at", "data": {"qq": str(SELF)}},
                               {"type": "text", "data": {"text": " 我论文又被打回来了"}}], 3))
        sent = await nap.wait_sent("send_group_msg", 2)
        assert {a["params"]["group_id"] for a in sent} == {GROUP}
        first = sent[0]["params"]["message"]
        assert first[0] == {"type": "reply", "data": {"id": "3"}}
        assert first[1] == {"type": "at", "data": {"qq": str(FRIEND)}}
        sys = brain.llm.calls[-1][0]["content"]
        assert "摸鱼小分队" in sys and "关系很好的朋友" in sys
        assert "六级" not in sys and "六级" not in brain.llm.calls[-1][1]["content"]
        # 群消息不进你们俩的私聊记录
        assert not [r for r in brain.db.recent_messages(50) if "论文" in r["content"]]
        rows = brain.db.conn.execute("SELECT role FROM qq_group_messages WHERE group_id=?", (GROUP,)).fetchall()
        assert [r["role"] for r in rows] == ["user", "assistant", "assistant"]
        # 没人叫她、插话骰子没中：不说话
        monkeypatch.setattr(qg.random, "random", lambda: 0.99)
        n = len(nap.sent("send_group_msg"))
        await nap.push(_group(GROUP, FRIEND, "小王", [{"type": "text", "data": {"text": "算了不说了"}}], 4))
        await asyncio.sleep(0.4)
        assert len(nap.sent("send_group_msg")) == n
        # 攒够消息后整理了对小王的印象
        for _ in range(30):
            note = brain.db.conn.execute("SELECT note FROM qq_people WHERE user_id=?", (FRIEND,)).fetchone()["note"]
            if note:
                break
            await asyncio.sleep(0.05)
        assert "赶论文" in note
        await q.stop()
        await nap.runner.cleanup()
    asyncio.run(go())


# ---------------- 只用 QQ（不填 Telegram token） ----------------
def _qq_only_bot(tmp_path, port):
    import shutil
    from test_flow import FIXTURE, cfg_root
    from ailife.config import load_config
    cfg = load_config()
    cfg["root"] = tmp_path
    shutil.copytree(cfg_root() / "persona", tmp_path / "persona", dirs_exist_ok=True)
    shutil.copy(FIXTURE, tmp_path / "persona" / "student.md")
    cfg["persona_file"] = "persona/student.md"
    cfg["telegram"]["bot_token"] = ""
    cfg["telegram"]["owner_id"] = 0
    cfg["style"]["typing_chars_per_sec"] = 1000
    cfg["style"]["merge_wait_seconds"] = 0.05
    cfg["memory"]["embed"]["enabled"] = False
    cfg["proactive"]["quiet_hours"] = ""
    cfg["qq"] = {**cfg["qq"], "enabled": True, "ws_url": f"ws://127.0.0.1:{port}/", "owner_id": OWNER}
    from ailife.brain import Brain
    from ailife.bot import ChatBot
    brain = Brain(cfg)
    brain.llm = FakeLLM()
    brain.memory.llm = brain.llm
    bot = ChatBot(cfg, brain)
    return cfg, brain, bot


def test_qq_only_mode(tmp_path):
    async def go():
        nap = FakeNapCat()
        await nap.start()
        cfg, brain, bot = _qq_only_bot(tmp_path, nap.port)
        assert bot.tg is False and bot.app is None
        await bot.start()                                     # 不连 Telegram
        from ailife.qq_bridge import setup, wait_ready
        q = setup(cfg, bot)
        assert await wait_ready(q)
        await asyncio.sleep(0.1)
        assert bot.owner_id == OWNER and bot.channel() == "qq"
        # 聊天
        await nap.push(_private(OWNER, "在吗", 11))
        await nap.wait_sent("send_private_msg", 2)
        chat_sys = [c[0]["content"] for c in brain.llm.calls if "用 QQ 聊天" in c[0]["content"]]
        assert chat_sys and "[回应:" not in chat_sys[-1] and "发表情包" in chat_sys[-1]
        # 命令（中文别名）
        brain.db.set_diary("2026-10-18", "今天画了一整天图。") if hasattr(brain.db, "set_diary") else None
        n = len(nap.sent("send_private_msg"))
        await nap.push(_private(OWNER, "/心情", 12))
        sent = await nap.wait_sent("send_private_msg", n + 1)
        assert sent[-1]["params"]["user_id"] == OWNER
        n = len(nap.sent("send_private_msg"))
        await nap.push(_private(OWNER, "/game", 13))          # 要按钮的命令：说明一下
        sent = await nap.wait_sent("send_private_msg", n + 1)
        assert "用不了" in sent[-1]["params"]["message"][0]["data"]["text"]
        n = len(nap.sent("send_private_msg"))
        await nap.push(_private(OWNER, "/help", 14))
        sent = await nap.wait_sent("send_private_msg", n + 1)
        assert "/diary" in sent[-1]["params"]["message"][0]["data"]["text"]
        # 信、纪念册这类长文：也发 QQ
        n = len(nap.sent("send_private_msg"))
        await bot.send_letter("这周你好像很忙。")
        sent = await nap.wait_sent("send_private_msg", n + 1)
        assert "这周的信" in sent[-1]["params"]["message"][0]["data"]["text"]
        assert brain.db.last_message("assistant")["channel"] == "qq"
        await q.stop()
        await bot.stop()
        await nap.runner.cleanup()
    asyncio.run(go())


def test_goodbye_over_qq_http(env, monkeypatch):  # noqa: F811
    cfg, brain, bot, fake_tg = env
    cfg["qq"] = {**cfg["qq"], "http_url": "http://127.0.0.1:3000", "owner_id": OWNER}
    from types import SimpleNamespace
    bot.qq = SimpleNamespace(owner_id=OWNER, private_ready=lambda: True)
    brain.db.set("last_channel", "qq")
    posted = []

    class FakeClient:
        def __init__(self, **kw):
            assert kw.get("trust_env") is False                # 不走 Clash 代理
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, url, json=None, headers=None):
            posted.append((url, json))
            return SimpleNamespace(json=lambda: {"status": "ok", "retcode": 0, "data": {"message_id": 9}})
    import httpx
    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(brain.away, "goodbye_text", lambda now: "我先下啦，晚点见")
    assert brain.away.goodbye_sync()
    assert posted[0][0].endswith("/send_private_msg") and posted[0][1]["user_id"] == OWNER
    assert brain.db.last_message("assistant")["channel"] == "qq"


# ---------------- 独立的群聊角色（friend 模式） ----------------
def test_group_character_friend_mode_and_idle(env, monkeypatch):  # noqa: F811
    cfg, brain, bot, fake_tg = env
    brain.llm = GroupLLM()
    brain.memory.llm = brain.llm
    cfg["mode"] = "group"
    from ailife.config import ROOT
    from ailife.persona import load_persona
    brain.persona = load_persona(ROOT / "persona" / "xiaoman.md")
    cfg["qq"] = {**cfg["qq"], "group": {**cfg["qq"]["group"], "owner_note": "把你拉进群的老朋友",
                                         "active_hours": "00:00-23:59"}}
    from ailife.qq_group import GroupChat

    class FakeOB:
        self_id = SELF
        ready = True

        def __init__(self):
            self.sent = []

        async def send_group(self, gid, segs):
            self.sent.append((gid, segs))
            return len(self.sent)

        async def call(self, action, **kw):
            return {"group_name": "画画群"}
    ob = FakeOB()
    gc = GroupChat(cfg, brain, ob, OWNER, [GROUP], ["满满"])
    assert gc.partner is False
    old = time.time() - 5 * 3600
    for uid, name, text in ((OWNER, "阿杰", "周末去不去看展"), (FRIEND, "小王", "去！")):
        brain.db.conn.execute("INSERT INTO qq_group_messages(ts, group_id, user_id, name, role, content) "
                              "VALUES (?,?,?,?,?,?)", (old, GROUP, uid, name, "user", text))
    brain.db.conn.commit()
    msgs = asyncio.run(gc.build_messages(GROUP, False, True, ""))
    sys_txt, user_txt = msgs[0]["content"], msgs[1]["content"]
    assert "把你拉进群的老朋友" in sys_txt and "哥哥" not in sys_txt and "（哥哥）" not in user_txt
    # 安静 5 小时 → 冒泡
    import ailife.qq_group as qg
    monkeypatch.setattr(qg.random, "random", lambda: 0.0)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(qg.asyncio, "sleep", lambda *_: real_sleep(0))
    asyncio.run(gc.idle_tick(60))
    assert len(ob.sent) == 2 and "安静了" in brain.llm.calls[-1][1]["content"]
    # 刚冒过泡、最后一句是她自己：不再冒
    asyncio.run(gc.idle_tick(60))
    assert len(ob.sent) == 2


def test_config_extends_and_instance(tmp_path, monkeypatch):
    import ailife.config as C
    main = tmp_path / "main.yaml"
    main.write_text("llm:\n  temperature: 0.42\nqq:\n  groups: [1]\n", encoding="utf-8")
    grp = tmp_path / "grp.yaml"
    grp.write_text(f"extends: {main}\nmode: group\ninstance_root: {tmp_path / 'inst'}\n"
                   "persona_file: persona/xiaoman.md\nqq:\n  groups: [2]\n", encoding="utf-8")
    monkeypatch.setenv("AILIFE_CONFIG", str(grp))
    c = C.load_config()
    assert c.llm.temperature == 0.42 and c.qq.groups == [2] and c.mode == "group"
    assert c.root == tmp_path / "inst" and (tmp_path / "inst" / "data").is_dir()
    assert c.persona_file == str(C.ROOT / "persona" / "xiaoman.md")      # 角色文件夹里没有：用程序目录的


# ---------------- QQ 私聊的细节 ----------------
def test_flatten_qq_specials():
    f = flatten([{"type": "face", "data": {"id": "14"}},
                 {"type": "image", "data": {"url": "https://x/s.gif", "sub_type": 1, "summary": "[动画表情]"}},
                 {"type": "json", "data": {"data": json.dumps({"prompt": "[QQ小程序]哔哩哔哩", "meta": {"detail_1": {
                     "title": "哔哩哔哩", "desc": "猫猫跳舞合集", "qqdocurl": "https://b23.tv/abc"}}})}},
                 {"type": "forward", "data": {"id": "fw1"}}], SELF)
    assert "[表情:微笑]" in f.text and "[表情包]" in f.text and not f.images
    assert f.stickers == ["https://x/s.gif"] and f.links == ["https://b23.tv/abc"]
    assert "猫猫跳舞合集" in f.text and f.forward_id == "fw1"
    from ailife.qq import card_info, face_for
    assert card_info("not json") == ("", "", "")
    assert face_for("开心") == 13 and face_for("超级开心") == 13 and face_for("??") is None


def _notice(**kw):
    return {"post_type": "notice", **kw}


def test_private_details(tmp_path, monkeypatch):
    async def go():
        nap = FakeNapCat()
        nap.friends = []                                   # 一开始还不是好友
        await nap.start()
        cfg, brain, bot = _qq_only_bot(tmp_path, nap.port)
        cfg["qq"]["poke_back"] = 1.0
        cfg["style"]["typo_rate"] = 0
        from ailife.qq_bridge import setup, wait_ready
        import ailife.qq_bridge as qb
        q = setup(cfg, bot)
        assert await wait_ready(q)
        await asyncio.sleep(0.2)
        assert q.is_friend is False and "不能主动找你" in q.status_line()
        # 他加她好友：自动同意
        await nap.push({"post_type": "request", "request_type": "friend", "user_id": OWNER, "flag": "f1"})
        await nap.wait_sent("set_friend_add_request")
        assert nap.sent("set_friend_add_request")[0]["params"] == {"flag": "f1", "approve": True}
        # 戳一戳：记下来、回复，并且戳回去
        await nap.push(_notice(notice_type="notify", sub_type="poke", user_id=OWNER, target_id=SELF))
        await nap.wait_sent("friend_poke")
        await nap.wait_sent("send_private_msg")
        assert any(r["content"] == "[戳了戳你]" for r in brain.db.recent_messages(10))
        assert nap.sent("set_input_status"), "回复前应该显示“正在输入”"
        # 撤回：她看到了
        await nap.push(_private(OWNER, "其实我有点想你", 21))
        await nap.wait_sent("send_private_msg", 3)
        await nap.push(_notice(notice_type="friend_recall", user_id=OWNER, message_id=21))
        for _ in range(50):
            if any("撤回" in r["content"] for r in brain.db.recent_messages(10)):
                break
            await asyncio.sleep(0.02)
        assert any("撤回" in r["content"] and "其实我有点想你" in r["content"] for r in brain.db.recent_messages(10))
        # 引用她的话回 👎：记进“她会避开的说法”
        her = brain.db.conn.execute("SELECT tg_msg_id, content FROM messages WHERE role='assistant' AND channel='qq' "
                                    "AND tg_msg_id IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        await nap.push({**_private(OWNER, "", 30), "message": [{"type": "reply", "data": {"id": str(her["tg_msg_id"])}},
                                                                {"type": "text", "data": {"text": "👎"}}]})
        for _ in range(50):
            if brain.db.get("disliked") or any("👎" in r["content"] for r in brain.db.recent_messages(5)):
                break
            await asyncio.sleep(0.02)
        assert any("👎" in r["content"] and her["content"][:10] in r["content"] for r in brain.db.recent_messages(5))
        # 合并转发：读出里面的内容
        n = len(nap.sent("send_private_msg"))
        await nap.push({**_private(OWNER, "", 22), "message": [{"type": "forward", "data": {"id": "fw9"}}]})
        await nap.wait_sent("send_private_msg", n + 1)
        assert any("明天八点开会" in r["content"] for r in brain.db.recent_messages(20))
        # 表情包：学下来，打上情绪；之后她发“开心”就用这张
        png = b"\x89PNG fake sticker"

        async def fake_fetch(url, limit=0, timeout=0):
            return png
        monkeypatch.setattr(qb, "fetch_bytes", fake_fetch)
        brain.llm.vision = True

        async def label(b64, prompt, mime="image/png"):
            return "开心"
        brain.llm.label_image = label
        n = len(nap.sent("send_private_msg"))
        await nap.push({**_private(OWNER, "", 23), "message": [
            {"type": "image", "data": {"url": "https://x/a.gif", "sub_type": 1, "summary": "[动画表情]"}}]})
        await nap.wait_sent("send_private_msg", n + 1)
        assert any(r["content"].startswith("[发了一个表情包：开心]") for r in brain.db.recent_messages(20))
        from ailife.output import Bubble, Parsed
        n = len(nap.sent("send_private_msg"))
        await bot.deliver(Parsed(bubbles=[Bubble(sticker="开心")]))
        seg = nap.sent("send_private_msg")[n]["params"]["message"][0]
        assert seg["type"] == "image" and seg["data"]["sub_type"] == 1
        assert seg["data"]["file"].startswith("base64://")
        # 没学到的情绪：发小黄脸
        await bot.deliver(Parsed(bubbles=[Bubble(sticker="困")]))
        assert nap.sent("send_private_msg")[-1]["params"]["message"][0] == {"type": "face", "data": {"id": "25"}}
        # 打错字：补一句“*正确的字”
        from ailife.bot_common import TYPOS
        k = next(iter(TYPOS))
        cfg["style"]["typo_rate"] = 1.0
        monkeypatch.setattr(qb.random, "uniform", lambda a, b: 0.01)
        n = len(nap.sent("send_private_msg"))
        pad = "".join(c for c in "嗯嗯嗯嗯嗯嗯哈哈哈哈哈" if all(c not in key for key in TYPOS))
        sent_text = f"{pad}{k}{pad}"
        await bot.deliver(Parsed(bubbles=[Bubble(text=sent_text)]))
        texts = [a["params"]["message"][-1]["data"].get("text") for a in nap.sent("send_private_msg")[n:]]
        assert texts[-1] == "*" + k and texts[0] != sent_text
        cfg["style"]["typo_rate"] = 0
        # 签名
        line = await q.maybe_update_signature(force=True)
        assert line and nap.sent("set_self_longnick")[-1]["params"]["longNick"] == line
        assert brain.db.get("qq_signature")["text"] == line
        await q.stop()
        await nap.runner.cleanup()
    asyncio.run(go())


def test_goodbye_over_qq_websocket(env, monkeypatch):  # noqa: F811
    """真实情况下 NapCat 是另一个进程；这里把假 NapCat 放在另一个线程的事件循环里。"""
    import threading
    cfg, brain, bot, fake_tg = env
    nap = FakeNapCat()
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(nap.start())
        ready.set()
        loop.run_forever()
    threading.Thread(target=serve, daemon=True).start()
    assert ready.wait(5)
    try:
        cfg["qq"] = {**cfg["qq"], "ws_url": f"ws://127.0.0.1:{nap.port}/", "http_url": "", "owner_id": OWNER}
        from types import SimpleNamespace
        bot.qq = SimpleNamespace(owner_id=OWNER, private_ready=lambda: True)
        brain.db.set("last_channel", "qq")
        monkeypatch.setattr(brain.away, "goodbye_text", lambda now: "我先下啦")
        assert brain.away.goodbye_sync()
        sent = nap.sent("send_private_msg")
        assert sent and sent[0]["params"]["user_id"] == OWNER
        assert brain.db.last_message("assistant")["channel"] == "qq"
    finally:
        loop.call_soon_threadsafe(loop.stop)
