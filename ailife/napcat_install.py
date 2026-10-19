"""NapCat 一键安装（Windows）：下载 → 解压 → 配好 WebSocket → 第一次扫码登录 → 打开自动登录。

    python run.py napcat      单独跑这一步（setup 向导里选了 QQ 也会自动跑）

- 下载官方发布页的 NapCat.Shell.Windows.Node.zip（自带 QQ 内核，不用另外装 QQ），放到程序目录下的 napcat/。
  GitHub 直连慢或者连不上时，依次换几个公共加速地址；都不行就提示手动下载后把 zip 放进程序目录。
- 在 napcat/config/onebot11.json 写好 WebSocket 服务器（127.0.0.1:3001，消息格式 array，随机 token）：
  NapCat 第一次登录某个号时会拿它当模板，所以不用再打开 WebUI 手动建。
- 第一次登录要用她的小号扫一次码（这一步谁也省不了）；登录成功后把号码写进 NapCat 的自动登录设置，
  以后 start.bat 带 -q 号码启动，不再扫码。
"""
from __future__ import annotations

import json
import os
import secrets as _secrets
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from .config import ROOT

ASSET = "NapCat.Shell.Windows.Node.zip"
RELEASE = f"https://github.com/NapNeko/NapCatQQ/releases/latest/download/{ASSET}"
# 公共的 GitHub 下载加速（只用来下载官方 release 文件；挂了就换下一个）
MIRRORS = ["", "https://ghfast.top/", "https://gh-proxy.com/", "https://ghproxy.net/"]
DEFAULT_PORT = 3001


def say(text: str):
    print(text, flush=True)


# ---------------- 找 / 下载 ----------------
def is_napcat_root(d: Path) -> bool:
    return d.is_dir() and ((d / "napcat.bat").is_file() or (d / "napcat" / "napcat.mjs").is_file())


def find_existing(root: Path = ROOT) -> Path | None:
    """先看程序目录下的 napcat/，再用 qq_setup 的办法在常见位置找。"""
    if is_napcat_root(root / "napcat"):
        return root / "napcat"
    try:
        from .qq_setup import find_napcat_configs, napcat_root_of
        for c in find_napcat_configs():
            r = napcat_root_of(c)
            if is_napcat_root(r):
                return r
    except Exception:  # noqa: BLE001
        pass
    return None


def _download(url: str, dst: Path, proxy: str = "") -> None:
    import httpx
    kw = {"proxy": proxy} if proxy else {}
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, read=60), **kw) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        if total and total < 10 * 1024 * 1024:
            raise RuntimeError(f"文件太小（{total} 字节），不像 NapCat 安装包")
        got, last = 0, 0.0
        with open(dst, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                got += len(chunk)
                if time.time() - last > 1:
                    last = time.time()
                    pct = f"{got * 100 // total}%" if total else ""
                    print(f"\r   已下载 {got / 1048576:.0f} MB {pct}   ", end="", flush=True)
    print()


def download(dest: Path, proxy: str = "", zip_path: Path | None = None) -> Path:
    """下载并解压到 dest。zip_path 给了（或者程序目录里已经放了那个 zip）就不下载。返回 NapCat 根目录。"""
    local = zip_path or next((p for p in (ROOT / ASSET, ROOT / "data" / ASSET) if p.is_file()), None)
    tmp = ROOT / "data" / (ASSET + ".part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if local:
        say(f"   用已经下载好的 {local.name}")
        src = local
    else:
        errs, ok = [], False
        for m in MIRRORS:
            for px in ([proxy, ""] if proxy and not m else [""]):
                url = m + RELEASE
                say(f"   下载 {url}" + ("（走代理）" if px else ""))
                try:
                    _download(url, tmp, px)
                    with zipfile.ZipFile(tmp) as z:
                        if z.testzip() is not None:
                            raise RuntimeError("下载的文件损坏了")
                    ok = True
                    break
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{url}：{str(e)[:80]}")
                    say(f"   ✗ {str(e)[:80]}")
            if ok:
                break
        if not ok:
            raise RuntimeError("NapCat 下载失败：\n   " + "\n   ".join(errs) +
                               f"\n   可以手动在浏览器打开 https://github.com/NapNeko/NapCatQQ/releases 下载 {ASSET}，"
                               f"放进程序文件夹（{ROOT}），再运行一次。")
        src = tmp
    extract(src, dest)
    if src == tmp:
        tmp.unlink(missing_ok=True)
    return dest


def extract(zip_file: Path, dest: Path) -> None:
    """解压。zip 里如果套了一层文件夹，去掉那层。"""
    with zipfile.ZipFile(zip_file) as z:
        names = [n for n in z.namelist() if n and not n.startswith("__MACOSX")]
        tops = {n.split("/", 1)[0] for n in names}
        strip = len(tops) == 1 and all("/" in n for n in names if not n.endswith("/")) \
            and not any(t in ("napcat.bat", "index.js") for t in tops)
        prefix = (next(iter(tops)) + "/") if strip else ""
        dest.mkdir(parents=True, exist_ok=True)
        root = dest.resolve()
        for info in z.infolist():
            name = info.filename
            if not name.startswith(prefix) or name.startswith("__MACOSX"):
                continue
            rel = name[len(prefix):]
            if not rel:
                continue
            out = (dest / rel).resolve()
            if root not in out.parents and out != root:          # 防 zip 里有 ../ 之类的路径
                continue
            if name.endswith("/"):
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as s, open(out, "wb") as d:
                while chunk := s.read(1 << 20):
                    d.write(chunk)
    if not is_napcat_root(dest):
        raise RuntimeError(f"解压出来的东西不对（{dest} 里没有 napcat.bat）")


# ---------------- WebSocket 配置 ----------------
def config_dir(nc: Path) -> Path:
    d = nc / "napcat" / "config" if (nc / "napcat").is_dir() else nc / "config"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ws_server(port: int, token: str) -> dict:
    return {"enable": True, "name": "ai-life", "host": "127.0.0.1", "port": port, "reportSelfMessage": False,
            "enableForcePushEvent": True, "messagePostFormat": "array", "token": token, "debug": False,
            "heartInterval": 30000}


def ensure_ws(nc: Path, port: int = DEFAULT_PORT) -> tuple[int, str]:
    """保证 NapCat 有一个启用的 WebSocket 服务器。已经有了就用原来的（端口、token 不变）。返回 (端口, token)。"""
    d = config_dir(nc)
    files = sorted(d.glob("onebot11_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files:                              # 登录过的号：有 WebSocket 就直接用
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ws = [w for w in (data.get("network") or {}).get("websocketServers") or [] if w.get("enable")]
        if ws:
            return int(ws[0].get("port") or port), str(ws[0].get("token") or "")
    tpl = d / "onebot11.json"
    try:
        data = json.loads(tpl.read_text(encoding="utf-8")) if tpl.is_file() else {}
    except (OSError, ValueError):
        data = {}
    ws = [w for w in (data.get("network") or {}).get("websocketServers") or [] if w.get("enable")]
    if ws:
        port, token = int(ws[0].get("port") or port), str(ws[0].get("token") or "")
    else:
        token = _secrets.token_urlsafe(18)
        data.setdefault("network", {}).setdefault("websocketServers", []).append(ws_server(port, token))
        for k in ("httpServers", "httpSseServers", "httpClients", "websocketClients", "plugins"):
            data["network"].setdefault(k, [])
        data.setdefault("musicSignUrl", "")
        data.setdefault("enableLocalFile2Url", False)
        data.setdefault("parseMultMsg", False)
        tpl.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for f in files:                              # 登录过、但没建 WebSocket 的号：也加上
        try:
            od = json.loads(f.read_text(encoding="utf-8"))
            od.setdefault("network", {}).setdefault("websocketServers", []).append(ws_server(port, token))
            f.write_text(json.dumps(od, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass
    return port, token


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


# ---------------- 第一次登录 ----------------
def start_visible(nc: Path, uin: int | None = None) -> float:
    from .napcat_launcher import launch_args, pick_launcher
    launcher = pick_launcher(nc)
    if not launcher:
        raise RuntimeError(f"{nc} 里没找到 NapCat 的启动脚本")
    args = " ".join(launch_args(launcher, uin))
    cmd = f'cmd /c start "NapCat" /D "{launcher.parent}" "{launcher.name}" {args}'.rstrip()
    subprocess.Popen(cmd, cwd=str(launcher.parent), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return time.time()


def wait_login(nc: Path, port: int, token: str, t0: float, minutes: float = 6) -> dict | None:
    """等她的小号登录好（能连上 WebSocket、拿到账号信息）。期间出了二维码就弹出来。"""
    from .qq import send_once_sync
    url = f"ws://127.0.0.1:{port}"
    qrs = [nc / "napcat" / "cache" / "qrcode.png", nc / "cache" / "qrcode.png"]
    shown = 0.0
    end = time.time() + minutes * 60
    while time.time() < end:
        for q in qrs:
            try:
                if q.is_file() and q.stat().st_mtime >= max(t0 - 1, shown + 1):
                    shown = q.stat().st_mtime
                    if sys.platform == "win32":
                        os.startfile(str(q))      # noqa: S606
                    say("   👉 二维码弹出来了：用【她的 QQ 小号】在手机 QQ 上扫一下，并在手机上点确认登录。"
                        "\n      （二维码过期会自动刷新、重新弹出；NapCat 的黑窗口里也有一份）")
            except OSError:
                pass
        try:
            info = send_once_sync(url, token, "get_login_info", {}, 4)
            if info.get("user_id"):
                return info
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
    return None


def oneclick(root: Path = ROOT, proxy: str = "", ask=None) -> dict | None:
    """一键：没有 NapCat 就装，配好 WebSocket，登录，打开自动登录。成功返回 {uin, nickname, port, token, dir}。"""
    from .napcat_launcher import port_open, set_auto_login
    if sys.platform != "win32":
        say("NapCat 一键安装只支持 Windows。Linux / macOS 请按 docs/QQ.md 用 NapCat 的 Docker 版，装好后运行 qqsetup。")
        return None
    nc = find_existing(root)
    if nc:
        say(f"✅ 已经有 NapCat：{nc}")
    else:
        nc = root / "napcat"
        say(f"⏬ 下载 NapCat（约 120 MB，自带 QQ 内核，不用另外装 QQ）→ {nc}")
        download(nc, proxy)
        say("✅ NapCat 解压好了")
    port, token = ensure_ws(nc)
    say(f"✅ WebSocket 服务器：127.0.0.1:{port}（token 自动生成，等会儿自动写进 .env）")
    uin = None
    try:
        from .napcat_launcher import bot_uin
        uin = bot_uin({}, nc)
    except Exception:  # noqa: BLE001
        pass
    if port_open(f"ws://127.0.0.1:{port}"):
        say("   NapCat 已经在运行了")
        t0 = time.time()
    else:
        if not port_free(port):
            say(f"❌ 端口 {port} 被别的程序占了。关掉占用它的程序，或者在 {config_dir(nc)} 的 onebot11*.json 里换个端口。")
            return None
        say("🚀 启动 NapCat" + ("（快速登录，不用扫码）" if uin else "（第一次要扫码登录）") + "…")
        say("   ⚠️ 用她的【小号】登录，别用你自己的主号（非官方协议有封号风险）。电脑上已经登着这个号的 QQ 要先退出。")
        t0 = start_visible(nc, uin)
    info = wait_login(nc, port, token, t0)
    if not info:
        say("❌ 6 分钟里没登录上。看一下 NapCat 的黑窗口有没有报错；弄好后再运行一次 qq_setup.bat（已经装好的不会重新下载）。")
        return None
    uin = int(info["user_id"])
    say(f"✅ 登录成功：{info.get('nickname')}（{uin}）")
    if set_auto_login(nc, uin):
        say("✅ 已打开自动登录：以后 start.bat 会顺便启动 NapCat，不用再扫码")
    return {"uin": uin, "nickname": info.get("nickname"), "port": port, "token": token, "dir": nc}


def main(argv: list[str] | None = None) -> int:
    from .qq_setup import main as qq_main
    if sys.platform != "win32":
        return qq_main([])
    proxy = ""
    try:
        from .config import load_config
        proxy = str(load_config().get("proxy") or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        r = oneclick(proxy=proxy)
    except Exception as e:  # noqa: BLE001
        say(f"❌ {e}")
        return 1
    if not r:
        return 1
    say("\n接下来选出你自己的 QQ、写好 config.yaml：")
    return qq_main([])
