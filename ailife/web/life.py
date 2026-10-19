"""小屋网页里的愿望清单、时间胶囊接口（她的世界只读，在 /api/state 里）。"""
from __future__ import annotations

from pathlib import Path
import time

from aiohttp import web


class LifeWebMixin:
    def life_routes(self, a: web.Application):
        a.router.add_post("/api/wishes/add", self.wish_add)
        a.router.add_post("/api/wishes/done", self.wish_done)
        a.router.add_post("/api/wishes/delete", self.wish_delete)
        a.router.add_post("/api/capsules/add", self.capsule_add)
        a.router.add_post("/api/capsules/delete", self.capsule_delete)
        a.router.add_post("/api/disliked/delete", self.disliked_delete)
        a.router.add_get("/api/persona/raw", self.persona_raw)
        a.router.add_post("/api/persona/check", self.persona_check)
        a.router.add_post("/api/persona/save", self.persona_save)
        a.router.add_post("/api/persona/use", self.persona_use)
        a.router.add_post("/api/persona/new", self.persona_new)
        a.router.add_post("/api/routine/delete", self.routine_delete)

    def life_state(self) -> dict:
        b = self.brain
        from ailife.mind.report import report_for_web
        rh = b.rhythm
        d = b.now().date()
        try:
            her_life = {"school": rh.summary(d), "age": rh.age(d), "birthday": rh.birthday()}
        except Exception:  # noqa: BLE001
            her_life = {}
        from ailife.mind.report import _cache
        hit = _cache.get(id(b))
        report = hit[1] if hit else {"metrics": []}
        if not hit or time.time() - hit[0] > 1800:          # 过期了：后台慢慢算，这次先用旧的
            import asyncio

            async def refresh():
                try:
                    await asyncio.to_thread(report_for_web, b, True)
                except Exception:  # noqa: BLE001
                    pass
            try:
                t = asyncio.get_running_loop().create_task(refresh())
                self._bg_refs = getattr(self, "_bg_refs", set())
                self._bg_refs.add(t)
                t.add_done_callback(self._bg_refs.discard)
            except RuntimeError:                         # 不在事件循环里（测试 / 自检）：这次不算
                pass
        extra = {"report": report,
                 "disliked": [{"text": x["text"], "emoji": x.get("emoji", ""), "ts": x["ts"]} for x in b.quality.disliked()],
                 "routines": rh.routines(), "learned_times": rh.learned_times(),
                 "her_life": her_life}
        from ailife.persona.library import list_personas
        p = b.persona
        extra["persona"] = {"name": p.name, "ta": p.ta, "file": str(self.cfg.persona_file), "identity": p.identity,
                            "warnings": p.warnings,
                            "list": [x for x in list_personas(self.cfg) if not x.get("error")]}
        return {**extra, "wishes": [{"id": w["id"], "text": w["text"], "by": w["by"], "created": w["created"],
                            "done": w["done_ts"], "note": w["note"]} for w in self.db.wishes()],
                "capsules": b.moments.capsules_for_web(),
                "her_world": b.herworld.for_web(b.now().date())}

    async def _poke(self, delay: float = 2.0):
        if self.bot and hasattr(self.bot, "schedule_reply"):
            await self.bot.schedule_reply(delay)

    async def wish_add(self, req):
        d = await self._json(req)
        text = str(d.get("text") or "").strip()[:60]
        if not text:
            return web.json_response({"ok": False, "error": "写一件想一起做的事"})
        self.db.add_wish(text, "user")
        self.db.add_message("event", f"他在小屋里把“{text}”写进了你们的愿望清单")
        return web.json_response({"ok": True})

    async def wish_done(self, req):
        d = await self._json(req)
        wid = int(d["id"])
        note = str(d.get("note") or "").strip()[:120]
        w = next((x for x in self.db.wishes() if x["id"] == wid), None)
        if not w or not self.db.done_wish(wid, note):
            return web.json_response({"ok": False, "error": "没找到这个愿望（或已经完成）"})
        self.db.add_message("event", f"你们一起实现了愿望清单里的“{w['text']}”" + (f"，他说：{note}" if note else ""))
        await self._poke()
        return web.json_response({"ok": True, "message": "🎉 愿望达成！"})

    async def wish_delete(self, req):
        d = await self._json(req)
        self.db.del_wish(int(d["id"]))
        return web.json_response({"ok": True})

    async def capsule_add(self, req):
        d = await self._json(req)
        content = str(d.get("content") or "").strip()[:2000]
        from ailife.mind.moments import parse_open_day
        today = self.brain.now().date()
        day = parse_open_day(str(d.get("day") or ""), today)
        if not content:
            return web.json_response({"ok": False, "error": "写点什么给未来的你们吧"})
        if not day or day <= today or (day - today).days > 3660:
            return web.json_response({"ok": False, "error": "打开日期要是将来（十年以内）的某天"})
        _, mine = await self.brain.moments.create_capsule(content, day)
        await self._poke(3.0)
        return web.json_response({"ok": True, "message": f"埋好了，{day:%Y-%m-%d} 打开"
                                  + ("；她也偷偷写了一封" if mine else "")})

    async def disliked_delete(self, req):
        d = await self._json(req)
        text = str(d.get("text") or "")
        self.db.set("disliked", [x for x in (self.db.get("disliked") or []) if x.get("text") != text])
        return web.json_response({"ok": True})

    async def routine_delete(self, req):
        d = await self._json(req)
        ok = self.brain.rhythm.del_routine_match(str(d.get("when") or ""), str(d.get("what") or ""))
        return web.json_response({"ok": ok, "error": None if ok else "这条已经不在了"})

    async def capsule_delete(self, req):
        d = await self._json(req)
        cid = int(d["id"])
        c = next((x for x in self.db.capsules() if x["id"] == cid), None)
        if not c or c["by"] != "user" or c["opened_ts"]:
            return web.json_response({"ok": False, "error": "只能删你自己写的、还没打开的"})
        self.db.del_capsule(cid)
        return web.json_response({"ok": True})


class PersonaWebMixin:
    """小屋“设置”页里的人设编辑：看、改、检查、保存后马上生效；换角色（会重启）。"""

    def _persona_path(self, rel: str):
        import re
        from pathlib import Path
        rel = (rel or "").replace("\\", "/").strip()
        if not re.fullmatch(r"persona/[\w\u4e00-\u9fa5.-]{1,40}\.md", rel) or ".." in rel:
            raise web.HTTPBadRequest(text="人设文件只能是 persona/ 下面的 .md")
        return Path(self.cfg.root) / rel, rel

    async def persona_raw(self, req):
        path, rel = self._persona_path(req.query.get("file") or str(self.cfg.persona_file))
        if not path.exists():
            raise web.HTTPNotFound(text="没有这个文件")
        return web.json_response({"file": rel, "text": path.read_text(encoding="utf-8")})

    async def persona_check(self, req):
        d = await self._json(req)
        from ailife.persona.loader import parse_persona
        p = parse_persona(str(d.get("text") or ""))
        return web.json_response({"ok": True, "name": p.name, "warnings": p.warnings, "examples": len(p.examples),
                                  "chars": len(p.body), "gender": p.gender, "life": p.life})

    async def persona_save(self, req):
        d = await self._json(req)
        path, rel = self._persona_path(str(d.get("file") or ""))
        text = str(d.get("text") or "")
        from ailife.persona.loader import parse_persona
        p = parse_persona(text)
        fatal = [w for w in p.warnings if "没写 name" in w or "格式有错" in w or "开头没有" in w]
        if fatal:
            return web.json_response({"ok": False, "error": "；".join(fatal)})
        if len(text) > 60000:
            return web.json_response({"ok": False, "error": "人设太长了（6 万字以内）"})
        if path.exists():                                  # 改之前先备份一份
            import time as _t
            bk = Path(self.cfg.root) / "data" / "backup" / "persona"
            bk.mkdir(parents=True, exist_ok=True)
            (bk / f"{path.stem}-{_t.strftime('%Y%m%d-%H%M%S')}.md").write_text(path.read_text(encoding="utf-8"),
                                                                              encoding="utf-8")
        path.write_text(text, encoding="utf-8")
        from ailife.persona.library import _rel
        cur = rel == _rel(self.cfg, Path(self.cfg.root) / self.cfg.persona_file)
        warns = self.brain.reload_persona() if cur else p.warnings
        if cur and self.bot:
            from ailife.chat.common import install_ui_pronoun
            install_ui_pronoun(self.brain.persona.ta)
        return web.json_response({"ok": True, "warnings": warns,
                                  "message": "保存好了，已经生效" if cur else "保存好了（这不是现在用的人设）"})

    async def persona_use(self, req):
        d = await self._json(req)
        path, rel = self._persona_path(str(d.get("file") or ""))
        if not path.exists():
            return web.json_response({"ok": False, "error": "没有这个文件"})
        from ailife.persona.loader import load_persona
        if any("没写 name" in w or "格式有错" in w for w in load_persona(path).warnings):
            return web.json_response({"ok": False, "error": "这个人设有错，先改好再换"})
        from ailife.persona.library import assign, set_persona_file
        db = assign(self.cfg, rel, share_with_current=bool(d.get("share")))
        set_persona_file(self.cfg.root, rel)
        if self.bot and hasattr(self.bot, "restart_event"):
            import asyncio
            asyncio.get_running_loop().call_later(1.5, self.bot.restart_event.set)
        return web.json_response({"ok": True, "message": f"换好了（记忆：{db}），程序正在重启，十几秒后刷新页面"})

    async def persona_new(self, req):
        d = await self._json(req)
        import re
        from pathlib import Path
        name = re.sub(r"[^\w\u4e00-\u9fa5-]+", "", str(d.get("name") or ""))[:24]
        if not name:
            return web.json_response({"ok": False, "error": "起个文件名"})
        path = Path(self.cfg.root) / "persona" / f"{name}.md"
        if path.exists():
            return web.json_response({"ok": False, "error": "已经有这个名字的人设了"})
        tpl = Path(self.cfg.root) / "persona" / "template.md"
        path.write_text(tpl.read_text(encoding="utf-8") if tpl.exists() else f"---\nname: {name}\n---\n", encoding="utf-8")
        return web.json_response({"ok": True, "file": f"persona/{name}.md"})
