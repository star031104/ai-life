"""她的小屋（小档案网页）。

- 本机入口 http://127.0.0.1:8765：在电脑上打开，不用登录。
- 公开入口 127.0.0.1:8766：给 Telegram Mini App / 隧道用。每个请求都要带 Telegram 的签名（initData），
  校验通过且是主人本人才放行；手机快捷指令的 /hook/phone 用单独的密钥。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from urllib.parse import parse_qsl
from datetime import datetime
from pathlib import Path

from aiohttp import web

from .web_life import LifeWebMixin, PersonaWebMixin

log = logging.getLogger("ailife.web")
PAGE = Path(__file__).with_name("web_page.html")
WEEK = "一二三四五六日"


def verify_init_data(init_data: str, bot_token: str, max_age: int = 7 * 86400) -> int | None:
    """校验 Telegram Mini App 的 initData，返回用户 id；不合法返回 None。"""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        got = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not got or not hmac.compare_digest(calc, got):
            return None
        if time.time() - int(pairs.get("auth_date", 0)) > max_age:
            return None
        return int(json.loads(pairs.get("user") or "{}").get("id") or 0) or None
    except Exception:  # noqa: BLE001
        return None


@web.middleware
async def local_only(req, handler):
    host = (req.host or "").split(":")[0].lower()
    local = ("127.0.0.1", "localhost", "[::1]", "::1")
    if host not in local:
        raise web.HTTPForbidden(text="只能在本机打开")
    if req.path.startswith(("/api/", "/v1/", "/hook/")):
        # 别的网站在你浏览器里偷偷调用本机接口（跨站请求）一律拒绝；本机程序（没有 Origin）不受影响
        if req.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
            raise web.HTTPForbidden(text="不允许跨站调用")
        origin = req.headers.get("Origin")
        if origin and origin != "null":
            from urllib.parse import urlparse
            if (urlparse(origin).hostname or "").lower() not in local:
                raise web.HTTPForbidden(text="不允许跨站调用")
    return await handler(req)


class Dashboard(LifeWebMixin, PersonaWebMixin):
    def __init__(self, cfg, brain, bot):
        self.cfg, self.brain, self.bot = cfg, brain, bot
        self.db = brain.db
        c = cfg.get("web") or {}
        self.enabled = bool(c.get("enabled", True))
        self.host = c.get("host", "127.0.0.1")
        self.port = int(c.get("port", 8765))
        self.public_port = int((cfg.get("miniapp") or {}).get("public_port", 8766))
        self.runner: web.AppRunner | None = None
        self.public_runner: web.AppRunner | None = None
        self.tokens: dict[str, float] = {}            # Mini App 登录后的临时令牌 → 过期时间
        self.hook_key = self.db.get("hook_key") or secrets.token_urlsafe(12)
        self.db.set("hook_key", self.hook_key)
        self.public_url: str | None = None
        from .pet import Pet
        self.pet = Pet(cfg, brain, bot)

    def app(self, public: bool = False) -> web.Application:
        a = web.Application(middlewares=[self._guard] if public else [self._local_only])
        a["public"] = public
        a.router.add_post("/api/auth", self.auth)
        a.router.add_route("*", "/hook/phone", self.hook_phone)
        a.router.add_post("/api/threads/close", self.thread_close)
        a.router.add_post("/api/expr/set", self.expr_set)
        a.router.add_post("/api/expr/delete", self.expr_delete)
        a.router.add_post("/api/notes/add", self.note_add)
        a.router.add_post("/api/habits/add", self.habit_add)
        a.router.add_post("/api/habits/delete", self.habit_delete)
        a.router.add_post("/api/habits/checkin", self.habit_checkin)
        self.life_routes(a)
        if not public:                              # 大模型的 Key：只在本机网页里管理，手机小屋里没有
            a.router.add_get("/api/llm", self.llm_list)
            a.router.add_post("/api/llm/models", self.llm_models)
            a.router.add_post("/api/llm/test", self.llm_test)
            a.router.add_post("/api/llm/add", self.llm_add)
            a.router.add_post("/api/llm/remove", self.llm_remove)
            a.router.add_post("/api/llm/restart", self.llm_restart)
            a.router.add_get("/api/update", self.update_info)
            a.router.add_post("/api/update/check", self.update_check)
            a.router.add_post("/api/update/apply", self.update_apply)
            a.router.add_post("/api/update/rollback", self.update_rollback)
            a.router.add_get("/api/links", self.links)
        if not public and getattr(self, "pet", None) and self.pet.enabled:
            self.pet.routes(a)                      # 桌宠只在本机入口
        a.router.add_get("/", self.page)
        a.router.add_get("/api/state", self.state)
        a.router.add_get("/photos/{name}", self.photo)
        a.router.add_post("/api/memory/delete", self.mem_delete)
        a.router.add_post("/api/memory/update", self.mem_update)
        a.router.add_post("/api/dates/add", self.date_add)
        a.router.add_post("/api/dates/delete", self.date_delete)
        a.router.add_post("/api/shows/add", self.show_add)
        a.router.add_post("/api/shows/delete", self.show_delete)
        a.router.add_post("/api/settings", self.settings)
        return a

    async def start(self):
        if not self.enabled:
            return
        self.runner = web.AppRunner(self.app())
        await self.runner.setup()
        await web.TCPSite(self.runner, self.host, self.port).start()
        log.info("%s的小屋：http://%s:%d", self.brain.persona.name, self.host, self.port)

    async def start_public(self):
        """给 Mini App / 隧道用的入口：只监听本机，由隧道转发进来。"""
        r = web.AppRunner(self.app(public=True))
        await r.setup()
        try:
            await web.TCPSite(r, "127.0.0.1", self.public_port).start()
        except Exception:
            await r.cleanup()
            raise
        self.public_runner = r

    async def stop(self):
        for r in (self.runner, self.public_runner):
            if r:
                await r.cleanup()

    # ---------------- 本机入口：只认本机地址（防止网页用 DNS 重绑定偷看） ----------------
    @web.middleware
    async def _local_only(self, req, handler):
        return await local_only(req, handler)

    # ---------------- 公开入口的门卫 ----------------
    @web.middleware
    async def _guard(self, req, handler):
        path = req.path
        if path in ("/", "/api/auth", "/hook/phone", "/favicon.ico"):
            return await handler(req)
        tok = req.headers.get("X-Token") or req.query.get("t") or ""
        if self.tokens.get(tok, 0) > time.time():
            return await handler(req)
        raise web.HTTPUnauthorized(text="链接过期了：在 QQ 里给她发 /小屋（或在 Telegram 里点“小屋”）拿新链接")

    def issue_token(self, hours: float = 12) -> str:
        """给主人发一个临时令牌（/home 里“用浏览器打开”的链接用），过期就失效。"""
        tok = secrets.token_urlsafe(24)
        now = time.time()
        self.tokens = {k: v for k, v in self.tokens.items() if v > now}
        self.tokens[tok] = now + hours * 3600
        return tok

    async def auth(self, req):
        d = await self._json(req)
        if not req.app["public"]:
            return web.json_response({"ok": True, "token": ""})
        uid = verify_init_data(str(d.get("initData") or ""), self.cfg.telegram.bot_token)
        if not uid or uid != getattr(self.bot, "owner_id", 0):
            raise web.HTTPForbidden(text="只有主人能打开")
        tok = secrets.token_urlsafe(24)
        now = time.time()
        self.tokens = {k: v for k, v in self.tokens.items() if v > now}
        self.tokens[tok] = now + 12 * 3600
        return web.json_response({"ok": True, "token": tok})

    async def hook_phone(self, req):
        if req.query.get("k") != self.hook_key:
            raise web.HTTPForbidden()
        text = req.query.get("text") or ""
        if not text and req.can_read_body:
            try:
                text = (await req.text())[:200]
            except Exception:  # noqa: BLE001
                text = ""
        if getattr(self.bot, "phone", None) and text:
            await self.bot.phone.handle(text)
        return web.json_response({"ok": True})

    # ---------------- 页面 ----------------
    async def page(self, req):
        return web.Response(text=PAGE.read_text(encoding="utf-8"), content_type="text/html")

    async def photo(self, req):
        import re
        name = req.match_info["name"]
        if not re.fullmatch(r"\d{1,12}\.jpg", name):          # 只认“时间戳.jpg”，防止读到别的文件
            raise web.HTTPNotFound()
        p = self.cfg.root / "data" / "photos" / name
        if not p.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(p)

    async def state(self, req):
        from .brain import next_occurrence
        b, db = self.brain, self.db
        wt = getattr(self, "_wtask", None)
        if b.weather_info is None and b.weather.city and (wt is None or wt.done()):
            import asyncio
            self._wtask = asyncio.create_task(b.refresh_world())     # 后台查天气，不拖慢页面
        today = b.now().date()
        dates = []
        for d in db.all_dates():
            nxt = next_occurrence(d["date"], today)
            dates.append({"id": d["id"], "date": d["date"], "name": d["name"], "kind": d["kind"],
                          "days_left": (nxt - today).days if nxt else None})
        dates.sort(key=lambda x: (x["days_left"] is None, x["days_left"] if x["days_left"] is not None else 0))
        start_today = datetime.combine(today, datetime.min.time(), b.cfg.tz).timestamp()
        msgs_today = len(db.messages_between(start_today, time.time()))
        total = db.conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        first = db.conn.execute("SELECT MIN(ts) t FROM messages").fetchone()["t"]
        return web.json_response({
            "name": b.persona.name,
            "mood": b.mood, "affection": b.affection,
            "life_today": db.get(f"life_{today}", ""),
            "weather": (b.weather_info or {}).get("text", ""),
            "apps": {"tg": bool(getattr(self.bot, "tg", True)), "qq": getattr(self.bot, "qq", None) is not None,
                     "qq_online": bool(getattr(self.bot, "qq", None) and self.bot.qq.ob.ready)},
            "channel": self.bot.channel() if hasattr(self.bot, "channel") else "tg",
            "stats": {"total": total, "today": msgs_today,
                      "days": int((time.time() - first) // 86400) + 1 if first else 0,
                      "memories": len(db.all_memories()), "stickers": db.sticker_count(),
                      "photos_left": b.photos.remaining_today()},
            "mood_history": [{"ts": r["ts"], "mood": r["mood"], "affection": r["affection"]}
                             for r in db.mood_history(365)] + [{"ts": time.time(), "mood": b.mood,
                                                                 "affection": b.affection, "now": True}],
            "first_ts": first,
            "last_said": next((r["content"] for r in reversed(db.recent_messages(30))
                               if r["role"] == "assistant" and r["kind"] in ("text", "proactive")), ""),
            "photos": self._photos(req),
            "memories": [{"id": m["id"], "content": m["content"], "importance": m["importance"],
                          "ts": m["ts"], "owner": m["owner"]} for m in reversed(db.all_memories())],
            "emotion": b.emotion.snapshot(),
            "emo_history": [{"ts": r["ts"], "joy": r["joy"], "longing": r["longing"], "sulk": r["sulk"],
                             "tired": r["tired"]} for r in db.mood_history(30) if r["joy"] is not None]
                           + [{"ts": time.time(), **b.emotion.snapshot()}],
            "threads": [{"id": t["id"], "topic": t["topic"], "ts": t["ts"]} for t in db.open_threads(20)],
            "expressions": [{"id": e["id"], "phrase": e["phrase"], "meaning": e["meaning"], "status": e["status"],
                             "seen": e["seen"], "uses": e["uses"], "weight": round(e["weight"], 2)}
                            for e in db.expressions()],
            "notes": [{"id": n["id"], "ts": n["ts"], "author": n["author"], "content": n["content"]}
                      for n in db.notes(60)],
            "letters": [{"week": x["week"], "ts": x["ts"], "content": x["content"]} for x in db.letters(12)],
            "sense": {"pc": db.get("sense_pc"), "phone": db.get("sense_phone"),
                      "ntfy": getattr(getattr(self.bot, "phone", None), "url", None),
                      "hook": (f"{self.public_url}/hook/phone?k={self.hook_key}&text=" if self.public_url else None),
                      "public_url": self.public_url},
            "diaries": [{"day": d["day"], "content": d["content"]} for d in reversed(db.recent_diary(14))],
            "dates": dates,
            "shows": [{"id": s["id"], "name": s["name"], "weekday": s["weekday"], "time": s["time"],
                       "episode": s["episode"], "total": s["total_eps"], "subject_id": s["subject_id"],
                       "latest": [{"ep": e["ep"], "title": e["title"]} for e in b.media.recent_eps_cached(s, 1)]}
                      for s in db.active_shows()],
            "arcs": b.story.for_web(today),
            "thoughts": [{"ts": t["ts"], "thought": t["thought"], "mood": t["mood"], "contact": bool(t["contact"]),
                          "sent": bool(t["sent"])} for t in reversed(db.thoughts(12))],
            "profile": (lambda p: {"ts": p["ts"], "content": p["content"]} if p else None)(db.latest_profile()),
            "music": [{"ts": m["ts"], "title": m["title"], "artist": m["artist"], "album": m["album"],
                       "url": f"https://music.163.com/song?id={m['song_id']}" if m["song_id"] else None,
                       "source": m["source"]} for m in db.music(30)],
            "top_artists": [{"artist": a, "n": n} for a, n in db.top_artists()],
            "pet": bool(getattr(self, "pet", None) and self.pet.enabled),
            "skin": (self.pet.sprite_id() if getattr(self, "pet", None) and self.pet.enabled
                     and not (getattr(req, "app", None) is not None and req.app.get("public"))
                     else ""),
            "habits": b.together.habits_for_web(),
            **self.life_state(),
            "recaps": [{"month": r["month"], "ts": r["ts"], "content": r["content"], "stats": r["stats"]}
                       for r in db.recaps(12)],
            "reminders": [{"due": r["due_ts"], "content": r["content"]} for r in db.pending_reminders()],
            "settings": {"city": b.weather.city, "schedule": db.get("schedule") or ""},
        })

    def _photos(self, req=None) -> list[dict]:
        d = self.cfg.root / "data" / "photos"
        if not d.exists():
            return []
        tok = (req.headers.get("X-Token") if req is not None else "") or ""
        q = f"?t={tok}" if tok else ""
        files = sorted(d.glob("*.jpg"), key=lambda p: p.name, reverse=True)[:24]
        return [{"url": f"photos/{p.name}{q}", "ts": int(p.stem) if p.stem.isdigit() else 0} for p in files]

    # ---------------- 修改 ----------------
    async def _json(self, req) -> dict:
        try:
            return await req.json()
        except Exception:  # noqa: BLE001
            return {}

    async def mem_delete(self, req):
        d = await self._json(req)
        self.db.delete_memory(int(d["id"]))
        return web.json_response({"ok": True})

    async def mem_update(self, req):
        d = await self._json(req)
        self.db.update_memory(int(d["id"]), content=(d.get("content") or None),
                              importance=int(d["importance"]) if d.get("importance") else None)
        return web.json_response({"ok": True})

    async def date_add(self, req):
        from .output import normalize_date
        d = await self._json(req)
        nd = normalize_date(str(d.get("date", "")))
        name = str(d.get("name", "")).strip()
        if not nd or not name:
            return web.json_response({"ok": False, "error": "日期写成 11-20（每年）或 2026-12-20（一次性）"})
        kind = "birthday" if "生日" in name else "anniversary" if len(nd) == 5 else "event"
        self.db.add_date(nd, name[:40], kind, 3 if kind != "event" else 1)
        return web.json_response({"ok": True})

    async def date_delete(self, req):
        d = await self._json(req)
        self.db.delete_date(int(d["id"]))
        return web.json_response({"ok": True})

    async def show_add(self, req):
        d = await self._json(req)
        name = str(d.get("name", "")).strip().strip("《》")
        t = str(d.get("time", ""))
        try:
            wd = int(d.get("weekday"))
            hh, mm = map(int, t.split(":"))
            assert 0 <= wd <= 6 and 0 <= hh < 24 and 0 <= mm < 60 and name
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "error": "填番名、星期和 HH:MM 时间"})
        sid = self.db.add_show(name[:40], wd, f"{hh:02d}:{mm:02d}")
        self.brain._enrich_later(sid)
        return web.json_response({"ok": True})

    async def show_delete(self, req):
        d = await self._json(req)
        self.db.delete_show(int(d["id"]))
        return web.json_response({"ok": True})

    async def settings(self, req):
        d = await self._json(req)
        msg = []
        if "city" in d:
            self.db.set("city", str(d["city"]).strip())
            w = await self.brain.weather.fetch(force=True) if d["city"] else None
            self.brain.weather_info = w
            msg.append(w["text"] if w else ("已清空城市" if not d["city"] else "城市已保存，但天气没查到"))
        if "schedule" in d:
            self.db.set("schedule", str(d["schedule"]).strip())
            msg.append("作息已保存")
        return web.json_response({"ok": True, "message": "；".join(msg)})

    # ---------------- 自定义大模型（只在本机网页） ----------------
    async def llm_list(self, req):
        from .llm_custom import load_custom
        custom = ((load_custom(self.cfg.root).get("llm") or {}).get("profiles") or {})
        llm = self.cfg.get("llm") or {}
        profs = [{"name": n, "model": p.get("model"), "base_url": p.get("base_url"), "custom": n in custom}
                 for n, p in (llm.get("profiles") or {}).items()]
        for n, p in custom.items():                 # 刚加的（还没重启）
            if not any(x["name"] == n for x in profs):
                profs.append({"name": n, "model": p.get("model"), "base_url": p.get("base_url"), "custom": True,
                              "pending": True})
        return web.json_response({"profiles": profs, "routes": dict(llm.get("routes") or {}),
                                  "stats": self.brain.llm.stats() if hasattr(self.brain.llm, "stats") else {}})

    async def llm_models(self, req):
        from .llm_custom import chat_models, list_models
        d = await self._json(req)
        models, url, via_proxy, err = await list_models(str(d.get("url") or ""), str(d.get("key") or ""),
                                                        str(self.cfg.get("proxy") or ""))
        return web.json_response({"ok": bool(models), "url": url, "via_proxy": via_proxy, "error": err,
                                  "models": chat_models(models) or models})

    async def llm_test(self, req):
        from .llm_custom import probe_model
        d = await self._json(req)
        ok, secs, text, extra = await probe_model(str(d.get("url") or ""), str(d.get("key") or ""),
                                                  str(d.get("model") or ""), str(self.cfg.get("proxy") or ""),
                                                  bool(d.get("via_proxy")))
        return web.json_response({"ok": ok, "secs": round(secs, 1), "text": text, "extra": extra})

    async def llm_add(self, req):
        from .llm_custom import save_profile
        d = await self._json(req)
        if not d.get("url") or not d.get("model"):
            return web.json_response({"ok": False, "error": "地址和模型都要有"})
        name, routes = save_profile(self.cfg, url=str(d["url"]), key=str(d.get("key") or ""), model=str(d["model"]),
                                    name=str(d.get("name") or ""), how=str(d.get("how") or "first"),
                                    roles_bg=("helper", "memory", "night"), via_proxy=bool(d.get("via_proxy")),
                                    extra=d.get("extra") or None, root=self.cfg.root)
        return web.json_response({"ok": True, "name": name, "routes": routes,
                                  "message": f"加好了：{name}。点“重启生效”或者下次启动时生效"})

    async def llm_remove(self, req):
        from .llm_custom import remove_profile
        d = await self._json(req)
        ok = remove_profile(str(d.get("name") or ""), self.cfg.root)
        return web.json_response({"ok": ok, "message": "删掉了，重启后生效" if ok else "只能删自己加的接口"})

    async def llm_restart(self, req):
        if self.bot and hasattr(self.bot, "restart_event"):
            import asyncio
            asyncio.get_running_loop().call_later(1.0, self.bot.restart_event.set)
            return web.json_response({"ok": True, "message": "程序正在重启，十几秒后刷新页面"})
        return web.json_response({"ok": False, "error": "重启不了：手动关掉 start.bat 再开"})

    # ---------------- 各个连接的状态（四种颜色） ----------------
    def link_states(self) -> list[dict]:
        """green＝好好的（收到过东西）；blue＝装好了，等第一次；yellow＝要你动手；gray＝没开。"""
        out = []
        db, bot, now = self.db, self.bot, time.time()

        def last_from(channel: str | None):
            q = "SELECT MAX(ts) t FROM messages WHERE role='user'" + (
                " AND channel=?" if channel == "qq" else " AND (channel IS NULL OR channel='tg')")
            r = db.conn.execute(q, (channel,) if channel == "qq" else ()).fetchone()
            return (r["t"] if r else None) or 0

        def ago(ts):
            if not ts:
                return "还没收到过"
            m = (now - ts) / 60
            return "刚刚收到过" if m < 2 else f"{int(m)} 分钟前收到过" if m < 60 else \
                f"{int(m // 60)} 小时前收到过" if m < 1440 else f"{int(m // 1440)} 天前收到过"
        qq = getattr(bot, "qq", None)
        if qq is None:
            out.append({"key": "qq", "name": "QQ", "state": "gray", "text": "没开（qq_setup.bat 可以接上）"})
        elif not qq.ob.ready:
            out.append({"key": "qq", "name": "QQ（NapCat）", "state": "yellow",
                        "text": "NapCat 没连上：程序会自己再拉起它；一直不行就看日志或者重跑 qq_setup.bat"})
        else:
            t = last_from("qq")
            out.append({"key": "qq", "name": "QQ（NapCat）", "state": "green" if t else "blue",
                        "text": f"连上了，{ago(t)}你的消息" if t else "连上了，等你第一条消息"})
        if not getattr(bot, "tg", False) or getattr(bot, "app", None) is None:
            out.append({"key": "tg", "name": "Telegram", "state": "gray", "text": "没开"})
        else:
            upd = getattr(bot.app, "updater", None)
            running = bool(upd and getattr(upd, "running", False))
            t = last_from("tg")
            if not running:
                out.append({"key": "tg", "name": "Telegram", "state": "yellow", "text": "没连上：检查代理和 bot token"})
            elif not bot.owner_id:
                out.append({"key": "tg", "name": "Telegram", "state": "blue", "text": "连上了，用你的 TG 给它发 /start 绑定"})
            else:
                out.append({"key": "tg", "name": "Telegram", "state": "green" if t else "blue",
                            "text": f"连上了，{ago(t)}你的消息" if t else "连上了，等你第一条消息"})
        st = self.brain.llm.stats() if hasattr(self.brain.llm, "stats") else {}
        calls, ok = int(st.get("calls", 0)), int(st.get("ok", 0))
        if not calls:
            out.append({"key": "llm", "name": "大模型", "state": "blue", "text": "今天还没用过"})
        elif ok == 0 and calls >= 3:
            out.append({"key": "llm", "name": "大模型", "state": "yellow",
                        "text": f"今天 {calls} 次都没成功：Key 失效或额度用完？在下面“大模型”里换一个"})
        else:
            lim = int(st.get("limited", 0))
            out.append({"key": "llm", "name": "大模型", "state": "green",
                        "text": f"今天 {ok}/{calls} 次成功" + (f"，限流 {lim} 次" if lim else "")})
        pet = getattr(self, "pet", None)
        if not (pet and pet.enabled):
            out.append({"key": "pet", "name": "桌宠", "state": "gray", "text": "没开"})
        else:
            out.append({"key": "pet", "name": "桌宠", "state": "green" if pet.active() else "blue",
                        "text": "在桌面上" if pet.active() else "没打开（python run.py pet，或者开机自动出来）"})
        sense_on = bool((self.cfg.get("sense") or {}).get("enabled", True))
        pc = db.get("sense_pc") or {}
        if not sense_on:
            out.append({"key": "sense", "name": "电脑感知", "state": "gray", "text": "没开"})
        elif getattr(bot, "pc", None) and getattr(bot.pc, "paused", lambda: False)():
            out.append({"key": "sense", "name": "电脑感知", "state": "yellow", "text": "暂停中（/sense on 恢复）"})
        elif now - float(pc.get("ts") or 0) < 300:
            out.append({"key": "sense", "name": "电脑感知", "state": "green", "text": pc.get("desc") or "在看"})
        else:
            out.append({"key": "sense", "name": "电脑感知", "state": "blue", "text": "开着，还没看到什么"})
        up = db.get("update_info") or {}
        if up.get("newer"):
            out.append({"key": "update", "name": "软件更新", "state": "yellow",
                        "text": f"有新版本 {up.get('latest')}（现在 {up.get('current')}）：在设置里点更新"})
        return out

    async def links(self, req):
        return web.json_response({"links": self.link_states()})

    # ---------------- 软件更新（只在本机网页） ----------------
    def _restart_soon(self):
        import asyncio
        if self.bot and hasattr(self.bot, "restart_event"):
            asyncio.get_running_loop().call_later(1.5, self.bot.restart_event.set)

    async def update_info(self, req):
        from .update import current_version, load_state
        st = load_state(self.cfg.root)
        return web.json_response({"current": current_version(), "info": self.db.get("update_info") or {},
                                  "last": {k: st.get(k) for k in ("from", "to", "state", "ts", "sha256", "source")} if st else {},
                                  "auto": bool((self.cfg.get("update") or {}).get("auto_check", True))})

    async def update_check(self, req):
        import asyncio
        from .update import check
        info = await asyncio.to_thread(check, self.cfg)
        self.db.set("update_info", info)
        return web.json_response({"ok": not (info["error"] and not info["latest"]), **info})

    async def update_apply(self, req):
        import asyncio
        from .update import UpdateError, apply
        try:
            st = await asyncio.to_thread(apply, self.cfg)
        except UpdateError as e:
            return web.json_response({"ok": False, "error": str(e)})
        self._restart_soon()
        return web.json_response({"ok": True, "to": st["to"],
                                  "message": f"更新到 {st['to']} 了，正在重启，半分钟后刷新页面"})

    async def update_rollback(self, req):
        from .update import UpdateError, rollback
        try:
            st = rollback(self.cfg.root)
        except UpdateError as e:
            return web.json_response({"ok": False, "error": str(e)})
        self._restart_soon()
        return web.json_response({"ok": True, "message": f"换回 {st['from']} 了，正在重启"})

    # ---------------- 新增：话题 / 说话方式 / 留言板 ----------------
    async def thread_close(self, req):
        d = await self._json(req)
        self.db.close_thread(int(d["id"]))
        return web.json_response({"ok": True})

    async def expr_set(self, req):
        d = await self._json(req)
        st = str(d.get("status") or "")
        if st in ("approved", "rejected", "pending"):
            self.db.set_expression(int(d["id"]), status=st, **({"weight": 1.0} if st == "approved" else {}))
        return web.json_response({"ok": True})

    async def expr_delete(self, req):
        d = await self._json(req)
        self.db.delete_expression(int(d["id"]))
        return web.json_response({"ok": True})

    async def note_add(self, req):
        d = await self._json(req)
        text = str(d.get("content") or "").strip()[:500]
        if not text:
            return web.json_response({"ok": False, "error": "写点什么吧"})
        self.db.add_note("user", text)
        if self.bot and hasattr(self.bot, "on_note"):
            self.bot.spawn(self.bot.on_note(text))
        return web.json_response({"ok": True, "message": "她会看到的"})

    # ---------------- 一起打卡 ----------------
    async def habit_add(self, req):
        d = await self._json(req)
        name = str(d.get("name") or "").strip()[:20]
        t = str(d.get("time") or "").strip()
        if not name:
            return web.json_response({"ok": False, "error": "写个目标名，比如：早睡"})
        if t:
            from .together import norm_hhmm
            t = norm_hhmm(t)
            if not t:
                return web.json_response({"ok": False, "error": "时间写成 23:30 这样（0:00～23:59）"})
        self.db.add_habit(name, t or None)
        self.db.add_message("event", f"他和你约好了一个打卡目标：{name}" + (f"（每天 {t}）" if t else "") + "，你负责监督他")
        return web.json_response({"ok": True})

    async def habit_delete(self, req):
        d = await self._json(req)
        self.db.del_habit(int(d["id"]))
        return web.json_response({"ok": True})

    async def habit_checkin(self, req):
        d = await self._json(req)
        hid = int(d["id"])
        h = next((x for x in self.db.habits() if x["id"] == hid), None)
        if not h:
            return web.json_response({"ok": False, "error": "没有这个目标"})
        new, streak = self.brain.together.do_checkin(hid)
        if new:
            self.db.add_message("event", f"他完成了打卡：{h['name']}（已经连续 {streak} 天）")
            if self.bot and hasattr(self.bot, "schedule_reply"):
                await self.bot.schedule_reply(2.0)
        return web.json_response({"ok": True, "message": f"打卡成功！连续 {streak} 天" if new else "今天已经打过卡啦"})
