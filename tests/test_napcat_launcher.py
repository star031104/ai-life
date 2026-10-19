"""start.bat 顺便启动 NapCat。"""
import asyncio
import json
import os
import time

import ailife.napcat_launcher as L


REAL_BAT = '@echo off\r\nsetlocal\r\ncd /d "%~dp0" || exit /b 1\r\n"%~dp0node.exe" "%~dp0index.js" %*\r\nexit /b %errorlevel%\r\n'


def _napcat(tmp_path, bat_body=REAL_BAT):
    root = tmp_path / "NapCat.Shell.Windows.Node"
    (root / "napcat" / "config").mkdir(parents=True)
    (root / "napcat" / "cache").mkdir()
    (root / "napcat.bat").write_text(bat_body, encoding="utf-8")
    ob = {"network": {"websocketServers": [{"enable": True, "host": "127.0.0.1", "port": 3001, "token": "t"}]}}
    (root / "napcat" / "config" / "onebot11_2918273645.json").write_text(json.dumps(ob), encoding="utf-8")
    return root


def test_pick_and_args(tmp_path):
    root = _napcat(tmp_path)
    lp = L.pick_launcher(root)
    assert lp.name == "napcat.bat"
    assert L.launch_args(lp, 2918273645) == ["-q", "2918273645"]           # Node 版（真实的 napcat.bat 内容）
    (root / "napcat.bat").unlink()
    (root / "launcher.bat").write_text("@echo off\r\nNapCatWinBootMain.exe %1\r\n", encoding="utf-8")
    lp = L.pick_launcher(root)
    assert lp.name == "launcher.bat" and L.launch_args(lp, 123456) == ["-q", "123456"]      # Shell 版也要 -q
    assert L.launch_args(lp, None) == []
    cfg = {"qq": {"napcat_dir": str(root)}}
    assert L.napcat_root(cfg) == root and L.bot_uin(cfg, root) == 2918273645


def test_ensure_napcat_on_windows(tmp_path, monkeypatch):
    root = _napcat(tmp_path)
    cfg = {"root": str(tmp_path), "qq": {"napcat_dir": str(root), "ws_url": "ws://127.0.0.1:3001",
                                         "napcat_qq": 2918273645, "napcat_hidden": False}}
    launched, opened = [], []
    monkeypatch.setattr(L.sys, "platform", "win32")
    monkeypatch.setattr(L, "pick_launcher", lambda r: r / "napcat.bat")
    monkeypatch.setattr(L.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(L.asyncio, "sleep", lambda *_: real_sleep(0))

    class OB:
        ready = False
    # 已经在跑：不再开第二个
    monkeypatch.setattr(L, "port_open", lambda url, timeout=1.0: True)
    asyncio.run(L.ensure_napcat(cfg, OB()))
    assert not launched
    # 没在跑：启动，带快速登录参数；快速登录失败出了二维码 → 弹出来
    monkeypatch.setattr(L, "port_open", lambda url, timeout=1.0: False)

    def fake_popen(cmd, **kw):
        launched.append(cmd)
        qr = root / "napcat" / "cache" / "qrcode.png"
        qr.write_bytes(b"png")
        os.utime(qr, (time.time() + 1, time.time() + 1))
    monkeypatch.setattr(L.subprocess, "Popen", fake_popen)
    asyncio.run(L.ensure_napcat(cfg, OB()))
    assert launched and launched[0].endswith('"napcat.bat" -q 2918273645')
    assert launched[0].startswith('cmd /c start "NapCat" /min /D "')
    assert opened and opened[0].endswith("qrcode.png")
    # 关掉自动启动
    launched.clear()
    cfg["qq"]["napcat_autostart"] = False
    asyncio.run(L.ensure_napcat(cfg, OB()))
    assert not launched


def test_auto_login_written(tmp_path):
    root = _napcat(tmp_path)
    w = root / "napcat" / "config" / "webui.json"
    w.write_text(json.dumps({"port": 6099, "autoLoginAccount": ""}), encoding="utf-8")
    assert L.set_auto_login(root, 2918273645)
    assert json.loads(w.read_text(encoding="utf-8"))["autoLoginAccount"] == "2918273645"
    assert json.loads(w.read_text(encoding="utf-8"))["port"] == 6099
    assert not L.set_auto_login(root, 2918273645)          # 已经是了：不重复写
    assert not L.set_auto_login(root, None)


def test_start_napcat_hidden(tmp_path, monkeypatch):
    root = _napcat(tmp_path)
    cfg = {"root": str(tmp_path), "qq": {"napcat_dir": str(root), "napcat_qq": 2918273645}}
    calls = []

    class P:
        pid = 4321

    def fake_popen(cmd, **kw):
        calls.append((cmd, kw))
        return P()
    monkeypatch.setattr(L.sys, "platform", "win32")
    monkeypatch.setattr(L.subprocess, "Popen", fake_popen)
    assert L.start_napcat(cfg)
    cmd, kw = calls[0]
    assert cmd == ["cmd", "/c", "napcat.bat", "-q", "2918273645"] and kw["stdout"] is not None   # 后台跑，没有窗口
    assert (tmp_path / "data" / "napcat.pid").read_text() == "4321"
    assert (tmp_path / "data" / "logs" / "napcat.log").exists()
