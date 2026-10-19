"""start.bat 启动她的时候，顺便把 NapCat 也启动（Windows）。

- NapCat 已经在跑（WebSocket 端口能连上）：什么都不做
- 没在跑：在 NapCat 文件夹里找启动脚本，带上她小号的 QQ 号（快速登录，不用每次扫码），开一个最小化的窗口启动
- 快速登录不成功、NapCat 要扫码：自动把二维码图片弹出来
NapCat 文件夹：config 里的 qq.napcat_dir（qq_setup 会写），没写就自动找。
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

log = logging.getLogger("ailife.qq")

LAUNCHERS = ["napcat.bat", "launcher-user.bat", "launcher.bat", "launcher-win10.bat", "NapCatWinBootMain.exe",
             "napcat-utf8.bat"]


def port_open(url: str, timeout: float = 1.0) -> bool:
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 3001), timeout=timeout):
            return True
    except OSError:
        return False


def napcat_root(cfg) -> Path | None:
    q = cfg.get("qq") or {}
    d = str(q.get("napcat_dir") or "").strip().strip('"')
    if d and Path(d).is_dir():
        return Path(d)
    from .qq_setup import find_napcat_configs
    for c in find_napcat_configs():
        root = c.parent.parent if c.parent.name.lower() == "napcat" else c.parent
        if pick_launcher(root):
            return root
    return None


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
    from .qq_setup import read_onebot
    for c in (root / "napcat" / "config", root / "config"):
        if c.is_dir():
            ob = read_onebot(c)
            if ob and ob.get("uin"):
                return int(ob["uin"])
    return None


def start_napcat(cfg) -> float | None:
    """在新的最小化窗口里启动 NapCat。返回启动时间；没启动返回 None。"""
    if sys.platform != "win32":
        return None
    root = napcat_root(cfg)
    if not root:
        log.warning("没找到 NapCat 文件夹，不能自动启动它：重新运行一次 qq_setup.bat，或在 config.yaml 的 qq.napcat_dir 写上路径")
        return None
    launcher = pick_launcher(root)
    if not launcher:
        log.warning("NapCat 文件夹 %s 里没找到启动脚本（napcat.bat / launcher.bat）", root)
        return None
    uin = bot_uin(cfg, root)
    args = launch_args(launcher, uin)
    if set_auto_login(root, uin):
        log.info("已在 NapCat 里打开 %s 的自动登录（以后不用每次扫码）", uin)
    # start 把第一个带引号的参数当窗口标题，所以整条命令自己拼（路径都加引号，防空格）
    cmd = f'cmd /c start "NapCat" /min /D "{launcher.parent}" "{launcher.name}" ' + " ".join(args)
    import os
    env = dict(os.environ, **({"NAPCAT_QUICK_ACCOUNT": str(uin)} if uin else {}))
    try:
        subprocess.Popen(cmd, cwd=str(launcher.parent), env=env,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        log.warning("启动 NapCat 失败：%s", e)
        return None
    log.info("已经帮你启动 NapCat（%s %s，任务栏里最小化的那个窗口）", launcher.name, " ".join(args))
    return time.time()


async def ensure_napcat(cfg, ob) -> None:
    """QQ 连不上时启动 NapCat，并盯着要不要扫码。"""
    q = cfg.get("qq") or {}
    if not q.get("napcat_autostart", True) or sys.platform != "win32":
        return
    url = str(q.get("ws_url") or "ws://127.0.0.1:3001")
    for _ in range(3):                         # 刚开机 / 你手动刚打开 NapCat：先等几秒，免得开两个
        if port_open(url):
            return
        await asyncio.sleep(2)
    t0 = start_napcat(cfg)
    if not t0:
        return
    root = napcat_root(cfg)
    qr = [root / "napcat" / "cache" / "qrcode.png", root / "cache" / "qrcode.png"] if root else []
    shown = False
    for _ in range(90):                        # 最多盯 3 分钟
        await asyncio.sleep(2)
        if ob.ready:
            log.info("NapCat 已经登录好，QQ 连上了")
            return
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
    if not ob.ready:
        log.warning("NapCat 启动 3 分钟了还没连上：看一下任务栏里的 NapCat 窗口（是不是要扫码、或者报错了）")
