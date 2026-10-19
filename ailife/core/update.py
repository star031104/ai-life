"""自动更新：看 GitHub（或你配的 Gitee 镜像）上有没有新版本，下载、检查、备份、换上；新版起不来就换回去。

    python run.py update            看看有没有新版本
    python run.py update apply      下载并换上新版本（会先备份）
    python run.py update rollback   换回更新前的版本

规矩（参考了 Wakuwaku 的更新方式）：
- 只从配置里的仓库下载：GitHub（codeload / raw），国内可以在 update.gitee 填 Gitee 上的镜像仓库，先试它
- 下载的 zip 先检查：能完整解开（每个文件的 CRC 都对）、里面的版本号确实比现在新、程序的关键文件都在；
  不对就不装，一个文件都不动。记下 zip 的 SHA-256，回滚时用来核对
- 换之前把要被覆盖的文件打包备份到 data/backup/code-<旧版本>-<时间>.zip；新加的文件记在清单里，回滚时删掉
- 你的东西永远不碰：config.yaml、config.llm.yaml、.env、data/、napcat/、.venv/，persona/ 里已有的人设
- 换好后重启；新版连续两次刚启动就退出（托盘会发现），自动换回旧版；新版正常跑够 2 分钟才算更新成功
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

log = logging.getLogger("ailife.update")

REPO = "star031104/ai-life"
BRANCH = "main"
VERSION_RE = re.compile(r'__version__\s*=\s*["\']([\d.]+)["\']')
PROTECT_FILES = {"config.yaml", "config.llm.yaml", ".env"}
PROTECT_DIRS = ("data/", "napcat/", ".venv/", "venv/", ".git/", "instances/")
MUST_HAVE = ("run.py", "ailife/__init__.py", "ailife/cli.py", "requirements.txt")
# 新版本里已经没有的旧代码文件要拿掉（搬家、删掉的模块），只看这些地方、这些类型；拿掉前同样备份
CODE_DIRS = ("ailife/", "tests/", "scripts/", "tools/")
CODE_EXT = (".py", ".pyw", ".html", ".bat", ".sh", ".ps1")
LEGACY_ROOT = {"llm.bat", "qq_setup.bat", "update.bat", "selftest.bat", "pet_setup.bat", "autostart_on.bat",
               "autostart_off.bat", "start_console.bat"}
MAX_ZIP = 60 * 1024 * 1024
CONFIRM_SECONDS = 120


class UpdateError(Exception):
    pass


def vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "0"))) or (0,)


def current_version() -> str:
    from ailife import __version__
    return __version__


def parse_version(text: str) -> str:
    m = VERSION_RE.search(text or "")
    return m.group(1) if m else ""


def sources(cfg) -> list[dict]:
    """去哪里看版本、下载代码：Gitee 镜像（填了的话，国内先试它）→ GitHub。"""
    u = dict(cfg.get("update") or {})
    repo = str(u.get("repo") or REPO).strip("/")
    branch = str(u.get("branch") or BRANCH)
    out = []
    gitee = str(u.get("gitee") or "").strip("/")
    if gitee:
        out.append({"name": "Gitee", "version": f"https://gitee.com/{gitee}/raw/{branch}/ailife/__init__.py",
                    "zip": f"https://gitee.com/{gitee}/repository/archive/{branch}.zip"})
    out.append({"name": "GitHub", "version": f"https://raw.githubusercontent.com/{repo}/{branch}/ailife/__init__.py",
                "zip": f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}"})
    return out


def _fetch(url: str, proxy: str | None, limit: int, timeout: float = 60) -> bytes:
    """先直连，不行再走代理。"""
    import httpx
    last = None
    for px in ((None, proxy) if proxy else (None,)):
        try:
            with httpx.Client(proxy=px, timeout=timeout, follow_redirects=True,
                              headers={"user-agent": "ai-life-updater"}) as c:
                with c.stream("GET", url) as r:
                    r.raise_for_status()
                    buf = io.BytesIO()
                    for chunk in r.iter_bytes():
                        buf.write(chunk)
                        if buf.tell() > limit:
                            raise UpdateError("文件太大了，不像是 ai-life 的代码")
                    return buf.getvalue()
        except UpdateError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
    raise UpdateError(f"{url} 连不上：{last}")


def check(cfg, fetch=_fetch) -> dict:
    """看看有没有新版本。返回 {current, latest, newer, source, error}。"""
    cur = current_version()
    errors = []
    for src in sources(cfg):
        try:
            latest = parse_version(fetch(src["version"], cfg.get("proxy") or None, 64_000, 20).decode("utf-8", "replace"))
            if not latest:
                raise UpdateError("没读到版本号")
            return {"current": cur, "latest": latest, "newer": vtuple(latest) > vtuple(cur), "source": src["name"],
                    "error": "", "ts": time.time()}
        except UpdateError as e:
            errors.append(f"{src['name']}：{e}")
    return {"current": cur, "latest": "", "newer": False, "source": "", "error": "；".join(errors), "ts": time.time()}


def _protected(rel: str, root: Path) -> bool:
    if rel in PROTECT_FILES or rel.startswith(PROTECT_DIRS) or "/__pycache__/" in f"/{rel}":
        return True
    if rel.startswith("persona/") and (root / rel).exists():
        return True                                  # 你改过的人设不覆盖（新的人设模板照常加进来）
    return False


def inspect_zip(data: bytes) -> tuple[zipfile.ZipFile, str, str]:
    """检查下载的 zip：能解开、每个文件的 CRC 对、关键文件都在。返回 (zip, 顶层目录前缀, 版本号)。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UpdateError(f"下载的不是完整的 zip：{e}")
    bad = z.testzip()
    if bad:
        raise UpdateError(f"zip 里的 {bad} 坏了")
    names = [n for n in z.namelist() if not n.endswith("/")]
    init = next((n for n in names if n.endswith("ailife/__init__.py") and n.count("/") <= 2), "")
    if not init:
        raise UpdateError("zip 里没有 ailife/__init__.py，不像是 ai-life")
    prefix = init[: -len("ailife/__init__.py")]
    for must in MUST_HAVE:
        if prefix + must not in names:
            raise UpdateError(f"zip 里少了 {must}")
    for n in names:
        rel = n[len(prefix):]
        if not n.startswith(prefix) or rel.startswith("/") or ".." in Path(rel).parts or ":" in rel:
            raise UpdateError(f"zip 里有奇怪的路径：{n}")
    ver = parse_version(z.read(init).decode("utf-8", "replace"))
    return z, prefix, ver


def apply(cfg, fetch=_fetch, *, pip: bool = True) -> dict:
    """下载、检查、备份、换上新版本。不重启（调用的人决定什么时候重启）。"""
    root = Path(cfg.root)
    info = check(cfg, fetch)
    if info["error"] and not info["latest"]:
        raise UpdateError(info["error"])
    if not info["newer"]:
        raise UpdateError(f"已经是最新的了（{info['current']}）")
    src = next(s for s in sources(cfg) if s["name"] == info["source"])
    data = fetch(src["zip"], cfg.get("proxy") or None, MAX_ZIP, 180)
    sha = hashlib.sha256(data).hexdigest()
    z, prefix, ver = inspect_zip(data)
    if vtuple(ver) <= vtuple(info["current"]):
        raise UpdateError(f"下载到的是 {ver}，不比现在的 {info['current']} 新")

    plan = []                                         # (相对路径, 新内容)
    for n in z.namelist():
        if n.endswith("/"):
            continue
        rel = n[len(prefix):]
        if not rel or _protected(rel, root):
            continue
        new = z.read(n)
        old_p = root / rel
        if old_p.exists() and old_p.read_bytes() == new:
            continue
        plan.append((rel, new))
    in_zip = {n[len(prefix):] for n in z.namelist() if not n.endswith("/")}
    stale = sorted(rel for rel in _local_code(root) if rel not in in_zip)
    if not plan and not stale:
        raise UpdateError("没有要换的文件")

    # 备份：要覆盖的旧文件进 zip；新加的文件记下来，回滚时删掉
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = root / "data" / "backup"
    bdir.mkdir(parents=True, exist_ok=True)
    backup = bdir / f"code-{info['current']}-{stamp}.zip"
    overwritten, added = [], []
    with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as bz:
        for rel, _ in plan:
            p = root / rel
            if p.exists():
                bz.write(p, rel)
                overwritten.append(rel)
            else:
                added.append(rel)
        for rel in stale:
            bz.write(root / rel, rel)
    req_changed = any(rel == "requirements.txt" for rel, _ in plan)
    written = []
    try:
        for rel, new in plan:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".update-tmp")
            tmp.write_bytes(new)
            tmp.replace(p)
            written.append(rel)
        for rel in stale:
            (root / rel).unlink(missing_ok=True)
    except OSError as e:                              # 写到一半失败：已经换上的换回去
        _restore(root, backup, overwritten + stale, [r for r in added if r in written])
        raise UpdateError(f"写文件失败（{e}），已经换回原来的版本") from e
    state = {"from": info["current"], "to": ver, "sha256": sha, "source": info["source"], "backup": backup.name,
             "overwritten": overwritten, "added": added, "removed": stale, "ts": time.time(), "state": "applied", "fails": 0}
    _save_state(root, state)
    log.info("更新到 %s：换了 %d 个文件、新加 %d 个、拿掉 %d 个旧的（备份 %s）", ver, len(overwritten), len(added),
             len(stale), backup.name)
    if req_changed and pip:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(root / "requirements.txt")],
                           cwd=str(root), timeout=600, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:  # noqa: BLE001
            log.warning("装新依赖没成功（重启后可能缺包，可以双击 install.bat 再装一次）：%s", e)
    return state


def state_path(root: Path) -> Path:
    return Path(root) / "data" / "update" / "last.json"


def load_state(root: Path) -> dict:
    try:
        return json.loads(state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(root: Path, st: dict):
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _local_code(root: Path) -> list[str]:
    """本地的代码文件（相对路径）：CODE_DIRS 里的 CODE_EXT 文件，加上根目录里旧版本放过的 .bat。"""
    out = []
    for d in CODE_DIRS:
        base = root / d
        if base.is_dir():
            out += [p.relative_to(root).as_posix() for p in base.rglob("*")
                    if p.is_file() and p.suffix in CODE_EXT and "__pycache__" not in p.parts]
    out += [n for n in LEGACY_ROOT if (root / n).is_file()]
    return out


def _restore(root: Path, backup: Path, overwritten: list[str], added: list[str]):
    with zipfile.ZipFile(backup) as bz:
        for rel in overwritten:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(bz.read(rel))
    for rel in added:
        (root / rel).unlink(missing_ok=True)


def rollback(root: Path) -> dict:
    """换回更新前的版本（用最近一次更新的备份）。"""
    root = Path(root)
    st = load_state(root)
    if not st or st.get("state") not in ("applied", "confirmed"):
        raise UpdateError("没有可以换回去的更新")
    backup = root / "data" / "backup" / st["backup"]
    if not backup.exists():
        raise UpdateError(f"找不到备份 {backup.name}")
    _restore(root, backup, (st.get("overwritten") or []) + (st.get("removed") or []), st.get("added") or [])
    st["state"] = "rolled_back"
    st["rolled_back_at"] = time.time()
    _save_state(root, st)
    log.info("换回了 %s", st.get("from"))
    return st


def confirm_later(root: Path):
    """程序启动时调：刚更新过的话，正常跑够 2 分钟就算更新成功（之后再出错就不自动回滚了）。"""
    import threading

    def done():
        st = load_state(root)
        if st.get("state") == "applied":
            st["state"] = "confirmed"
            st["fails"] = 0
            _save_state(root, st)
    if load_state(root).get("state") == "applied":
        t = threading.Timer(CONFIRM_SECONDS, done)
        t.daemon = True
        t.start()


def on_quick_exit(root: Path) -> str:
    """托盘发现程序刚启动就退出时调：刚更新过、连续两次这样，就自动换回旧版。返回要提示的话（空＝没做什么）。"""
    st = load_state(root)
    if st.get("state") != "applied":
        return ""
    st["fails"] = int(st.get("fails") or 0) + 1
    _save_state(root, st)
    if st["fails"] < 2:
        return ""
    try:
        rollback(root)
    except UpdateError as e:
        return f"新版本起不来，自动换回也没成功：{e}"
    return f"新版本 {st.get('to')} 起不来，已经自动换回 {st.get('from')}"


async def check_loop(cfg, db, every_hours: float = 24):
    """后台：启动 1 分钟后看一次，之后每天看一次。结果存进数据库，小屋里显示。"""
    import asyncio
    if not (cfg.get("update") or {}).get("auto_check", True):
        return
    await asyncio.sleep(60)
    while True:
        try:
            info = await asyncio.to_thread(check, cfg)
            db.set("update_info", info)
            if info["newer"]:
                log.info("有新版本 %s（现在是 %s）：在小屋“设置 → 软件更新”里点更新", info["latest"], info["current"])
        except Exception as e:  # noqa: BLE001
            log.debug("检查更新失败：%s", e)
        await asyncio.sleep(every_hours * 3600)


def main(argv: list[str]) -> int:
    from ailife.core.config import load_config
    cfg = load_config()
    cmd = (argv or ["check"])[0]
    try:
        if cmd == "check":
            info = check(cfg)
            if info["error"] and not info["latest"]:
                print("检查失败：", info["error"])
                return 1
            print(f"现在：{info['current']}　最新：{info['latest']}（{info['source']}）")
            print("有新版本：python run.py update apply（或者在小屋里点更新）" if info["newer"] else "已经是最新的了")
            return 0
        if cmd == "apply":
            st = apply(cfg)
            print(f"更新好了：{st['from']} → {st['to']}（备份在 data/backup/{st['backup']}）。重启程序生效（托盘里点重启）")
            return 0
        if cmd == "rollback":
            st = rollback(cfg.root)
            print(f"换回 {st['from']} 了，重启程序生效")
            return 0
    except UpdateError as e:
        print("没做成：", e)
        return 1
    print(__doc__)
    return 1
