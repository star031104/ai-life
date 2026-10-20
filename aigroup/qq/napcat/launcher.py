"""start.bat 启动她的时候，顺便把 NapCat 也启动（Windows）。

- NapCat 已经在跑（WebSocket 端口能连上）：什么都不做
- 没在跑：在 NapCat 文件夹里找启动脚本，带上她小号的 QQ 号（快速登录，不用每次扫码），开一个最小化的窗口启动
- 快速登录不成功、NapCat 要扫码：自动把二维码图片弹出来
NapCat 文件夹：config 里的 qq.napcat_dir（setup 会写），没写就用程序目录下的 napcat/。
不会去找别的程序（比如 ai-life）的 NapCat —— 那是另一个号。
"""
from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("aigroup.napcat")

LAUNCHERS = ["napcat.bat", "launcher-user.bat", "launcher.bat", "launcher-win10.bat", "NapCatWinBootMain.exe",
             "napcat-utf8.bat"]


def port_open(url: str, timeout: float = 1.0) -> bool:
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 3002), timeout=timeout):
            return True
    except OSError:
        return False


def napcat_root(cfg) -> Path | None:
    q = cfg.get("qq") or {}
    d = str(q.get("napcat_dir") or "").strip().strip('"')
    if d and Path(d).is_dir():
        return Path(d)
    from aigroup.core.config import ROOT
    own = ROOT / "napcat"
    return own if pick_launcher(own) else None


def pick_launcher(root: Path) -> Path | None:
    names = list(LAUNCHERS)
    try:
        if sys.platform == "win32" and sys.getwindowsversion().build < 22000:     # Win10 用它专门的那个
            names.remove("launcher-win10.bat")
            names.insert(names.index("launcher.bat"), "launcher-win10.bat")
    except (AttributeError, ValueError):
        pass
    for n in names:
        for p in (root / n, root / "napcat" / n):
            if p.is_file():
                return p
    return None


def launch_args(launcher: Path, uin: int | None) -> list[str]:
    """快速登录参数。现在的 NapCat（Node 版 napcat.bat、Shell 版 launcher*.bat 都一样）只认 `-q 号码`：
    以前只跟一个光秃秃的号码，NapCat 会忽略它，于是每次都要扫码。"""
    return ["-q", str(uin)] if uin else []


def set_auto_login(root: Path, uin: int | None) -> bool:
    """在 NapCat 的 webui.json 里写上自动登录的号码（-q 参数之外的第二道保险）。改了返回 True。"""
    if not uin:
        return False
    import json
    for c in (root / "napcat" / "config" / "webui.json", root / "config" / "webui.json"):
        if not c.is_file():
            continue
        try:
            d = json.loads(c.read_text(encoding="utf-8"))
            if str(d.get("autoLoginAccount") or "") == str(uin):
                return False
            d["autoLoginAccount"] = str(uin)
            tmp = c.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=4), encoding="utf-8")
            tmp.replace(c)
            return True
        except (OSError, ValueError) as e:
            log.info("写 NapCat 自动登录设置失败：%s", e)
    return False


def bot_uin(cfg, root: Path) -> int | None:
    q = cfg.get("qq") or {}
    if str(q.get("napcat_qq") or "").isdigit():
        return int(q["napcat_qq"])
    from aigroup.qq.napcat.find import read_onebot
    for c in (root / "napcat" / "config", root / "config"):
        if c.is_dir():
            ob = read_onebot(c)
            if ob and ob.get("uin"):
                return int(ob["uin"])
    return None


PASSWORD_KEY = "NAPCAT_QUICK_PASSWORD_MD5"


def napcat_env(cfg, uin: int | None) -> dict:
    """给 NapCat 的环境变量。快速登录失败（QQ 提示“你的用户身份已失效”）时，NapCat 会用 ACCOUNT +
    NAPCAT_QUICK_PASSWORD_MD5 改用密码登录，不用再扫码；没存密码就照旧弹二维码。密码只存 MD5，在 .env 里。"""
    import os
    from aigroup.core.secrets import read_env
    env = dict(os.environ)
    if uin:
        env["NAPCAT_QUICK_ACCOUNT"] = str(uin)
        md5 = os.environ.get(PASSWORD_KEY) or read_env(Path(str(cfg.get("root") or "."))).get(PASSWORD_KEY, "")
        if md5:
            env["ACCOUNT"] = str(uin)
            env[PASSWORD_KEY] = md5
    return env


def save_password(root: Path, password: str) -> str:
    """把小号密码的 MD5 存进 .env（NapCat 只要 MD5）。空字符串 = 删掉。"""
    import hashlib
    from aigroup.core.secrets import write_env
    md5 = hashlib.md5(password.encode("utf-8")).hexdigest() if password else ""
    write_env(root, {PASSWORD_KEY: md5})
    return md5


def start_napcat(cfg) -> float | None:
    """启动 NapCat。默认在后台（不开窗口，输出写进 data/logs/napcat.log）；qq.napcat_hidden: false 就开一个最小化的窗口。
    返回启动时间；没启动返回 None。"""
    if sys.platform != "win32":
        return None
    root = napcat_root(cfg)
    if not root:
        log.warning("没找到 NapCat 文件夹，不能自动启动它：重新运行一次 setup.bat，或在 config.yaml 的 qq.napcat_dir 写上路径")
        return None
    launcher = pick_launcher(root)
    if not launcher:
        log.warning("NapCat 文件夹 %s 里没找到启动脚本（napcat.bat / launcher.bat）", root)
        return None
    uin = bot_uin(cfg, root)
    args = launch_args(launcher, uin)
    if set_auto_login(root, uin):
        log.info("已在 NapCat 里打开 %s 的自动登录（以后不用每次扫码）", uin)
    env = napcat_env(cfg, uin)
    hidden = bool((cfg.get("qq") or {}).get("napcat_hidden", True))
    data = Path(str(cfg.get("root") or ".")) / "data"
    try:
        if hidden:            # 后台跑：没有黑窗口；要扫码时二维码图片照样会弹出来
            (data / "logs").mkdir(parents=True, exist_ok=True)
            out = open(data / "logs" / "napcat.log", "ab")
            proc = subprocess.Popen(["cmd", "/c", launcher.name, *args], cwd=str(launcher.parent), env=env,
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                (data / "napcat.pid").write_text(str(proc.pid), encoding="utf-8")   # 托盘里“退出”时一起关掉
            except OSError:
                pass
        else:
            # start 把第一个带引号的参数当窗口标题，所以整条命令自己拼（路径都加引号，防空格）
            cmd = f'cmd /c start "NapCat" /min /D "{launcher.parent}" "{launcher.name}" ' + " ".join(args)
            subprocess.Popen(cmd, cwd=str(launcher.parent), env=env,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        log.warning("启动 NapCat 失败：%s", e)
        return None
    log.info("已经帮你启动 NapCat（%s %s，%s）", launcher.name, " ".join(args),
             "在后台运行，输出在 data/logs/napcat.log" if hidden else "任务栏里最小化的那个窗口")
    return time.time()


def _pid_alive(cfg) -> bool:
    """上次后台启动的 NapCat 还在不在（看 data/napcat.pid）。不知道就当不在。"""
    f = Path(str(cfg.get("root") or ".")) / "data" / "napcat.pid"
    try:
        pid = int(f.read_text(encoding="utf-8").strip())
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                             timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return str(pid) in out
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


async def _launch_and_watch(cfg, ob) -> bool:
    """启动 NapCat，盯 3 分钟（要扫码就把二维码弹出来）。返回有没有连上。"""
    t0 = start_napcat(cfg)
    if not t0:
        return False
    root = napcat_root(cfg)
    qr = [root / "napcat" / "cache" / "qrcode.png", root / "cache" / "qrcode.png"] if root else []
    shown = False
    for _ in range(90):                        # 最多盯 3 分钟
        await asyncio.sleep(2)
        if ob.ready:
            log.info("NapCat 已经登录好，QQ 连上了")
            return True
        for p in qr:
            try:
                if not shown and p.is_file() and p.stat().st_mtime >= t0 - 1:
                    import os
                    os.startfile(str(p))       # 快速登录没成功：把二维码弹出来，用她的小号扫
                    log.warning("NapCat 要扫码登录：二维码已经弹出来了，用她的小号扫一下"
                                "（扫这一次就行，以后会自动快速登录；手机 QQ 扫码时勾上“下次自动登录”更稳）")
                    shown = True
            except OSError:
                pass
    log.warning("NapCat 启动 3 分钟了还没连上：看 data/logs/napcat.log（是不是要扫码、或者报错了）")
    return False


async def ensure_napcat(cfg, ob) -> None:
    """启动时 QQ 连不上就开 NapCat；之后一直看着，运行中 NapCat 没了（被关、崩了）就再帮你开。

    - 只在 NapCat 端口连不上、而且上次后台开的 NapCat 进程也不在时才开，不会开出两个
    - 掉线 2 分钟后才动手（NapCat 自己重连 / 你手动重开都来得及）
    - 连着 3 次没救回来就不再自动开，日志里提醒你手动看；QQ 重新连上后重新计数
    """
    q = cfg.get("qq") or {}
    if not q.get("napcat_autostart", True) or sys.platform != "win32":
        return
    url = str(q.get("ws_url") or "ws://127.0.0.1:3002")
    grace = float(q.get("napcat_restart_after", 120))     # 掉线多少秒后自动拉起
    for _ in range(3):                         # 刚开机 / 你手动刚打开 NapCat：先等几秒，免得开两个
        if port_open(url):
            break
        await asyncio.sleep(2)
    else:
        await _launch_and_watch(cfg, ob)
    if not q.get("napcat_watchdog", True) or grace <= 0:
        return
    down_since: float | None = None
    fails = 0
    while True:
        await asyncio.sleep(15)
        if ob.ready:
            if down_since is not None:
                log.info("QQ 又连上了")
            down_since, fails = None, 0
            continue
        now = time.time()
        if down_since is None:
            down_since = now
            log.warning("QQ 掉线了：先等 NapCat 自己恢复，%d 秒后还没好就帮你重新打开 NapCat", int(grace))
            continue
        if now - down_since < grace or fails >= 3:
            continue
        if port_open(url) or _pid_alive(cfg):  # NapCat 还在（可能在等扫码 / 正在登录）：不再开第二个
            continue
        fails += 1
        log.warning("QQ 掉线 %d 秒了，NapCat 也不在运行：第 %d 次帮你重新打开", int(now - down_since), fails)
        if not await _launch_and_watch(cfg, ob):
            down_since = time.time()           # 重新等一轮再试
            if fails >= 3:
                log.warning("已经自动重开 NapCat 3 次都没连上，不再自动重试：看 data/logs/napcat.log，"
                            "要扫码就扫，或者托盘图标右键 → 重启")
