"""小屋里能改的东西：记忆、纪念日、在追的剧、作息城市、话题、说话方式、留言板、习惯打卡。"""
from __future__ import annotations


from aiohttp import web


class RecordsMixin:
    def records_routes(self, a: web.Application):
        r = a.router
        r.add_post("/api/memory/delete", self.mem_delete)
        r.add_post("/api/memory/update", self.mem_update)
        r.add_post("/api/dates/add", self.date_add)
        r.add_post("/api/dates/delete", self.date_delete)
        r.add_post("/api/shows/add", self.show_add)
        r.add_post("/api/shows/delete", self.show_delete)
        r.add_post("/api/settings", self.settings)
        r.add_post("/api/threads/close", self.thread_close)
        r.add_post("/api/expr/set", self.expr_set)
        r.add_post("/api/expr/delete", self.expr_delete)
        r.add_post("/api/notes/add", self.note_add)
        r.add_post("/api/habits/add", self.habit_add)
        r.add_post("/api/habits/delete", self.habit_delete)
        r.add_post("/api/habits/checkin", self.habit_checkin)

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
        from ailife.mind.output import normalize_date
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

    async def habit_add(self, req):
        d = await self._json(req)
        name = str(d.get("name") or "").strip()[:20]
        t = str(d.get("time") or "").strip()
        if not name:
            return web.json_response({"ok": False, "error": "写个目标名，比如：早睡"})
        if t:
            from ailife.mind.together import norm_hhmm
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
