"""桌面上的她：Live2D 桌宠，和 Telegram 里的她共用同一个大脑、记忆和心情。

- 网页 http://127.0.0.1:8765/pet ，由 `python run.py pet` 打开成一个真透明、置顶、无边框、不占任务栏的小窗口
  （PySide6；右下角托盘有图标可以显示 / 收起 / 关掉），没装 PySide6 就退回 pywebview（Windows 上有浅色底）或浏览器。
- 在桌宠里打字和她聊，她的话冒在头顶的气泡里；她在 Telegram 上主动找你时，桌宠头上也会冒气泡。
- Live2D 的运行库和模型第一次用时从 CDN 下载并缓存到 data/pet/，以后不联网也能用。
  默认模型是 Live2D 官方示例模型 Rice（金发小魔法师，pet.preset: rice）；也可以换回 Haru（pet.preset: haru）。
  这两个都是 Live2D 官方的免费示例（Free Material License：个人和小规模使用可以免费用）。想换成自己的模型，把整个模型文件夹放进 data/pet/model/ 就行，
  程序会自动找到里面的 xxx.model3.json（Cubism 3/4）或 xxx.model.json（Cubism 2）；有好几个时在 pet.model 里指定。
- 另外提供 OpenAI 兼容接口 http://127.0.0.1:8765/v1/chat/completions ，
  别的桌宠/VTuber 程序（如 Open-LLM-VTuber、AIRI）把大模型地址填成它，就是在和同一个她说话。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
from aiohttp import web

log = logging.getLogger("ailife.pet")
PAGE = Path(__file__).with_name("pet_page.html")

LIBS = {
    "pixi.min.js": "https://cdn.jsdelivr.net/npm/pixi.js@6.5.10/dist/browser/pixi.min.js",
    "index.min.js": "https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/index.min.js",
    "live2d.min.js": "https://cdn.jsdelivr.net/gh/dylanNew/live2d@master/webgl/Live2D/lib/live2d.min.js",
    "live2dcubismcore.min.js": "https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js",
}
HARU_BASE = "https://cdn.jsdelivr.net/gh/guansss/pixi-live2d-display@master/test/assets/haru/"
RICE_BASE = "https://cdn.jsdelivr.net/gh/Live2D/CubismWebSamples@develop/Samples/Resources/Rice/"
# 默认模型（Haru）的表情：按每个表情文件里的参数判断的
HARU_EXPR = {"开心": "f04", "大笑": "f01", "喜欢": "f04", "得意": "f04", "期待": "f01", "生气": "f02", "哼": "f07",
             "无语": "f07", "难过": "f03", "委屈": "f03", "哭": "f03", "惊讶": "f05", "害羞": "f06", "想你": "f06",
             "撒娇": "f06", "困": "f00", "默认": "f00"}
# 自带的形象：pet.preset 选一个（没放自己的模型时用）。值：(下载地址, 入口文件, 情绪→表情, 摆放)
# 摆放：模型画布里人物的位置不一样，先按人物摆好；pet.scale / offset_y 在这个基础上再调
PRESETS = {
    "rice": (RICE_BASE, "Rice.model3.json", {},          # 金发、贝雷帽的小魔法师（没有表情，靠动作）
             {"scale": 2.1, "offset_x": -0.2, "offset_y": 60}),
    "haru": (HARU_BASE, "haru_greeter_t03.model3.json", HARU_EXPR, {"scale": 1.0, "offset_x": 0, "offset_y": 0}),
}
DEFAULT_PRESET = "rice"
CTYPES = {".js": "application/javascript", ".json": "application/json", ".png": "image/png",
          ".jpg": "image/jpeg", ".moc3": "application/octet-stream", ".moc": "application/octet-stream",
          ".mtn": "text/plain", ".wav": "audio/wav", ".mp3": "audio/mpeg"}


class PetAssets:
    """桌宠的页面、Live2D 运行库和模型（带缓存的 CDN 代理）。"""

    def __init__(self, cfg):
        c = dict(cfg.get("pet") or {})
        self.cfg = cfg
        self.enabled = bool(c.get("enabled", True))
        self.dir = cfg.root / "data" / "pet"
        self.model = str(c.get("model") or "").replace("\\", "/").strip("/") or self.find_model()
        self.preset = str(c.get("preset") or DEFAULT_PRESET).lower()
        if self.preset not in PRESETS:
            self.preset = DEFAULT_PRESET
        base, self.default_entry, self.default_expr, self.place = PRESETS[self.preset]
        self.custom_base = bool(c.get("model_base"))
        self.model_base = (c.get("model_base") or base).rstrip("/") + "/"
        self.proxy = cfg.proxy or None
        self._locks: dict[str, asyncio.Lock] = {}

    def find_model(self) -> str:
        """data/pet/model/ 里放了模型就自动用（不用改设置）。返回相对路径，没有返回空。"""
        d = self.dir / "model"
        if not d.is_dir():
            return ""
        found = sorted(list(d.rglob("*.model3.json")) + list(d.rglob("*.model.json")),
                       key=lambda p: (len(p.relative_to(d).parts), p.name))
        if not found:
            return ""
        if len(found) > 1:
            log.info("data/pet/model 里有好几个模型，先用 %s（想换就在 pet.model 里写）", found[0].relative_to(d).as_posix())
        return found[0].relative_to(d).as_posix()

    def static_routes(self, app: web.Application):
        app.router.add_get("/pet", self.page)
        app.router.add_get("/pet/", self.page)
        app.router.add_get("/pet/lib/{name}", self.lib)
        app.router.add_get(r"/pet/model/{path:.+}", self.model_file)
        app.router.add_get("/pet/sprite/{pid}/spritesheet.webp", self.sprite_file)

    # ---------------- 静态文件（带缓存的 CDN 代理） ----------------
    async def page(self, req):
        return web.Response(text=PAGE.read_text(encoding="utf-8"), content_type="text/html")

    async def _cached(self, url: str, dst: Path) -> Path:
        if dst.exists() and dst.stat().st_size > 0:
            return dst
        lock = self._locks.setdefault(str(dst), asyncio.Lock())
        async with lock:
            if dst.exists() and dst.stat().st_size > 0:
                return dst
            dst.parent.mkdir(parents=True, exist_ok=True)
            last = None
            for proxy in ((None, self.proxy) if self.proxy else (None,)):
                try:
                    async with httpx.AsyncClient(proxy=proxy, timeout=60, follow_redirects=True) as c:
                        r = await c.get(url)
                        r.raise_for_status()
                        tmp = dst.with_suffix(dst.suffix + ".part")
                        tmp.write_bytes(r.content)
                        tmp.replace(dst)
                        return dst
                except Exception as e:  # noqa: BLE001
                    last = e
            raise web.HTTPBadGateway(text=f"下载失败：{url}（{last}）")

    def _resp(self, p: Path):
        return web.FileResponse(p, headers={"Content-Type": CTYPES.get(p.suffix.lower(), "application/octet-stream"),
                                            "Cache-Control": "max-age=86400"})

    async def lib(self, req):
        name = req.match_info["name"]
        if name not in LIBS:
            raise web.HTTPNotFound()
        return self._resp(await self._cached(LIBS[name], self.dir / "lib" / name))

    async def sprite_file(self, req):
        from .pet_sprite import is_id, sprites_dir
        pid = req.match_info["pid"]
        f = sprites_dir(self.cfg.root) / pid / "spritesheet.webp"
        if not is_id(pid) or not f.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(f, headers={"Content-Type": "image/webp", "Cache-Control": "no-cache"})

    def _local_model_dir(self) -> Path:
        return self.dir / "model"

    async def model_file(self, req):
        rel = req.match_info["path"]
        if ".." in rel or rel.startswith(("/", "\\")) or ":" in rel:
            raise web.HTTPNotFound()
        local = self._local_model_dir() / rel
        if self.model and local.exists():             # 用户自己的模型
            return self._resp(local)
        cache = self.dir / "cache" / ("custom" if self.custom_base else self.preset) / re.sub(r"[^\w./-]", "_", rel)
        return self._resp(await self._cached(self.model_base + rel, cache))



class Pet(PetAssets):
    def __init__(self, cfg, brain, bot):
        super().__init__(cfg)
        c = dict(cfg.get("pet") or {})
        self.brain, self.bot, self.db = brain, bot, brain.db
        self.scale = float(c.get("scale", 1.0))
        self.offset_y = int(c.get("offset_y", 0))
        self.expr_map = dict(c.get("expressions") or ({} if self.model else self.default_expr))
        self.chatter = bool(c.get("chatter", True))
        self.chatter_gap = float(c.get("chatter_gap_minutes", 40)) * 60
        self.last_poll = 0.0
        self.queue: list[dict] = []           # 只在桌面上说的话（不发 Telegram）
        self._qid = 0

    def routes(self, app: web.Application):
        self.static_routes(app)
        self.api_routes(app, "/api/pet")
        app.router.add_get("/v1/models", self.oa_models)
        app.router.add_post("/v1/chat/completions", self.oa_chat)

    def api_routes(self, app: web.Application, prefix: str):
        """桌宠页面用的接口。"""
        app.router.add_get(f"{prefix}/config", self.get_config)
        app.router.add_post(f"{prefix}/config", self.set_config)
        app.router.add_post(f"{prefix}/chat", self.chat)
        app.router.add_get(f"{prefix}/poll", self.poll)
        app.router.add_get(f"{prefix}/prefs", self.prefs_get)
        app.router.add_post(f"{prefix}/prefs", self.prefs_set)
        app.router.add_get(f"{prefix}/sprites", self.sprites)
        app.router.add_get(f"{prefix}/sprite/gallery", self.sprite_gallery)
        app.router.add_post(f"{prefix}/sprite/use", self.sprite_use)
        app.router.add_post(f"{prefix}/sprite/remove", self.sprite_remove)

    # ---------------- 她有事告诉你时 ----------------
    ALERTS = ("bubble", "dot", "none")
    STAYS = (0, 3, 8, 30, 120)

    def prefs(self) -> dict:
        """桌宠的提醒方式：bubble＝直接冒气泡，dot＝亮个小点（鼠标经过再说），none＝什么都不做；
        stay＝气泡停留几秒（0＝看到为止）。小屋里改，存在数据库里。"""
        c = dict(self.cfg.get("pet") or {})
        p = {"alert": str(c.get("alert", "bubble")), "stay": int(c.get("stay", 8))}
        p.update(self.db.get("pet_prefs") or {})
        if p["alert"] not in self.ALERTS:
            p["alert"] = "bubble"
        return p

    async def prefs_get(self, req):
        return web.json_response(self.prefs())

    async def prefs_set(self, req):
        try:
            d = await req.json()
        except Exception:  # noqa: BLE001
            d = {}
        cur = dict(self.db.get("pet_prefs") or {})
        if d.get("alert") in self.ALERTS:
            cur["alert"] = d["alert"]
        try:
            st = int(d.get("stay"))
            if 0 <= st <= 600:
                cur["stay"] = st
        except (TypeError, ValueError):
            pass
        self.db.set("pet_prefs", cur)
        return web.json_response({"ok": True, **self.prefs()})

    def pc_state(self) -> dict:
        """电脑感知（开着的话）：他在打游戏 / 看视频（别弹气泡），或者离开电脑了（她安静下来）。"""
        pc = self.db.get("sense_pc") or {}
        if time.time() - float(pc.get("ts") or 0) > 180:
            return {"busy": False, "away": False}
        return {"busy": pc.get("kind") in ("打游戏", "看视频"), "away": pc.get("kind") == "离开电脑了"}

    # ---------------- 像素宠物（codex-pets.net） ----------------
    def sprite_id(self) -> str:
        """现在用哪只像素宠物（空＝用 Live2D）。小屋里选过的优先，其次是 pet.sprite。"""
        v = self.db.get("pet_sprite")
        if v is None:
            v = str((self.cfg.get("pet") or {}).get("sprite") or "")
        return str(v or "")

    async def sprites(self, req):
        from .pet_sprite import local_pets
        cur = self.sprite_id()
        return web.json_response({"current": cur, "local": local_pets(self.cfg.root),
                                  "live2d": self.model or self.preset})

    async def sprite_gallery(self, req):
        from .pet_sprite import PetError, gallery
        try:
            page = int(req.query.get("page") or 1)
        except ValueError:
            page = 1
        try:
            return web.json_response({"ok": True, **await gallery(req.query.get("q") or "", page,
                                                                    req.query.get("sort") or "popular",
                                                                    self.cfg.proxy or None)})
        except PetError as e:
            return web.json_response({"ok": False, "error": str(e)})

    async def sprite_use(self, req):
        """换成一只像素宠物（没下载过就先下载）；id 留空＝换回 Live2D。"""
        from .pet_sprite import PetError, download, find_local, parse_ref
        try:
            d = await req.json()
        except Exception:  # noqa: BLE001
            d = {}
        ref = str(d.get("id") or d.get("ref") or "").strip()
        if not ref:
            self.db.set("pet_sprite", "")
            return web.json_response({"ok": True, "current": "", "message": "换回 Live2D 了"})
        try:
            pid = parse_ref(ref)
            pet = find_local(self.cfg.root, pid) if not d.get("refresh") else None
            if not pet:
                pet = await download(self.cfg.root, pid, self.cfg.proxy or None)
        except PetError as e:
            return web.json_response({"ok": False, "error": str(e)})
        self.db.set("pet_sprite", pid)
        return web.json_response({"ok": True, "current": pid, "pet": pet,
                                  "message": f"换成「{pet.get('name') or pid}」了，桌宠马上会变"})

    async def sprite_remove(self, req):
        from .pet_sprite import remove_local
        try:
            d = await req.json()
        except Exception:  # noqa: BLE001
            d = {}
        pid = str(d.get("id") or "")
        if pid and pid == self.sprite_id():
            self.db.set("pet_sprite", "")
        return web.json_response({"ok": remove_local(self.cfg.root, pid)})

    async def _sprite_config(self) -> dict | None:
        pid = self.sprite_id()
        if not pid:
            return None
        from .pet_sprite import PetError, download, find_local
        pet = find_local(self.cfg.root, pid)
        if not pet:                                   # 设置里写了，但还没下载：现在下
            try:
                pet = await download(self.cfg.root, pid, self.cfg.proxy or None)
            except PetError as e:
                log.warning("像素宠物 %s 下载失败，先用 Live2D：%s", pid, e)
                return None
        c = dict(self.cfg.get("pet") or {})
        return {"kind": "sprite", "id": pid, "sheet": f"/pet/sprite/{pid}/spritesheet.webp",
                "version": pet.get("version", 2), "pet_name": pet.get("name"), "author": pet.get("author"),
                "scale": float(c.get("sprite_scale", 1.0)), "walk": bool(c.get("walk", True)),
                "look": bool(c.get("look", True))}

    # ---------------- 配置 ----------------
    async def get_config(self, req):
        sp = await self._sprite_config()
        if sp:
            return web.json_response({**sp, "skin": sp["id"], "prefs": self.prefs(), "name": self.brain.persona.name,
                                      "ta": getattr(self.brain.persona, "ta", "她")})
        entry = self.model or self.default_entry
        place = {"scale": 1.0, "offset_x": 0, "offset_y": 0} if self.model else self.place
        return web.json_response({"kind": "live2d", "skin": self.sprite_id(), "prefs": self.prefs(), "model": f"/pet/model/{entry}", "scale": self.scale * place["scale"],
                                  "offset_x": place["offset_x"], "offset_y": self.offset_y + place["offset_y"],
                                  "expr": self.expr(),
                                  "name": self.brain.persona.name, "ta": getattr(self.brain.persona, "ta", "她")})

    async def set_config(self, req):
        return web.json_response({"ok": True})          # 以前用来存“静音”，语音去掉后没有要存的了

    # ---------------- 聊天 ----------------
    async def chat(self, req):
        try:
            d = await req.json()
        except Exception:  # noqa: BLE001
            d = {}
        text = str(d.get("text") or "").strip()[:500]
        if not text:
            return web.json_response({"error": "说点什么吧"})
        try:
            r = await self.bot.desk_chat(text)
        except Exception as e:  # noqa: BLE001
            log.exception("桌宠聊天失败")
            return web.json_response({"error": f"她走神了：{str(e)[:80]}"})
        emo = next((b["emotion"] for b in r.get("bubbles", []) if b.get("emotion")), None)
        r["expr"] = self.expr(emo)
        r["emotion"] = emo or ""
        return web.json_response(r)

    # ---------------- 表情 ----------------
    def expr(self, emotion: str | None = None) -> str | None:
        """按她的情绪挑模型表情：先看这次的表情包情绪，再看心情文字，最后看情绪数值。"""
        if not self.expr_map:
            return None
        cands = [emotion] if emotion else []
        mood = self.brain.mood or ""
        cands += [k for k in self.expr_map if k in mood]
        cands.append(self.brain.emotion.mood_style())
        for c in cands:
            if c and c in self.expr_map:
                return self.expr_map[c]
        return self.expr_map.get("默认")

    # ---------------- 桌面上主动搭话 ----------------
    def active(self) -> bool:
        return time.time() - self.last_poll < 30

    async def on_pc_event(self, text: str):
        """电脑感知到“他刚回到电脑前 / 刚打完游戏 / 换了首歌”：桌宠开着的话，她在桌面上搭一句（不发 Telegram）。"""
        if not (self.enabled and self.chatter and self.active()):
            return
        last = float(self.db.get("pet_chatter_ts", 0) or 0)
        if time.time() - last < self.chatter_gap:
            return
        from .proactive import in_quiet
        if in_quiet(self.brain.now(), self.cfg.proactive.quiet_hours):
            return
        if "在听" in text and time.time() - last < 3 * self.chatter_gap:
            return                                # 换歌不用每次都说
        self.db.set("pet_chatter_ts", time.time())
        try:
            line = await self.brain.desk_line(text)
        except Exception as e:  # noqa: BLE001
            log.info("桌宠搭话失败：%s", e)
            return
        if line:
            self._qid += 1
            self.queue.append({"qid": self._qid, "text": line, "ts": time.time()})
            self.queue = self.queue[-5:]

    async def poll(self, req):
        """她在别处（Telegram）发的新消息，桌宠头上也冒个气泡。"""
        try:
            since = int(req.query.get("since") or 0)
        except ValueError:
            since = 0
        last = self.db.conn.execute("SELECT MAX(id) m FROM messages").fetchone()["m"] or 0
        rows = self.db.conn.execute(
            "SELECT * FROM messages WHERE id>? AND role='assistant' AND kind NOT IN ('desk') ORDER BY id",
            (since,)).fetchall() if since else []
        msgs = [{"id": r["id"], "text": r["content"]} for r in rows
                if r["kind"] in ("text", "proactive", "voice", "letter", "song")][-3:]
        self.last_poll = time.time()
        try:
            qsince = int(req.query.get("q") or 0)
        except ValueError:
            qsince = 0
        desk = [x for x in self.queue if x["qid"] > qsince and time.time() - x["ts"] < 120]
        return web.json_response({"last_id": last, "messages": msgs, "style": self.brain.emotion.mood_style(),
                                  "mood": self.brain.mood, "expr": self.expr(), "desk": desk,
                                  "qid": self._qid, "skin": self.sprite_id(), "prefs": self.prefs(),
                                  "pc": self.pc_state()})

    # ---------------- OpenAI 兼容接口（给别的桌宠程序用） ----------------
    async def oa_models(self, req):
        return web.json_response({"object": "list", "data": [{"id": "ailife", "object": "model", "owned_by": "ailife"}]})

    async def oa_chat(self, req):
        try:
            d = await req.json()
        except Exception:  # noqa: BLE001
            raise web.HTTPBadRequest()
        msgs = [m for m in (d.get("messages") or []) if isinstance(m, dict)]
        last = next((m for m in reversed(msgs) if m.get("role") == "user"), None)
        content = (last or {}).get("content") or ""
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        content = str(content).strip()
        if not content:
            raise web.HTTPBadRequest(text="没有用户消息")
        r = await self.bot.desk_chat(content[:500])
        text = "\n".join(b["text"] for b in r.get("bubbles", []) if b.get("text")) or "……"
        cid, now = f"chatcmpl-{int(time.time() * 1000)}", int(time.time())
        if d.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
            await resp.prepare(req)
            for piece in (text[i:i + 24] for i in range(0, len(text), 24)):
                chunk = {"id": cid, "object": "chat.completion.chunk", "created": now, "model": "ailife",
                         "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                await resp.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            end = {"id": cid, "object": "chat.completion.chunk", "created": now, "model": "ailife",
                   "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            await resp.write(f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode())
            return resp
        return web.json_response({"id": cid, "object": "chat.completion", "created": now, "model": "ailife",
                                  "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                               "finish_reason": "stop"}],
                                  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})


# ---------------- 桌面小窗口 ----------------
def win_env(own_hwnd: int = 0) -> tuple[bool, float]:
    """（Windows）有没有程序全屏（游戏、看视频），以及键盘鼠标多久没动了（秒）。别的系统返回 (False, 0)。"""
    if sys.platform != "win32":
        return False, 0.0
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
        li = LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(li)
        idle = 0.0
        if u.GetLastInputInfo(ctypes.byref(li)):
            idle = ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0
        hwnd = u.GetForegroundWindow()
        if not hwnd or hwnd == own_hwnd or hwnd in (u.GetDesktopWindow(), u.GetShellWindow()):
            return False, idle
        buf = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, buf, 64)
        if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False, idle

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        u.GetMonitorInfoW(u.MonitorFromWindow(hwnd, 2), ctypes.byref(mi))
        m = mi.rcMonitor
        full = r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom
        return bool(full), idle
    except Exception:  # noqa: BLE001
        return False, 0.0



class _Api:
    """给桌宠页面的 JS 用的接口。属性名以 _ 开头，pywebview 就不会把它们暴露给页面。"""

    def __init__(self):
        self._window = None
        self._home = ""

    def hide(self):
        if self._window:
            self._window.minimize()

    def open_home(self):
        import webbrowser
        webbrowser.open(self._home)


QT_BRIDGE = """
new QWebChannel(qt.webChannelTransport, function (ch) {
  window.pywebview = {api: ch.objects.pet};
  window.__qtpet = true;
  window.dispatchEvent(new Event("pywebviewready"));
});
"""


def _tray_icon_pixmap(size: int = 64):
    """托盘小图标：桌宠文件夹里有 icon.png 就用它，没有就画一个粉色圆点 + 心形。"""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#e7849f"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, size - 4, size - 4)
    path = QPainterPath()
    c = size / 2
    path.moveTo(QPointF(c, size * 0.74))
    path.cubicTo(QPointF(size * 0.12, size * 0.46), QPointF(size * 0.3, size * 0.16), QPointF(c, size * 0.34))
    path.cubicTo(QPointF(size * 0.7, size * 0.16), QPointF(size * 0.88, size * 0.46), QPointF(c, size * 0.74))
    p.setBrush(QColor("#ffffff"))
    p.drawPath(path)
    p.end()
    return pm


def run_qt(url: str, home: str, w: int, h: int, on_top: bool, title: str, icon_path: Path | None = None,
           hide_fullscreen: bool = True) -> bool:
    """真正透明（只看得到人物，没有方框）、不占任务栏的小窗口，右下角托盘里有图标。需要 PySide6。"""
    try:
        from PySide6.QtCore import QFile, QIODevice, QObject, Qt, QUrl, Slot
        from PySide6.QtGui import QAction, QGuiApplication, QIcon, QPixmap
        from PySide6.QtWebChannel import QWebChannel
        from PySide6.QtWebEngineCore import QWebEngineScript
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    except ImportError:
        return False
    import webbrowser
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(title)
    view = QWebEngineView()
    flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint      # Tool：不在任务栏出现
    if on_top:
        flags |= Qt.WindowStaysOnTopHint
    view.setWindowFlags(flags)
    view.setAttribute(Qt.WA_TranslucentBackground, True)
    view.setStyleSheet("background: transparent")
    view.page().setBackgroundColor(Qt.transparent)
    view.setWindowTitle(title)
    view.resize(w, h)
    geo = QGuiApplication.primaryScreen().availableGeometry()
    view.move(geo.right() - w - 16, geo.bottom() - h - 8)

    class Bridge(QObject):
        @Slot()
        def hide(self):
            view.hide()

        @Slot()
        def open_home(self):
            webbrowser.open(home)

        @Slot()
        def start_drag(self):
            wh = view.windowHandle()
            if wh is not None:
                wh.startSystemMove()

        @Slot(int, int)
        def walk(self, dx, ms):
            """像素宠物空闲时沿着屏幕走几步：窗口在 ms 毫秒里横着挪 dx 像素（不走出屏幕）。"""
            from PySide6.QtCore import QPoint, QPropertyAnimation
            g = (view.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            x = max(g.left(), min(g.right() - view.width(), view.x() + int(dx)))
            anim = QPropertyAnimation(view, b"pos", view)
            anim.setDuration(max(100, int(ms)))
            anim.setStartValue(view.pos())
            anim.setEndValue(QPoint(x, view.y()))
            anim.start()
            self._anim = anim

        @Slot()
        def walk_stop(self):
            a = getattr(self, "_anim", None)
            if a is not None:
                a.stop()

    bridge = Bridge()
    channel = QWebChannel(view.page())
    channel.registerObject("pet", bridge)
    view.page().setWebChannel(channel)
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.ReadOnly):
        js = bytes(f.readAll()).decode("utf-8") + QT_BRIDGE
        f.close()
        sc = QWebEngineScript()
        sc.setName("ailife-bridge")
        sc.setSourceCode(js)
        sc.setInjectionPoint(QWebEngineScript.DocumentCreation)
        sc.setWorldId(QWebEngineScript.MainWorld)
        sc.setRunsOnSubFrames(False)
        view.page().scripts().insert(sc)
    view.load(QUrl(url))
    view.show()

    # 鼠标在屏幕上的位置告诉页面（像素宠物的眼睛跟着鼠标转）；没动就不发
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QCursor
    last_cursor = {"p": None}

    def push_cursor():
        if not view.isVisible():
            return
        p = QCursor.pos()
        if p == last_cursor["p"]:
            return
        last_cursor["p"] = p
        c = view.geometry().center()
        view.page().runJavaScript(f"window.__petCursor&&window.__petCursor({p.x() - c.x()},{p.y() - c.y()})")
    cursor_timer = QTimer(view)
    cursor_timer.timeout.connect(push_cursor)
    cursor_timer.start(120)

    # 有程序全屏时让开（藏起来，退出全屏再回来）；告诉页面全屏 / 你多久没碰电脑，她好决定要不要说话、要不要安静
    env = {"hidden_by_fs": False}

    def push_env():
        try:
            own = int(view.winId())
        except Exception:  # noqa: BLE001
            own = 0
        fs, idle = win_env(own)
        if fs and hide_fullscreen and view.isVisible():
            view.hide()
            env["hidden_by_fs"] = True
        elif not fs and env["hidden_by_fs"]:
            env["hidden_by_fs"] = False
            view.show()
        view.page().runJavaScript(f"window.__petEnv&&window.__petEnv({'true' if fs else 'false'},{idle:.0f})")
    env_timer = QTimer(view)
    env_timer.timeout.connect(push_env)
    env_timer.start(2000)

    pm = QPixmap(str(icon_path)) if icon_path and icon_path.exists() else _tray_icon_pixmap()
    tray = QSystemTrayIcon(QIcon(pm), app)
    tray.setToolTip(f"{title}（桌宠）")
    menu = QMenu()

    def toggle():
        if view.isVisible():
            view.hide()
        else:
            view.show()
            view.raise_()
    act_show = QAction("显示 / 收起", menu)
    act_show.triggered.connect(toggle)
    act_home = QAction("打开小屋", menu)
    act_home.triggered.connect(lambda: webbrowser.open(home))
    act_top = QAction("总在最前", menu, checkable=True, checked=on_top)

    def set_top(on):
        vis = view.isVisible()
        view.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        if vis:
            view.show()
    act_top.toggled.connect(set_top)
    act_quit = QAction("关掉桌宠", menu)
    act_quit.triggered.connect(app.quit)
    for a in (act_show, act_home, act_top):
        menu.addAction(a)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda r: toggle() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
    tray.show()
    app.exec()
    return True


def run_window(cfg, auto: bool = False):
    """打开桌宠窗口（阻塞，直到关掉）。优先用 PySide6（真透明、不占任务栏、托盘图标），
    没有就用 pywebview（Windows 上背景透明不了，改成浅色背景），都没有就用浏览器打开。"""
    port = int((cfg.get("web") or {}).get("port", 8765))
    c = dict(cfg.get("pet") or {})
    import time as _t
    deadline = _t.time() + (90 if auto else 10)
    while _t.time() < deadline:            # 等主程序的网页先起来
        try:
            httpx.get(f"http://127.0.0.1:{port}/api/pet/config", timeout=2)
            break
        except Exception:  # noqa: BLE001
            _t.sleep(2)
    else:
        if auto:
            return
        print("主程序好像没在运行（先双击 start.bat）")
    w, h = int(c.get("width", 320)), int(c.get("height", 480))
    url = f"http://127.0.0.1:{port}/pet"
    home = f"http://127.0.0.1:{port}/"
    title = str(c.get("title") or "桌宠")
    transparent = bool(c.get("transparent", True))
    on_top = bool(c.get("on_top", True))
    root = Path(cfg.get("root") or Path(__file__).resolve().parent.parent)
    if run_qt(url + ("" if transparent else "?solid=1"), home, w, h, on_top, title,
              root / "data" / "pet" / "icon.png", bool(c.get("hide_fullscreen", True))):
        return
    try:
        import webview  # pywebview
    except ImportError:
        if auto:
            return
        import webbrowser
        print("没装 PySide6 / pywebview，用浏览器打开桌宠页面（运行一次 pet_setup.bat 就有透明小窗口了）")
        webbrowser.open(url + "?solid=1")
        return
    log.info("没装 PySide6：桌宠用 pywebview 打开（Windows 上没法真透明；运行 pet_setup.bat 装好后就没有方框了）")
    x = y = None
    try:
        s = webview.screens[0]
        x, y = s.width - w - 24, s.height - h - 64
    except Exception:  # noqa: BLE001
        pass
    api = _Api()
    api._home = home
    win = webview.create_window(title, url + "?solid=1", js_api=api,
                                width=w, height=h, x=x, y=y, frameless=True, easy_drag=True,
                                on_top=on_top, background_color="#F3EEFC", resizable=True)
    api._window = win
    webview.start(private_mode=False)


def spawn_window(root: Path) -> subprocess.Popen | None:
    """主程序启动后，另开一个进程显示桌宠（pywebview 需要占用主线程）。"""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        exe = sys.executable
        if sys.platform == "win32":                  # 用 pythonw：不弹黑窗口
            pw = Path(exe).with_name("pythonw.exe")
            if pw.exists():
                exe = str(pw)
        return subprocess.Popen([exe, str(root / "run.py"), "pet", "--auto"], cwd=str(root),
                                creationflags=flags)
    except Exception as e:  # noqa: BLE001
        log.warning("桌宠窗口没打开：%s", e)
        return None
