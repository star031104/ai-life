"""感知你此刻在做什么（都可以在 config.yaml 的 sense 里关掉）。

电脑（只在 Windows 上）：每 30 秒看一眼前台窗口是哪个程序、多久没碰键鼠、系统里正在放的歌。
    在本机认出“在玩《原神》/ 用 VS Code 写代码 / 在 B站看视频 / 刷知乎…”，记下今天每样做了多久。
    sense.pc.detail 决定告诉她多少：app（默认）＝只说程序、游戏、网站名；content＝再加视频标题、项目名、文档名；
    full＝原始窗口标题。无痕窗口只说“在上网”，sense.pc.ignore 里的程序一律只说“在用电脑”。
手机：手机上的快捷指令（iPhone）或 MacroDroid（安卓）在“闹钟关掉 / 到家 / 开始充电”时
    往 ntfy.sh 的一个私密频道发一句话，这里订阅那个频道。频道名在 /sense 命令里能看到。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import sys
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger("ailife.sense")

GAMES = ("league of legends", "leagueclient", "valorant", "cs2.exe", "csgo", "dota2", "yuanshen", "genshinimpact",
         "starrail", "zenlesszonezero", "overwatch", "eldenring", "tslgame", "pubg", "minecraft", "r5apex",
         "gta5", "bg3", "narakabladepoint", "monsterhunter", "b1-win64", "palworld", "client-win64-shipping",
         "wutheringwaves", "honkai", "hearthstone", "wow.exe", "diablo", "steam_app", "\\steamapps\\common\\",
         "arknights", "bluearchive", "deltaforce", "delta force", "marvelrivals", "fortnite", "apex")
KINDS = [
    ("写代码", ("code.exe", "cursor.exe", "pycharm", "idea64", "devenv.exe", "clion", "webstorm", "goland",
              "sublime_text", "windowsterminal", "wt.exe", "powershell", "cmd.exe")),
    ("看视频", ("potplayer", "vlc.exe", "mpv.exe", "bilibili", "哔哩哔哩", "youtube", "爱奇艺", "腾讯视频", "优酷",
              "netflix", "dandanplay", "弹弹play")),
    ("听歌", ("cloudmusic", "qqmusic", "spotify", "kugou", "kwmusic")),
    ("聊天", ("wechat.exe", "weixin.exe", "qq.exe", "discord", "dingtalk", "feishu", "lark")),
    ("和你聊天", ("telegram.exe", "telegram desktop")),
    ("学习/办公", ("winword", "excel", "powerpnt", "wps", "acrobat", "sumatrapdf", "notion", "obsidian", "zotero",
               "onenote", "typora", ".pdf")),
    ("刷网页", ("chrome.exe", "msedge.exe", "firefox.exe", "360se", "qqbrowser")),
]


# ---------------- 认出是哪个程序 / 哪个网站（都在本机判断） ----------------
# (关键词, 名字, 在干嘛)。关键词匹配“程序路径 + 窗口标题”的小写。
APPS = [
    (("yuanshen", "genshinimpact"), "原神", "打游戏"),
    (("starrail",), "崩坏：星穹铁道", "打游戏"),
    (("zenlesszonezero",), "绝区零", "打游戏"),
    (("wutheringwaves",), "鸣潮", "打游戏"),
    (("leagueclient", "league of legends"), "英雄联盟", "打游戏"),
    (("valorant",), "无畏契约", "打游戏"),
    (("cs2.exe", "csgo"), "CS2", "打游戏"),
    (("dota2",), "Dota 2", "打游戏"),
    (("overwatch",), "守望先锋", "打游戏"),
    (("eldenring",), "艾尔登法环", "打游戏"),
    (("tslgame", "pubg"), "PUBG", "打游戏"),
    (("minecraft",), "我的世界", "打游戏"),
    (("r5apex",), "Apex 英雄", "打游戏"),
    (("gta5",), "GTA5", "打游戏"),
    (("bg3",), "博德之门3", "打游戏"),
    (("narakabladepoint",), "永劫无间", "打游戏"),
    (("b1-win64",), "黑神话：悟空", "打游戏"),
    (("palworld",), "幻兽帕鲁", "打游戏"),
    (("hearthstone",), "炉石传说", "打游戏"),
    (("wow.exe",), "魔兽世界", "打游戏"),
    (("arknights",), "明日方舟", "打游戏"),
    (("bluearchive",), "蔚蓝档案", "打游戏"),
    (("deltaforce", "delta force"), "三角洲行动", "打游戏"),
    (("marvelrivals",), "漫威争锋", "打游戏"),
    (("fortnite",), "堡垒之夜", "打游戏"),
    (("monsterhunter",), "怪物猎人", "打游戏"),
    (("honkai",), "崩坏", "打游戏"),
    (("diablo",), "暗黑破坏神", "打游戏"),
    (("code.exe",), "VS Code", "写代码"),
    (("cursor.exe",), "Cursor", "写代码"),
    (("pycharm",), "PyCharm", "写代码"),
    (("idea64",), "IntelliJ IDEA", "写代码"),
    (("devenv.exe",), "Visual Studio", "写代码"),
    (("clion",), "CLion", "写代码"),
    (("webstorm",), "WebStorm", "写代码"),
    (("goland",), "GoLand", "写代码"),
    (("sublime_text",), "Sublime Text", "写代码"),
    (("windowsterminal", "wt.exe", "powershell", "cmd.exe"), "终端", "写代码"),
    (("potplayer",), "PotPlayer", "看视频"),
    (("vlc.exe",), "VLC", "看视频"),
    (("mpv.exe",), "mpv", "看视频"),
    (("dandanplay", "弹弹play"), "弹弹play", "看视频"),
    (("哔哩哔哩.exe", "bilibili.exe"), "B站", "看视频"),
    (("cloudmusic",), "网易云音乐", "听歌"),
    (("qqmusic",), "QQ音乐", "听歌"),
    (("spotify",), "Spotify", "听歌"),
    (("kugou",), "酷狗", "听歌"),
    (("kwmusic",), "酷我", "听歌"),
    (("wechat.exe", "weixin.exe"), "微信", "聊天"),
    (("\\qq.exe", "qqnt"), "QQ", "聊天"),
    (("discord",), "Discord", "聊天"),
    (("dingtalk",), "钉钉", "聊天"),
    (("feishu", "lark"), "飞书", "聊天"),
    (("telegram.exe", "telegram desktop"), "Telegram", "和你聊天"),
    (("winword",), "Word", "学习/办公"),
    (("excel.exe",), "Excel", "学习/办公"),
    (("powerpnt",), "PPT", "学习/办公"),
    (("wps",), "WPS", "学习/办公"),
    (("acrobat", "sumatrapdf"), "PDF 阅读器", "学习/办公"),
    (("notion",), "Notion", "学习/办公"),
    (("obsidian",), "Obsidian", "学习/办公"),
    (("zotero",), "Zotero", "学习/办公"),
    (("onenote",), "OneNote", "学习/办公"),
    (("typora",), "Typora", "学习/办公"),
]
BROWSERS = ("chrome.exe", "msedge.exe", "firefox.exe", "360se", "360chrome", "qqbrowser", "sogouexplorer", "opera",
            "brave.exe", "vivaldi")
# 浏览器里：(标题里的关键词, 网站名, 在干嘛, 标题尾巴要去掉的部分)
SITES = [
    (("哔哩哔哩", "bilibili"), "B站", "看视频", r"[_\-–| ]*哔哩哔哩.*$"),
    (("youtube",), "YouTube", "看视频", r"\s*[-–|]\s*YouTube.*$"),
    (("爱奇艺", "iqiyi"), "爱奇艺", "看视频", r"[_\-–| ]*爱奇艺.*$"),
    (("腾讯视频",), "腾讯视频", "看视频", r"[_\-–| ]*腾讯视频.*$"),
    (("优酷",), "优酷", "看视频", r"[_\-–| ]*优酷.*$"),
    (("netflix",), "Netflix", "看视频", r"\s*[-–|]\s*Netflix.*$"),
    (("抖音",), "抖音", "看视频", r"[_\-–| ]*抖音.*$"),
    (("知乎",), "知乎", "刷网页", r"\s*[-–|]\s*知乎.*$"),
    (("小红书",), "小红书", "刷网页", r"\s*[-–|]\s*小红书.*$"),
    (("微博",), "微博", "刷网页", r"[_\-–| ]*微博.*$"),
    (("贴吧",), "贴吧", "刷网页", r"[_\-–| ]*百度贴吧.*$"),
    (("github",), "GitHub", "写代码", r"\s*[-–·|]\s*GitHub.*$"),
    (("stack overflow",), "Stack Overflow", "写代码", r"\s*[-–|]\s*Stack Overflow.*$"),
    (("csdn",), "CSDN", "写代码", r"[_\-–| ]*CSDN.*$"),
    (("leetcode", "力扣"), "力扣", "写代码", r"\s*[-–|]\s*(力扣|LeetCode).*$"),
    (("chatgpt",), "ChatGPT", "刷网页", r"$^"),
    (("claude",), "Claude", "刷网页", r"$^"),
    (("淘宝", "天猫"), "淘宝", "网购", r"[_\-–| ]*(淘宝|天猫).*$"),
    (("京东",), "京东", "网购", r"[_\-–| ]*京东.*$"),
    (("拼多多",), "拼多多", "网购", r"[_\-–| ]*拼多多.*$"),
    (("steam",), "Steam", "刷网页", r"$^"),
]
BROWSER_TAIL = re.compile(r"\s*[-–—]\s*(个人\s*[-–—]\s*)?(Google Chrome|Microsoft\s*\S*\s*Edge|Mozilla Firefox|Firefox|"
                          r"360安全浏览器|360极速浏览器|QQ浏览器|搜狗高速浏览器|Opera|Brave|Vivaldi)\s*$", re.I)
BLANK_PAGES = ("new tab", "新标签页", "新建标签页", "空白页", "about:blank", "")
PRIVATE = re.compile(r"InPrivate|无痕|隐身|Private Browsing", re.I)
GAME_PATHS = ("\\steamapps\\common\\", "steam_app", "client-win64-shipping", "\\epic games\\", "\\wegame\\")
VIDEO_EXT = re.compile(r"\.(mkv|mp4|avi|flv|mov|wmv|webm|ts|rmvb)\b.*$", re.I)


@dataclass
class Activity:
    kind: str                     # 打游戏 / 写代码 / 看视频 / 听歌 / 聊天 / 和你聊天 / 学习/办公 / 刷网页 / 网购 / 用电脑
    app: str = ""                 # 认出来的程序 / 游戏 / 网站名（本机判断，不含窗口标题）
    content: str = ""             # 更具体的内容（视频标题、项目名、文档名），detail=content 才有
    web: bool = False             # 是浏览器里的网站

    def text(self) -> str:
        """一句话：在玩《原神》/ 在用 VS Code 写代码（项目 ai-life）/ 在 B站看《xxx》"""
        a, c, k = self.app, self.content, self.kind
        if k == "打游戏":
            return f"在玩《{a}》" if a else "在打游戏"
        if k == "看视频":
            return f"在{a or ''}看{'《' + c + '》' if c else '视频'}"
        if k == "写代码" and self.web:
            return f"在{a}上查代码相关的东西" + (f"（{c}）" if c else "")
        if k == "写代码":
            return f"在用 {a} 写代码" + (f"（{c}）" if c else "") if a else "在写代码"
        if k == "学习/办公":
            return f"在用 {a}" + (f" 弄「{c}」" if c else "") if a else "在学习 / 办公"
        if k == "刷网页":
            return f"在刷{a}" + (f"（{c}）" if c else "") if a else "在上网" + (f"（{c}）" if c else "")
        if k == "网购":
            return f"在{a or '网上'}逛" + (f"（{c}）" if c else "")
        if k == "聊天":
            return f"在{a or '别的软件'}上和别人聊天"
        if k == "听歌":
            return f"开着{a}听歌" if a else "在听歌"
        if k == "和你聊天":
            return "在电脑上和你聊天"
        return "在用电脑" + (f"（{a}）" if a else "")


def _clean(s: str, n: int = 40) -> str:
    s = re.sub(r"^\(\d+\)\s*", "", s or "").strip(" -–—_|·")
    return s[:n]


def recognize(exe: str, title: str, detail: str = "app", ignore: tuple = ()) -> Activity:
    """前台窗口 → 在干嘛。detail：app＝只认程序/游戏/网站；content＝再加视频标题、项目名、文档名；full＝原始窗口标题。"""
    exe = exe or ""
    title = title or ""
    s = f"{exe} {title}".lower()
    if any(k and k.lower() in s for k in ignore):
        return Activity("用电脑")
    exe_l = exe.lower().replace("/", "\\")
    is_browser = any(b in exe_l for b in BROWSERS)
    for keys, name, kind in APPS if not is_browser else ():   # 只看程序路径（窗口标题里的字不算，免得文档名里有“minecraft”就算打游戏）
        if any(k in exe_l for k in keys):
            act = Activity(kind, name)
            if detail in ("content", "full"):
                act.content = _content(name, title)
            if detail == "full" and title:
                act.content = _clean(title, 60)
            return act
    if any(p in exe_l for p in GAME_PATHS):          # Steam / Epic / WeGame 里的游戏：窗口标题一般就是游戏名
        return Activity("打游戏", _clean(title, 24) if title and not PRIVATE.search(title) else "")
    if is_browser:
        page = BROWSER_TAIL.sub("", title).strip()
        if PRIVATE.search(title):
            return Activity("刷网页")
        for keys, name, kind, tail in SITES:
            if any(k in page.lower() for k in keys):
                act = Activity(kind, name, web=True)
                if detail == "content":
                    act.content = _clean(re.sub(tail, "", page, flags=re.I))
                    if act.content.lower() in (name.lower(), "首页", "") or len(act.content) < 2:
                        act.content = ""
                elif detail == "full":
                    act.content = _clean(page, 60)
                return act
        act = Activity("刷网页", web=True)
        if detail in ("content", "full") and page.lower() not in BLANK_PAGES:
            act.content = _clean(page, 40 if detail == "content" else 60)
        return act
    act = Activity(classify_kind(s))
    if detail in ("content", "full"):
        stem = exe_l.split("\\")[-1].removesuffix(".exe")
        act.app = stem[:20] if stem and stem not in ("explorer", "applicationframehost") else ""
        if detail == "full" and title:
            act.content = _clean(title, 60)
    return act


def _content(app: str, title: str) -> str:
    """程序窗口标题里比较具体又不太私密的部分。"""
    t = title or ""
    if app == "VS Code" or app == "Cursor":           # file.py - project - Visual Studio Code
        parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", t) if p.strip()]
        if len(parts) >= 3:
            return f"项目 {_clean(parts[-2], 30)}"
        return ""
    if app in ("PyCharm", "IntelliJ IDEA", "CLion", "WebStorm", "GoLand"):   # project – file.py
        parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", t) if p.strip()]
        return f"项目 {_clean(parts[0], 30)}" if len(parts) >= 2 else ""
    if app in ("PotPlayer", "VLC", "mpv", "弹弹play"):
        name = re.split(r"\s+[-–—]\s+(PotPlayer|VLC|mpv|弹弹play).*$", t, flags=re.I)[0]
        name = VIDEO_EXT.sub("", name)
        return _clean(name, 40) if name.lower() not in ("potplayer", "vlc media player", "mpv", "") else ""
    if app in ("Word", "Excel", "PPT", "WPS", "PDF 阅读器", "Typora", "Obsidian", "Notion"):
        name = re.split(r"\s+[-–—]\s+", t)[0]
        name = re.sub(r"\.(docx?|xlsx?|pptx?|pdf|md)\s*$", "", name, flags=re.I)
        return _clean(name, 30) if name and name.lower() not in (app.lower(),) else ""
    return ""


def classify_kind(s: str) -> str:
    s = s.lower()
    if any(g in s for g in GAMES):
        return "打游戏"
    for kind, keys in KINDS:
        if any(k in s for k in keys):
            return kind
    return "用电脑"


def classify(exe: str, title: str) -> str:
    """只要“在干嘛”这一类（兼容旧代码）。"""
    return recognize(exe, title).kind


def fmt_dur(sec: float) -> str:
    m = sec / 60
    if m < 60:
        return f"{m:.0f} 分钟"
    h = m / 60
    return f"{h:.1f} 小时".replace(".0 小时", " 小时")


def day_summary(day: dict | None, top: int = 4) -> str:
    """“VS Code 写代码 3.2 小时、《原神》1.5 小时、B站看视频 40 分钟”（不到 10 分钟的不提）。"""
    day = day or {}
    apps = sorted((day.get("apps") or {}).items(), key=lambda x: -x[1])
    out = []
    for label, sec in apps:
        if sec < 600 or len(out) >= top:
            continue
        app, _, kind = label.partition("|")
        if kind == "和你聊天":
            out.append(f"和你聊天 {fmt_dur(sec)}")
        elif kind == "打游戏":
            out.append(f"{'《' + app + '》' if app else '打游戏'} {fmt_dur(sec)}")
        elif kind == "用电脑":
            out.append(f"{app or '用电脑'} {fmt_dur(sec)}")
        else:
            out.append(f"{app}{kind} {fmt_dur(sec)}" if app else f"{kind} {fmt_dur(sec)}")
    total = sum((day.get("kinds") or {}).values())
    if not out:
        return ""
    return "、".join(out) + (f"（一共在电脑前忙了 {fmt_dur(total)}）" if total >= 3600 else "")


class PCSense:
    FOCUS = ("写代码", "学习/办公")

    def __init__(self, cfg, db, on_song=None):
        c = dict((cfg.get("sense") or {}).get("pc") or {})
        self.on_song = on_song                  # 换歌时回调（查网易云、记进听歌记录）
        self.on_event = None                    # 刚发生的事回调（桌宠搭话）
        self.enabled = bool(c.get("enabled", True)) and sys.platform == "win32"
        detail = str(c.get("detail") or "").strip().lower()
        if detail not in ("app", "content", "full"):
            detail = "full" if c.get("send_titles") else "app"
        self.detail = detail
        self.ignore = tuple(str(x).lower() for x in (c.get("ignore") or []) if str(x).strip())
        self.tz = cfg.get("tz")
        self.db = db
        self._task: asyncio.Task | None = None
        self._kind, self._since = None, time.time()
        self._act: Activity | None = None
        self._app_since = time.time()
        self._prev: tuple[Activity, float, float] | None = None   # 上一件“正事”：(活动, 开始, 结束)
        self._music = None
        self._last_tick = 0.0
        self._fired: dict[str, float] = {}      # 话题冷却
        self.topics: list[dict] = []            # 可以拿来找他聊的“刚发生的事”

    def start(self):
        if self.enabled:
            self._task = asyncio.create_task(self._loop())
            log.info("电脑感知已开启（%s）", {"app": "只认程序、游戏和网站名，不发窗口标题",
                                         "content": "认程序、网站，加上视频标题 / 项目名 / 文档名",
                                         "full": "把窗口标题也告诉她"}[self.detail])

    async def stop(self):
        if self._task:
            self._task.cancel()

    # ---------------- 暂停（/sense 暂停） ----------------
    def paused(self) -> bool:
        return float(self.db.get("sense_pause_until") or 0) > time.time()

    def pause(self, hours: float | None):
        """hours=None 继续；否则暂停这么多小时。"""
        if hours is None:
            self.db.set("sense_pause_until", 0)
        else:
            self.db.set("sense_pause_until", time.time() + hours * 3600)
            self.db.set("sense_pc", None)

    # ---------------- Windows 接口 ----------------
    @staticmethod
    def _probe() -> tuple[str, str, float]:
        import ctypes
        from ctypes import wintypes
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.GetTickCount.restype = wintypes.DWORD
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return "", "", 0.0
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        h = k.OpenProcess(0x1000, False, pid.value)
        if h:
            size = wintypes.DWORD(1024)
            pbuf = ctypes.create_unicode_buffer(1024)
            if k.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
                exe = pbuf.value
            k.CloseHandle(h)

        class LII(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
        li = LII()
        li.cbSize = ctypes.sizeof(LII)
        u.GetLastInputInfo(ctypes.byref(li))
        idle = ((k.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0
        return exe, buf.value, max(0.0, idle)

    @staticmethod
    async def _media() -> tuple[str, str] | None:
        try:
            try:
                from winrt.windows.media.control import \
                    GlobalSystemMediaTransportControlsSessionManager as M  # type: ignore
            except ImportError:
                from winsdk.windows.media.control import \
                    GlobalSystemMediaTransportControlsSessionManager as M  # type: ignore
            mgr = await M.request_async()
            s = mgr.get_current_session()
            if not s:
                return None
            st = s.get_playback_info().playback_status
            if int(st) != 4:            # 4 = 正在播放
                return None
            props = await s.try_get_media_properties_async()
            if props and props.title:
                return props.title, props.artist or ""
        except Exception:  # noqa: BLE001
            return None
        return None

    # ---------------- 循环 ----------------
    async def _loop(self):
        await asyncio.sleep(10)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.debug("电脑感知出错：%s", e)
            await asyncio.sleep(30)

    def _topic(self, text: str, key: str | None = None, cooldown: float = 0, weight: float = 3.0):
        """记下一件可以找他聊的事（同一个 key 冷却期内不重复）。"""
        now = time.time()
        if key:
            if now - self._fired.get(key, 0) < cooldown:
                return
            self._fired[key] = now
        self.topics.append({"text": text, "ts": now, "w": weight})
        if self.on_event:                       # 桌宠在桌面上搭话
            t = asyncio.create_task(self.on_event(text))
            self._bg = getattr(self, "_bg", set())
            self._bg.add(t)
            t.add_done_callback(self._bg.discard)
        self.topics = [t for t in self.topics if now - t["ts"] < 1800][-5:]

    def _now(self):
        from datetime import datetime
        return datetime.now(self.tz) if self.tz else datetime.now()

    # ---------------- 今天在电脑上做了什么 ----------------
    def _day_key(self, d=None) -> str:
        return f"pc_day_{(d or self._now()):%Y-%m-%d}"

    def _count(self, act: Activity, sec: float):
        if sec <= 0 or act.kind in ("离开电脑了",):
            return
        key = self._day_key()
        day = self.db.get(key) or {"kinds": {}, "apps": {}, "first": time.time()}
        day["kinds"][act.kind] = day["kinds"].get(act.kind, 0) + sec
        label = f"{act.app}|{act.kind}" if act.app else f"|{act.kind}"
        day["apps"][label] = day["apps"].get(label, 0) + sec
        day["last"] = time.time()
        self.db.set(key, day)

    def today_summary(self, d=None, top: int = 4) -> str:
        return day_summary(self.db.get(self._day_key(d)), top)

    # ---------------- 每 30 秒看一眼 ----------------
    async def tick(self):
        if self.paused():
            return
        exe, title, idle = await asyncio.to_thread(self._probe)
        exe_name = exe.replace("/", "\\").split("\\")[-1]
        act = recognize(exe, title, self.detail, self.ignore)
        if idle > 600 and act.kind != "看视频":           # 看视频 / 看剧时不碰键鼠很正常，不算离开
            act = Activity("离开电脑了")
        music = await self._media()
        now = time.time()
        dt = min(now - self._last_tick, 90) if self._last_tick else 0
        self._last_tick = now
        if self._act is not None and (idle < 300 or self._act.kind == "看视频"):
            self._count(self._act, dt)
        kind = act.kind
        if kind != self._kind:
            self._on_kind_end(self._act, (now - self._since) / 60, now)
            if self._act is not None and self._act.kind not in ("离开电脑了", "和你聊天", "用电脑"):
                self._prev = (self._act, self._since, now)
            self._kind, self._since = kind, now
        if act.app != (self._act.app if self._act else None) or kind != (self._act.kind if self._act else None):
            self._app_since = now
        self._act = act
        mins = (now - self._since) / 60
        self._on_going(act, mins, idle)
        if music and music != self._music:
            self._topic(f"他在听《{music[0]}》" + (f" - {music[1]}" if music[1] else ""))
            if self.on_song:
                t = asyncio.create_task(self._song(music))
                self._bg = getattr(self, "_bg", set())
                self._bg.add(t)
                t.add_done_callback(self._bg.discard)
        self._music = music
        if kind == "离开电脑了":
            desc = f"不在电脑前（已经 {fmt_dur(now - self._since + 600)}）"
        else:
            desc = act.text() + (f"（已经 {fmt_dur(now - self._since)}）" if mins >= 10 else "")
        if kind == "和你聊天" and self._prev and now - self._prev[2] < 900:
            p_act, p_start, p_end = self._prev
            desc = f"刚才{p_act.text()}（{fmt_dur(p_end - p_start)}），现在切过来{desc}"
        if music:
            desc += f"；在放歌《{music[0]}》" + (f" - {music[1]}" if music[1] else "")
        self.db.set("sense_pc", {"kind": kind, "app": act.app, "exe": exe_name, "desc": desc, "since": self._since,
                                 "idle": idle, "music": list(music) if music else None, "ts": now,
                                 "today": self.today_summary()})

    def _on_kind_end(self, act: Activity | None, mins: float, now: float):
        """一件事刚结束：打完一局游戏、看完视频、离开很久回来了。"""
        if act is None:
            return
        if act.kind == "打游戏" and mins >= 40:
            self._topic(f"他刚打完{'《' + act.app + '》' if act.app else '游戏'}（玩了大概 {fmt_dur(mins * 60)}）",
                        "game_end", 1800)
        elif act.kind == "看视频" and mins >= 30:
            what = f"《{act.content}》" if act.content else ("一会儿" + act.app if act.app else "视频")
            self._topic(f"他刚看完{what}（看了 {fmt_dur(mins * 60)}）", "video_end", 3600, 2.0)
        elif act.kind == "离开电脑了" and mins >= 50:
            self._topic(f"他离开电脑 {fmt_dur(mins * 60 + 600)}后刚回来", "back", 3600)

    def _on_going(self, act: Activity, mins: float, idle: float):
        """一件事正在进行：开了一局游戏、连续专注了很久、半夜还在电脑前。"""
        if act.kind == "打游戏" and 5 <= mins < 6 and act.app:
            self._topic(f"他开始玩《{act.app}》了", f"game_start_{act.app}", 3 * 3600, 1.5)
        if act.kind in self.FOCUS and mins >= 120:
            what = act.text().removeprefix("在")
            self._topic(f"他已经连续{what}{fmt_dur(mins * 60)}了，一直没歇", f"focus_{self._since:.0f}", 10 ** 9, 4.0)
        h = self._now()
        if 0 <= h.hour < 5 and idle < 120 and act.kind not in ("离开电脑了", "和你聊天"):
            self._topic(f"都 {h:%H:%M} 了，他还在电脑前{act.text().removeprefix('在')}", f"late_{h:%Y-%m-%d}",
                        10 ** 9, 4.0)

    async def _song(self, music):
        try:
            info = await self.on_song(music[0], music[1])
            if info and info.get("album"):
                self._topic(f"他在听《{music[0]}》（{info['artist']}，专辑《{info['album']}》）")
        except Exception as e:  # noqa: BLE001
            log.debug("查歌失败：%s", e)

    def busy(self) -> bool:
        """他正专心打游戏 / 看视频时，少去打扰。"""
        pc = self.db.get("sense_pc") or {}
        return time.time() - pc.get("ts", 0) < 120 and pc.get("kind") in ("打游戏", "看视频")


PHONE_WORDS = [
    (("起床", "闹钟", "醒"), "刚起床"),
    (("到家", "回家"), "刚到家"),
    (("出门", "离开家"), "刚出门"),
    (("下班",), "下班了"),
    (("到公司", "到学校", "上班"), "到公司/学校了"),
    (("充电", "睡觉", "晚安"), "准备睡了（手机开始充电）"),
    (("电量低", "没电"), "手机快没电了"),
    (("健身", "运动"), "去运动了"),
]


def normalize_phone(text: str) -> str:
    t = (text or "").strip()
    for words, ev in PHONE_WORDS:
        if any(w in t for w in words):
            return ev
    return t[:40]


class PhoneSense:
    """订阅 ntfy 频道，手机发来的状态交给 on_event(event)。"""

    def __init__(self, cfg, db, on_event):
        c = dict((cfg.get("sense") or {}).get("phone") or {})
        self.enabled = bool(c.get("enabled", True))
        self.server = (c.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
        self.topic = c.get("ntfy_topic") or db.get("ntfy_topic")
        if not self.topic:
            self.topic = "ailife-" + secrets.token_hex(8)
            db.set("ntfy_topic", self.topic)
        self.proxy = cfg.proxy or None
        self.cfg_root = cfg.get("root")
        self.tz = cfg.get("tz")
        self.db, self.on_event = db, on_event
        self._task: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"{self.server}/{self.topic}"

    def start(self):
        if self.enabled:
            self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        delay = 5
        while True:
            for proxy in ([self.proxy, None] if self.proxy else [None]):
                try:
                    async with httpx.AsyncClient(proxy=proxy, timeout=httpx.Timeout(10, read=None)) as cli:
                        since = self.db.get("ntfy_since") or "10m"
                        async with cli.stream("GET", f"{self.url}/json", params={"since": since}) as r:
                            r.raise_for_status()
                            delay = 5
                            async for line in r.aiter_lines():
                                if not line.strip():
                                    continue
                                try:
                                    j = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                if j.get("event") == "message":
                                    self.db.set("ntfy_since", j.get("id") or "10m")
                                    await self.handle(j.get("message") or "")
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.debug("ntfy 连接断开：%s", e)
            await asyncio.sleep(delay)
            delay = min(300, delay * 2)

    async def handle(self, text: str):
        ev = normalize_phone(text)
        if not ev:
            return
        self.db.set("sense_phone", {"event": ev, "raw": text[:60], "ts": time.time()})
        log.info("手机传来：%s", ev)
        try:
            await self.on_event(ev)
        except Exception as e:  # noqa: BLE001
            log.warning("处理手机事件出错：%s", e)
