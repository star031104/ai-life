"""她的小屋（小档案网页）：http://127.0.0.1:8765，只认本机地址。

她跑在服务器上时，用你电脑上的“打开小屋”（scripts/open_home）通过 SSH 隧道把这个端口接到你电脑上再打开，
网页本身从不对外公开。
"""
from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime
from pathlib import Path

from aiohttp import web

from ailife.web.auth import local_only
from ailife.web.life import LifeWebMixin, PersonaWebMixin
from ailife.web.records import RecordsMixin
from ailife.web.system import SystemMixin

log = logging.getLogger("ailife.web")
PAGE = Path(__file__).with_name("page.html")
TOKENS = Path(__file__).with_name("tokens.css")          # 17.0 B 版：和群小屋共用的颜色、时段主题、组件
AVATAR_EXT = (".webp", ".png", ".jpg", ".jpeg")
WEEK = "一二三四五六日"


class Dashboard(RecordsMixin, LifeWebMixin, PersonaWebMixin, SystemMixin):
    def __init__(self, cfg, brain, bot):
        self.cfg, self.brain, self.bot = cfg, brain, bot
        self.db = brain.db
        c = cfg.get("web") or {}
        self.enabled = bool(c.get("enabled", True))
        self.host = c.get("host", "127.0.0.1")
        self.port = int(c.get("port", 8765))
        self.runner: web.AppRunner | None = None
        self.hook_key = self.db.get("hook_key") or secrets.token_urlsafe(12)
        self.db.set("hook_key", self.hook_key)

    def app(self) -> web.Application:
        a = web.Application(middlewares=[self._local_only])
        a.router.add_route("*", "/hook/phone", self.hook_phone)
        self.records_routes(a)
        self.life_routes(a)
        self.system_routes(a)
        a.router.add_get("/", self.page)
        a.router.add_get("/api/state", self.state)
        a.router.add_get("/photos/{name}", self.photo)
        a.router.add_get("/tokens.css", self.tokens_css)
        a.router.add_get("/avatar/{kind}", self.avatar)
        return a

    async def start(self):
        if not self.enabled:
            return
        self.runner = web.AppRunner(self.app())
        await self.runner.setup()
        await web.TCPSite(self.runner, self.host, self.port).start()
        log.info("%s的小屋：http://%s:%d", self.brain.persona.name, self.host, self.port)

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()

    # ---------------- 只认本机地址（防止网页用 DNS 重绑定偷看） ----------------
    @web.middleware
    async def _local_only(self, req, handler):
        return await local_only(req, handler)

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

    async def tokens_css(self, req):
        return web.Response(text=TOKENS.read_text(encoding="utf-8"), content_type="text/css",
                            headers={"Cache-Control": "no-cache"})

    async def avatar(self, req):
        """她的立绘 / 头像：data/avatar/ 里文件名带 portrait / avatar 的图（不进 GitHub）。同一批图也是画她自己的参考图。"""
        p = avatar_file(Path(self.cfg.root) / "data" / "avatar", req.match_info["kind"])
        if p is None:
            raise web.HTTPNotFound()
        return web.FileResponse(p, headers={"Cache-Control": "max-age=600"})

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
        from ailife.mind.brain import next_occurrence
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
            "apps": {"qq": getattr(self.bot, "qq", None) is not None,
                     "qq_online": bool(getattr(self.bot, "qq", None) and self.bot.qq.ob.ready)},
            "channel": "qq",
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
            "sense": {"phone": db.get("sense_phone"),
                      "ntfy": getattr(getattr(self.bot, "phone", None), "url", None)},
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
        files = sorted(d.glob("*.jpg"), key=lambda p: p.name, reverse=True)[:24]
        return [{"url": f"photos/{p.name}", "ts": int(p.stem) if p.stem.isdigit() else 0} for p in files]

    # ---------------- 修改 ----------------
    async def _json(self, req) -> dict:
        try:
            return await req.json()
        except Exception:  # noqa: BLE001
            return {}


def avatar_file(d: Path, kind: str) -> Path | None:
    """kind 只认 portrait（立绘）/ avatar（头像）；头像没有就用立绘。"""
    if kind not in ("portrait", "avatar") or not d.is_dir():
        return None
    files = sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in AVATAR_EXT)
    for want in ((kind,) if kind == "portrait" else ("avatar", "portrait")):
        for f in files:
            if want in f.stem.lower():
                return f
    return files[0] if files and kind == "portrait" else None
