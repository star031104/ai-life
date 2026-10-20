"""找 NapCat：读它的 onebot11 配置（WebSocket 端口、token）。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from aigroup.core.config import ROOT

NAPCAT_GLOBS = ["NapCat*", "napcat*", "*/NapCat*"]


# ---------------- 找 NapCat ----------------
def candidate_roots() -> list[Path]:
    out = []
    if sys.platform == "win32":
        for d in "CDEFGH":
            out.append(Path(f"{d}:/"))
    home = Path.home()
    out.insert(0, ROOT)                          # napcat_install 一键装的 NapCat 在程序目录下的 napcat/
    out += [home / "Desktop", home / "Downloads", home / "Documents", home, Path.cwd().parent]
    return [p for p in out if p.exists()]


def find_napcat_configs(roots: list[Path] | None = None) -> list[Path]:
    """返回所有找到的 napcat/config 目录（里面有 onebot11_*.json 的优先）。"""
    found: list[Path] = []
    for r in roots if roots is not None else candidate_roots():
        for g in NAPCAT_GLOBS:
            try:
                for d in r.glob(g):
                    if d.name.lower().endswith("-group"):     # 群聊角色那份（group_setup 建的），不是一对一的
                        continue
                    for c in (d / "napcat" / "config", d / "config"):
                        if c.is_dir() and c not in found:
                            found.append(c)
            except OSError:
                continue
    found.sort(key=lambda c: (not list(c.glob("onebot11_*.json")), -c.stat().st_mtime))
    return found


def config_dir_from(path: str) -> Path | None:
    p = Path(path.strip().strip('"').strip("'"))
    for c in (p, p / "config", p / "napcat" / "config"):
        if c.is_dir() and (list(c.glob("onebot11_*.json")) or (c / "napcat.json").exists()):
            return c
    return None


def read_onebot(cfg_dir: Path) -> dict | None:
    """读最近改过的 onebot11_<QQ号>.json：返回 {uin, url, token, format, self_msg, file}。没配 WebSocket 服务器就 None。"""
    files = sorted(cfg_dir.glob("onebot11_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        net = data.get("network") or {}
        ws = [w for w in net.get("websocketServers") or [] if w.get("enable")]
        if not ws:
            continue
        w = ws[0]
        host = str(w.get("host") or "127.0.0.1")
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        m = re.search(r"onebot11_(\d+)\.json$", f.name)
        return {"uin": int(m.group(1)) if m else 0, "url": f"ws://{host}:{int(w.get('port') or 3001)}",
                "token": str(w.get("token") or ""), "format": str(w.get("messagePostFormat") or "array"),
                "self_msg": bool(w.get("reportSelfMessage")), "file": f}
    return None


def napcat_root_of(cfg_dir: Path) -> Path:
    """.../NapCat.Shell.Windows.Node/napcat/config → .../NapCat.Shell.Windows.Node"""
    return cfg_dir.parent.parent if cfg_dir.parent.name.lower() == "napcat" else cfg_dir.parent


