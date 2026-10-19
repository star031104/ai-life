"""备份 / 换电脑：把她（程序 + 记忆 + 设置 + 密钥）打包，带到另一台电脑上。

    python run.py export          打包一份（这台电脑照常运行，可以当备份）
    python run.py import 包.zip   在新电脑装好程序后，把包里的记忆和设置导进来（会先备份现在的）

两台电脑不要同时运行她：会抢 QQ 消息，记忆也会分叉。换好电脑后，旧电脑上就别再启动了。
"""
from __future__ import annotations

import os
import socket
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path

from ailife.core import secrets

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv", ".idea", ".vscode"}
SKIP_SUFFIX = (".pyc", ".part", ".tmp", ".zip", ".session", ".session-journal", ".log")
DATA_KEEP = ("photos", "personas.json")          # data/ 里要带走的（缓存会在新电脑上重新下载）


def host_running(cfg) -> bool:
    """本机是不是正有她在运行（网页端口被占着就算）。"""
    port = int((cfg.get("web") or {}).get("port", 8765))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _iter_code(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        parts = rel.parts
        if parts and (parts[0] == "data" or parts[0] in SKIP_DIRS):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".venv")]
        for fn in filenames:
            if fn.endswith(SKIP_SUFFIX) or fn.startswith("ai-life-move-"):
                continue
            yield Path(dirpath) / fn


def export(cfg, log=print) -> Path:
    root = Path(cfg.root)
    moved = secrets.migrate(root)                      # 密钥先挪进 .env，跟着包一起走
    if moved:
        log(f"已把 {len(moved)} 个密钥从 config.yaml 挪进 .env")
    out = root / f"ai-life-move-{time.strftime('%Y%m%d-%H%M')}.zip"
    db_path = root / "data" / "ailife.db"
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in _iter_code(root):
            arc = "ai-life/" + f.relative_to(root).as_posix()
            if f.suffix == ".sh":                         # Windows 上的 git 可能把换行改成 CRLF
                z.writestr(zipfile.ZipInfo.from_file(f, arc), f.read_bytes().replace(b"\r\n", b"\n"))
            else:
                z.write(f, arc)
            n += 1
        from ailife.persona.library import all_dbs
        for dbf in all_dbs(cfg) or ([db_path] if db_path.exists() else []):   # 每个角色的记忆都带上
            with tempfile.TemporaryDirectory() as td:   # SQLite 在线备份：运行中也能拿到完整、一致的一份
                tmp = Path(td) / dbf.name
                src = sqlite3.connect(str(dbf))
                dst = sqlite3.connect(str(tmp))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
                    src.close()
                z.write(tmp, "ai-life/" + dbf.relative_to(root).as_posix())
        for sub in DATA_KEEP:
            d = root / "data" / sub
            if d.is_file():
                z.write(d, "ai-life/" + d.relative_to(root).as_posix())
                n += 1
            elif d.exists():
                for f in d.rglob("*"):
                    if f.is_file() and not f.name.endswith(SKIP_SUFFIX):
                        z.write(f, "ai-life/" + f.relative_to(root).as_posix())
                        n += 1
    size = out.stat().st_size / 1024 / 1024
    log(f"打包好了：{out}（{size:.1f} MB，{n} 个文件）")
    log("换电脑：新电脑上先装好程序（install.bat），再运行 python run.py import 这个包；旧电脑就别再启动她了。")
    log("⚠️ 包里有你的密钥和全部聊天记忆：别发给别人、别传到网盘，用完就删掉。")
    return out


def import_(cfg, zip_path: str, log=print) -> None:
    root = Path(cfg.root)
    if host_running(cfg):
        raise RuntimeError("她正在这台电脑上运行：先关掉 start.bat 窗口再导入")
    zp = Path(zip_path).expanduser()
    if not zp.exists():
        raise RuntimeError(f"找不到 {zp}")
    db_path = root / "data" / "ailife.db"
    if db_path.exists():                               # 先备份现在的
        bk = root / "data" / "backup"
        bk.mkdir(parents=True, exist_ok=True)
        dst = bk / f"before-import-{time.strftime('%Y%m%d-%H%M%S')}.db"
        src = sqlite3.connect(str(db_path))
        out = sqlite3.connect(str(dst))
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
        log(f"原来的记忆备份在 {dst}")
    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
        prefix = "ai-life/" if any(n.startswith("ai-life/") for n in names) else ""
        for n in names:
            rel = n[len(prefix):]
            if not rel or rel.endswith("/"):
                continue
            take = rel in ("data/ailife.db", "data/personas.json") or rel.startswith(
                ("data/photos/", "persona/", "data/chars/"))
            if rel == "config.yaml" and not (root / "config.yaml").exists():
                take = True
            if not take:
                continue
            target = (root / rel).resolve()
            if root.resolve() not in target.parents:
                continue                                # 防止包里有奇怪的路径
            target.parent.mkdir(parents=True, exist_ok=True)
            if rel.endswith(".db"):
                for side in ("-wal", "-shm"):           # 旧数据库的日志文件不删会把新数据库弄坏
                    p = Path(str(target) + side)
                    if p.exists():
                        p.unlink()
            with z.open(n) as src, open(target, "wb") as dst:
                dst.write(src.read())
        envname = prefix + ".env"
        if envname in names:
            import io
            tmp_env = {}
            for line in io.TextIOWrapper(z.open(envname), encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    tmp_env[k.strip()] = v.strip()
            if tmp_env:
                secrets.write_env(root, tmp_env)
    log("导入完成：启动后就是包里的那个她。")
