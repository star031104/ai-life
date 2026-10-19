"""小屋里的系统页：管理大模型、看各个连接的状态、软件更新。"""
from __future__ import annotations

import time

from aiohttp import web


class SystemMixin:
    def system_routes(self, a: web.Application):
        r = a.router
        r.add_get("/api/llm", self.llm_list)
        r.add_post("/api/llm/models", self.llm_models)
        r.add_post("/api/llm/test", self.llm_test)
        r.add_post("/api/llm/add", self.llm_add)
        r.add_post("/api/llm/remove", self.llm_remove)
        r.add_post("/api/llm/route", self.llm_route)
        r.add_post("/api/llm/restart", self.llm_restart)
        r.add_get("/api/update", self.update_info)
        r.add_post("/api/update/check", self.update_check)
        r.add_post("/api/update/apply", self.update_apply)
        r.add_post("/api/update/rollback", self.update_rollback)
        r.add_get("/api/links", self.links)

    # ---------------- 大模型：都是你自己加的，程序里不内置地址 ----------------
    async def llm_list(self, req):
        from ailife.llm import store
        ov = store.overview(self.cfg, self.cfg.root)
        running = set(getattr(self.brain.llm, "profiles", {}) or {})
        for m in ov["models"]:
            m["pending"] = m["name"] not in running          # 刚加的，重启后才生效
        ov["stats"] = self.brain.llm.stats() if hasattr(self.brain.llm, "stats") else {}
        return web.json_response(ov)

    async def llm_models(self, req):
        from ailife.llm.probe import list_models
        from ailife.llm.roles import default_roles, guess_kind
        d = await self._json(req)
        models, url, via_proxy, err = await list_models(str(d.get("url") or ""), str(d.get("key") or ""),
                                                        str(self.cfg.get("proxy") or ""))
        return web.json_response({"ok": bool(models), "url": url, "via_proxy": via_proxy, "error": err,
                                  "models": [{"id": m, "kind": guess_kind(m), "roles": default_roles(m)}
                                             for m in models]})

    async def llm_test(self, req):
        from ailife.llm.probe import probe
        from ailife.llm.roles import guess_kind
        d = await self._json(req)
        model = str(d.get("model") or "")
        ok, secs, text, extra = await probe(str(d.get("url") or ""), str(d.get("key") or ""), model,
                                            str(d.get("kind") or guess_kind(model)), str(self.cfg.get("proxy") or ""),
                                            bool(d.get("via_proxy")))
        return web.json_response({"ok": ok, "secs": round(secs, 1), "text": text, "extra": extra})

    async def llm_add(self, req):
        from ailife.llm import store
        from ailife.llm.roles import ROLES
        d = await self._json(req)
        if not d.get("url") or not d.get("model"):
            return web.json_response({"ok": False, "error": "地址和模型都要有"})
        roles = [r for r in (d.get("roles") or []) if r in ROLES]
        name = store.add_model(self.cfg, url=str(d["url"]), key=str(d.get("key") or ""), model=str(d["model"]),
                               roles=roles, first=bool(d.get("first", True)), name=str(d.get("name") or ""),
                               extra=d.get("extra") or None, via_proxy=bool(d.get("via_proxy")), root=self.cfg.root)
        return web.json_response({"ok": True, "name": name, "message": f"加好了：{name}。点“重启生效”或者下次启动时生效"})

    async def llm_remove(self, req):
        from ailife.llm import store
        d = await self._json(req)
        ok = store.remove_model(str(d.get("name") or ""), self.cfg.root)
        return web.json_response({"ok": ok, "message": "删掉了，重启后生效" if ok else "只能删在这里加的模型"})

    async def llm_route(self, req):
        from ailife.llm import store
        d = await self._json(req)
        try:
            store.set_route(str(d.get("role") or ""), [str(x) for x in d.get("names") or []], self.cfg.root)
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)})
        return web.json_response({"ok": True, "message": "改好了，重启后生效"})

    async def llm_restart(self, req):
        if self.bot and hasattr(self.bot, "restart_event"):
            self._restart_soon()
            return web.json_response({"ok": True, "message": "程序正在重启，十几秒后刷新页面"})
        return web.json_response({"ok": False, "error": "重启不了：在托盘里点“重启”"})

    # ---------------- 各个连接的状态（四种颜色） ----------------
    def link_states(self) -> list[dict]:
        """green＝好好的（收到过东西）；blue＝装好了，等第一次；yellow＝要你动手；gray＝没开。"""
        out = []
        db, bot, now = self.db, self.bot, time.time()

        def last_from(channel: str):
            r = db.conn.execute("SELECT MAX(ts) t FROM messages WHERE role='user' AND channel=?", (channel,)).fetchone()
            return (r["t"] if r else None) or 0

        def ago(ts):
            if not ts:
                return "还没收到过"
            m = (now - ts) / 60
            return "刚刚收到过" if m < 2 else f"{int(m)} 分钟前收到过" if m < 60 else \
                f"{int(m // 60)} 小时前收到过" if m < 1440 else f"{int(m // 1440)} 天前收到过"
        qq = getattr(bot, "qq", None)
        if qq is None:
            out.append({"key": "qq", "name": "QQ", "state": "gray", "text": "没开（config.yaml 的 qq.enabled）"})
        elif not qq.ob.ready:
            out.append({"key": "qq", "name": "QQ（NapCat）", "state": "yellow",
                        "text": "NapCat 没连上：程序会自己再拉起它；一直不行就看日志或者重跑 scripts\\qq_setup.bat"})
        else:
            t = last_from("qq")
            out.append({"key": "qq", "name": "QQ（NapCat）", "state": "green" if t else "blue",
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
        up = db.get("update_info") or {}
        if up.get("newer"):
            out.append({"key": "update", "name": "软件更新", "state": "yellow",
                        "text": f"有新版本 {up.get('latest')}（现在 {up.get('current')}）：在设置里点更新"})
        return out

    async def links(self, req):
        return web.json_response({"links": self.link_states()})

    def _restart_soon(self):
        import asyncio
        if self.bot and hasattr(self.bot, "restart_event"):
            asyncio.get_running_loop().call_later(1.5, self.bot.restart_event.set)

    async def update_info(self, req):
        from ailife.core.update import current_version, load_state
        st = load_state(self.cfg.root)
        return web.json_response({"current": current_version(), "info": self.db.get("update_info") or {},
                                  "last": {k: st.get(k) for k in ("from", "to", "state", "ts", "sha256", "source")} if st else {},
                                  "auto": bool((self.cfg.get("update") or {}).get("auto_check", True))})

    async def update_check(self, req):
        import asyncio
        from ailife.core.update import check
        info = await asyncio.to_thread(check, self.cfg)
        self.db.set("update_info", info)
        return web.json_response({"ok": not (info["error"] and not info["latest"]), **info})

    async def update_apply(self, req):
        import asyncio
        from ailife.core.update import UpdateError, apply
        try:
            st = await asyncio.to_thread(apply, self.cfg)
        except UpdateError as e:
            return web.json_response({"ok": False, "error": str(e)})
        self._restart_soon()
        return web.json_response({"ok": True, "to": st["to"],
                                  "message": f"更新到 {st['to']} 了，正在重启，半分钟后刷新页面"})

    async def update_rollback(self, req):
        from ailife.core.update import UpdateError, rollback
        try:
            st = rollback(self.cfg.root)
        except UpdateError as e:
            return web.json_response({"ok": False, "error": str(e)})
        self._restart_soon()
        return web.json_response({"ok": True, "message": f"换回 {st['from']} 了，正在重启"})
