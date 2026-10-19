"""桌宠的窗口：PySide6 真透明、置顶、不占任务栏的小窗口（托盘里有图标），没有就退回 pywebview / 浏览器。

窗口里显示的是 http://127.0.0.1:<port>/pet（页面和接口在 ailife/pet/server.py）。这里还负责：
- 把全屏的鼠标位置推给页面（像素宠物的眼睛跟着转），让窗口“走几步”
- 每 2 秒告诉页面有没有程序全屏、你多久没碰电脑；有程序全屏时先藏起来
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import httpx

log = logging.getLogger("ailife.pet")


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
    root = Path(cfg.get("root") or Path(__file__).resolve().parents[2])
    if run_qt(url + ("" if transparent else "?solid=1"), home, w, h, on_top, title,
              root / "data" / "pet" / "icon.png", bool(c.get("hide_fullscreen", True))):
        return
    try:
        import webview  # pywebview
    except ImportError:
        if auto:
            return
        import webbrowser
        print("没装 PySide6 / pywebview，用浏览器打开桌宠页面（运行一次 scripts\\pet_setup.bat 就有透明小窗口了）")
        webbrowser.open(url + "?solid=1")
        return
    log.info("没装 PySide6：桌宠用 pywebview 打开（Windows 上没法真透明；运行 scripts\\pet_setup.bat 装好后就没有方框了）")
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
