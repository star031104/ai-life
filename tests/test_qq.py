"""QQ 接入：纯函数 + 用假的 NapCat（本机 WebSocket）跑一遍私聊和群聊（不联网）。"""
import asyncio
import json
import time


from test_flow import FakeLLM, env  # noqa: F401  （env 是 fixture）

from ailife.qq import flatten

SELF, OWNER = 10001, 55555


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


def _setup_qq(cfg, bot, port, groups=()):
    cfg["qq"] = {**cfg.get("qq", {}), "enabled": True, "ws_url": f"ws://127.0.0.1:{port}/", "owner_id": OWNER,
                 "groups": list(groups)}
    from ailife.qq_bridge import QQ, wait_ready
    q = QQ(cfg, bot)
    bot.qq = q
    q.start()
    return q, wait_ready


def _private(uid, text, mid):
    return {"post_type": "message", "message_type": "private", "user_id": uid, "message_id": mid,
            "sender": {"nickname": "x"}, "message": [{"type": "text", "data": {"text": text}}]}


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
