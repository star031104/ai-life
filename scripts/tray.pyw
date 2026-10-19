"""托盘启动器（Windows）：不开黑窗口，在任务栏右下角放一个小图标。

start.bat 用 pythonw 启动它；它在后台跑 `python run.py`（没有窗口，输出写进 data/logs/console.log），
程序退出了 10 秒后自动重开（退出码 3＝配置有问题，不重开，图标上提示你看日志）。
右键图标：状态、打开小屋 / 看日志 / 换大模型 / 重启 / 开机自启 / 退出。
退出时先让程序自己收尾（她会说一声再走），再把后台的 NapCat 一起关掉。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# ---------------- 每个程序不一样的地方 ----------------
APP = {
    "name": "ai-life",
    "launcher_env": "AILIFE_LAUNCHER",
    "log": DATA / "logs" / "ailife.log",
    "home": "http://127.0.0.1:8765",           # 双击图标打开小屋
    "color": (236, 92, 140),
    "letter": "L",
    "startup": "ai-life.cmd",
    "extra": [("换大模型", "scripts/llm.bat"), ("检查更新", "scripts/update.bat")],
}

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EXIT_NO_RESTART = 3


def after_update_failure() -> str:
    """刚更新过的新版本连续起不来：换回旧版（ailife/update.py）。"""
    try:
        sys.path.insert(0, str(ROOT))
        from ailife.core.update import on_quick_exit
        return on_quick_exit(ROOT)
    except Exception:  # noqa: BLE001
        return ""


def message(text: str, title: str | None = None):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title or APP["name"], 0x40)
    except Exception:  # noqa: BLE001
        pass


def single_instance():
    DATA.mkdir(parents=True, exist_ok=True)
    f = open(DATA / "tray.lock", "a+")
    try:
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except (OSError, ImportError):
        f.close()
        return None
    return f


def console_python() -> str:
    """pythonw.exe 旁边的 python.exe（子进程用它，输出能写进文件）。"""
    exe = Path(sys.executable)
    cand = exe.with_name("python.exe")
    return str(cand if cand.exists() else exe)


def make_icon(color, letter: str, dim: bool = False):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = tuple(int(x * 0.45 + 120) for x in color) if dim else color
    d.ellipse((2, 2, 62, 62), fill=c + (255,))
    try:
        font = ImageFont.truetype("seguisb.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), letter, font=font)
    d.text(((64 - (box[2] - box[0])) / 2 - box[0], (64 - (box[3] - box[1])) / 2 - box[1]), letter,
           fill=(255, 255, 255, 255), font=font)
    return img


def startup_file() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / \
        APP["startup"]


class Runner:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.state = "正在启动"
        self.quitting = False
        self.wake = threading.Event()
        self.restart_now = False
        self.icon = None

    def set_state(self, s: str, notify: str | None = None):
        self.state = s
        if not self.icon:
            return
        try:
            self.icon.title = f"{APP['name']}：{s}"[:120]
            self.icon.icon = make_icon(APP["color"], APP["letter"], dim=s != "运行中")
            if notify:
                self.icon.notify(notify, APP["name"])
            self.icon.update_menu()
        except Exception:  # noqa: BLE001
            pass

    def loop(self):
        (DATA / "logs").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", **{APP["launcher_env"]: "1"})
        fails = 0
        while not self.quitting:
            con = DATA / "logs" / "console.log"
            try:
                if con.exists() and con.stat().st_size > 5_000_000:
                    con.replace(con.with_suffix(".old.log"))
            except OSError:
                pass
            (DATA / "tray_cmd").unlink(missing_ok=True)
            out = open(con, "ab")
            t0 = time.time()
            self.proc = subprocess.Popen([console_python(), "run.py"], cwd=str(ROOT), env=env,
                                         stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                         creationflags=NO_WINDOW)
            self.restart_now = False
            self.set_state("运行中")
            rc = self.proc.wait()
            out.close()
            if self.quitting:
                break
            if rc == EXIT_NO_RESTART:
                self.set_state("已停止（看日志）", "程序停下来了：可能是配置有问题。右键图标 → 看日志；改好后点“重启”。")
                self.wake.clear()
                self.wake.wait()                 # 等你点“重启”
                continue
            if self.restart_now:                  # 托盘里点了“重启”：马上重开
                self.restart_now = False
                continue
            fails = fails + 1 if time.time() - t0 < 60 else 0
            if fails and rc not in (0, None):
                msg = after_update_failure()
                if msg:                              # 刚更新的新版本起不来：已经换回旧版，马上重开
                    self.set_state("正在重启", msg)
                    fails = 0
                    continue
            if rc not in (0, None):
                self.set_state("出错了，10 秒后重启", "程序意外退出了，10 秒后自动重启" if fails <= 1 else None)
            self.wake.clear()
            self.wake.wait(10 if fails < 5 else 120)

    def command(self, cmd: str, wait: float = 12):
        """让程序自己收尾（她会说一声再走），等不及就强关。"""
        p = self.proc
        if not p or p.poll() is not None:
            return
        try:
            (DATA / "tray_cmd").write_text(cmd, encoding="utf-8")
        except OSError:
            pass
        try:
            p.wait(wait)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], creationflags=NO_WINDOW,
                           capture_output=True)

    def restart(self):
        self.set_state("正在重启")
        self.restart_now = True
        if self.proc and self.proc.poll() is None:
            threading.Thread(target=self.command, args=("restart",), daemon=True).start()
        self.wake.set()

    def stop_napcat(self):
        pidf = DATA / "napcat.pid"
        try:
            pid = int(pidf.read_text().strip())
        except (OSError, ValueError):
            return
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], creationflags=NO_WINDOW, capture_output=True)
        pidf.unlink(missing_ok=True)

    def quit(self, icon=None, item=None):
        self.quitting = True
        self.set_state("正在退出")
        self.command("stop")
        self.stop_napcat()
        self.wake.set()
        if self.icon:
            self.icon.stop()


def open_path(p: Path):
    try:
        os.startfile(str(p))                     # noqa: S606
    except OSError:
        message(f"打不开 {p}")


def run_bat(name: str):
    bat = ROOT / name
    if bat.exists():
        subprocess.Popen(["cmd", "/c", "start", "", str(bat)], cwd=str(ROOT), creationflags=NO_WINDOW)


def toggle_startup(icon=None, item=None):
    f = startup_file()
    if f.exists():
        f.unlink(missing_ok=True)
    else:
        f.write_text(f'@echo off\r\ncd /d "{ROOT}"\r\nstart "" /min "{ROOT / "start.bat"}"\r\n', encoding="utf-8")


def main():
    lock = single_instance()
    if lock is None:
        message(f"{APP['name']} 已经在运行了：看任务栏右下角的小图标（可能藏在 ^ 里）。")
        return
    try:
        import pystray
    except ImportError:
        message("缺少 pystray：重新双击 start.bat（它会自动装上），或者运行 install.bat。")
        return
    r = Runner()
    items = [pystray.MenuItem(lambda _i: f"状态：{r.state}", None, enabled=False),
             pystray.Menu.SEPARATOR]
    if APP.get("home"):
        items.append(pystray.MenuItem("打开小屋", lambda *_: webbrowser.open(APP["home"]), default=True))
    items.append(pystray.MenuItem("看日志", lambda *_: open_path(APP["log"]), default=not APP.get("home")))
    for label, target in APP.get("extra", []):
        items.append(pystray.MenuItem(label, (lambda t: (lambda *_: run_bat(t)))(target)))
    items += [pystray.Menu.SEPARATOR,
              pystray.MenuItem("重启", lambda *_: r.restart()),
              pystray.MenuItem("开机自动启动", toggle_startup, checked=lambda _i: startup_file().exists()),
              pystray.MenuItem("退出", r.quit)]
    r.icon = pystray.Icon(APP["name"], make_icon(APP["color"], APP["letter"], dim=True), f"{APP['name']}：正在启动",
                          menu=pystray.Menu(*items))

    def setup(icon):
        icon.visible = True
        threading.Thread(target=r.loop, daemon=True).start()
    r.icon.run(setup=setup)
    lock.close()


if __name__ == "__main__":
    main()
