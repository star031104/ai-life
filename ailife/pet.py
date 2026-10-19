"""桌面上的她：Live2D 桌宠，和 Telegram 里的她共用同一个大脑、记忆和心情。

- 网页 http://127.0.0.1:8765/pet ，由 `python run.py pet` 打开成一个真透明、置顶、无边框、不占任务栏的小窗口
  （PySide6；右下角托盘有图标可以显示 / 收起 / 关掉），没装 PySide6 就退回 pywebview（Windows 上有浅色底）或浏览器。
- 在桌宠里打字和她聊，她的话冒在头顶的气泡里；她在 Telegram 上主动找你时，桌宠头上也会冒气泡。
- Live2D 的运行库和模型第一次用时从 CDN 下载并缓存到 data/pet/，以后不联网也能用。
  默认模型是 Live2D 官方示例模型（Haru）；想换成自己的模型，把整个模型文件夹放进 data/pet/model/ 就行，
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
DEFAULT_MODEL_BASE = "https://cdn.jsdelivr.net/gh/guansss/pixi-live2d-display@master/test/assets/haru/"
DEFAULT_MODEL = "haru_greeter_t03.model3.json"
# 默认模型（Haru）的表情：按每个表情文件里的参数判断的
HARU_EXPR = {"开心": "f04", "大笑": "f01", "喜欢": "f04", "得意": "f04", "期待": "f01", "生气": "f02", "哼": "f07",
             "无语": "f07", "难过": "f03", "委屈": "f03", "哭": "f03", "惊讶": "f05", "害羞": "f06", "想你": "f06",
             "撒娇": "f06", "困": "f00", "默认": "f00"}
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
        self.model_base = (c.get("model_base") or DEFAULT_MODEL_BASE).rstrip("/") + "/"
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

    def _local_model_dir(self) -> Path:
        return self.dir / "model"

    async def model_file(self, req):
        rel = req.match_info["path"]
        if ".." in rel or rel.startswith(("/", "\\")) or ":" in rel:
            raise web.HTTPNotFound()
        local = self._local_model_dir() / rel
        if self.model and local.exists():             # 用户自己的模型
            return self._resp(local)
        cache = self.dir / "cache" / re.sub(r"[^\w./-]", "_", rel)
        return self._resp(await self._cached(self.model_base + rel, cache))



class Pet(PetAssets):
    def __init__(self, cfg, brain, bot):
        super().__init__(cfg)
        c = dict(cfg.get("pet") or {})
        self.brain, self.bot, self.db = brain, bot, brain.db
        self.scale = float(c.get("scale", 1.0))
        self.offset_y = int(c.get("offset_y", 0))
        self.expr_map = dict(c.get("expressions") or ({} if self.model else HARU_EXPR))
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

    # ---------------- 配置 ----------------
    async def get_config(self, req):
        entry = self.model or DEFAULT_MODEL
        return web.json_response({"model": f"/pet/model/{entry}", "scale": self.scale, "offset_y": self.offset_y,
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
                                  "qid": self._qid})

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


def run_qt(url: str, home: str, w: int, h: int, on_top: bool, title: str, icon_path: Path | None = None) -> bool:
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
              root / "data" / "pet" / "icon.png"):
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
