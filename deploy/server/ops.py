"""放在服务器上的小管家（11.2）：每天备份、每 5 分钟看一眼三个她在不在线、每天算一份“这一周的体检”。

用法（用 ai-life 的虚拟环境跑，里面有 aiohttp 和 yaml）：
    python deploy/server/ops.py watch     # 每 5 分钟：在线状态写进小屋首页；掉线超过 10 分钟就在 QQ 上提醒你
    python deploy/server/ops.py backup    # 每天：三个数据库 + 配置 + 本地人设打包，留 30 天
    python deploy/server/ops.py report    # 每天：最近 7 天的用量、花费、消息数、出错次数 → 小屋首页的“体检”
    python deploy/server/ops.py all       # 三个都做一遍（装好后试一下）

目录用环境变量改：AILIFE_HOME（默认 ~/ailife，里面放 ai-life / ai-life-group / ai-life-wechat）、
AILIFE_WWW（默认 /var/www/ailife，小屋首页放这）、AILIFE_BACKUPS（默认 $AILIFE_HOME/backups）。
提醒发给 ai-life 的 qq.owner_id；先用 QQ 群小满的号发，发不出去再用有希的号发（消息开头带【小屋提醒】）。
.env（密钥）不进备份包：恢复时照原样再填一次。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path(os.environ.get("AILIFE_HOME") or Path.home() / "ailife")
WWW = Path(os.environ.get("AILIFE_WWW") or "/var/www/ailife")
BACKUPS = Path(os.environ.get("AILIFE_BACKUPS") or HOME / "backups")
KEEP_DAYS = int(os.environ.get("AILIFE_KEEP_DAYS") or 30)
STATE = HOME / "ops_state.json"

BOTS = [  # key, 名字, 目录, 网页端口, 服务名, 网址路径, 数据库
    {"key": "yuki", "label": "有希", "kind": "QQ 私聊", "dir": "ai-life", "home": "yuki", "port": 8765, "svc": "ai-life", "path": "/yuki/",
     "db": "data/ailife.db"},
    {"key": "qq", "label": "QQ 群的小满", "kind": "QQ 群", "dir": "ai-life-group", "home": "qq", "port": 8767, "svc": "ai-life-group",
     "path": "/qq/", "db": "data/group.db"},
    {"key": "wx", "label": "微信群的小满", "kind": "微信群", "dir": "ai-life-wechat", "home": "wechat", "port": 8768, "svc": "ai-life-wechat",
     "path": "/wx/", "db": "data/group.db"},
]
CODE_DIR = Path(os.environ.get("AILIFE_CODE") or HOME / "app")   # 12.0 起三个在一个仓库里：代码在这，她们的家在 home/<名字>
TZ_OFFSET = timedelta(hours=8)          # 北京时间


def now_cn() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + TZ_OFFSET


def bdir(b: dict) -> Path:
    """她的家：新的（app/home/yuki）有就用新的，否则是合并以前的老目录（ailife/ai-life）。"""
    new = CODE_DIR / "home" / b["home"]
    return new if (new / "config.yaml").exists() else HOME / b["dir"]


# ---------------- 读配置 ----------------
def read_env(d: Path) -> dict:
    out = {}
    p = d / ".env"
    if p.exists():
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", ln)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def read_cfg(d: Path) -> dict:
    try:
        import yaml
        c = yaml.safe_load((d / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    env = read_env(d)

    def subst(v):
        if isinstance(v, str):
            return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: env.get(m.group(1), os.environ.get(m.group(1), "")), v)
        if isinstance(v, dict):
            return {k: subst(x) for k, x in v.items()}
        if isinstance(v, list):
            return [subst(x) for x in v]
        return v
    return subst(c)


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_state(st: dict):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def write_www(name: str, data: dict):
    try:
        WWW.mkdir(parents=True, exist_ok=True)
        tmp = WWW / (name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(WWW / name)
    except OSError as e:
        print("写不了小屋首页的数据：", e, file=sys.stderr)


# ---------------- QQ（OneBot）----------------
async def onebot(url: str, token: str, action: str, params: dict | None = None, timeout: float = 8.0):
    import aiohttp
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url, headers=headers, timeout=timeout, heartbeat=None) as ws:
            echo = f"ops{time.time()}"
            await ws.send_json({"action": action, "params": params or {}, "echo": echo})
            t_end = time.time() + timeout
            while time.time() < t_end:
                msg = await ws.receive(timeout=max(0.5, t_end - time.time()))
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    continue
                d = json.loads(msg.data)
                if d.get("echo") == echo:
                    return d
    raise TimeoutError(f"{action} 没有回应")


def qq_conf(key: str) -> tuple[str, str, int]:
    b = next(x for x in BOTS if x["key"] == key)
    c = read_cfg(bdir(b))
    q = c.get("qq") or {}
    return str(q.get("ws_url") or ""), str(q.get("access_token") or q.get("token") or ""), int(q.get("owner_id") or 0)


async def qq_online(key: str) -> tuple[bool | None, str]:
    url, token, _ = qq_conf(key)
    if not url:
        return None, "没配"
    try:
        d = await onebot(url, token, "get_status")
    except Exception as e:  # noqa: BLE001
        return False, f"NapCat 连不上（{str(e)[:60]}）"
    data = d.get("data") or {}
    online = bool(data.get("online", d.get("status") == "ok"))
    return online, "" if online else "QQ 掉线了（多半要重新扫码）"


async def notify(text: str) -> bool:
    _, _, owner = qq_conf("yuki")
    if not owner:
        return False
    for key in ("qq", "yuki"):
        url, token, _ = qq_conf(key)
        if not url:
            continue
        try:
            d = await onebot(url, token, "send_private_msg", {"user_id": owner, "message": [
                {"type": "text", "data": {"text": "【小屋提醒】" + text}}]})
            if d.get("status") == "ok" or d.get("retcode") == 0:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


# ---------------- 今天花了多少（共享账本） ----------------
def spend_today() -> dict:
    """三个程序今天各花了多少（人民币，按 llm_stats 里估算的美元），写进 budget.json 给三个程序看。"""
    day = now_cn().strftime("%Y-%m-%d")
    c = (read_cfg(bdir(BOTS[0])).get("llm") or {}).get("budget") or {}
    rate = float(c.get("usd_cny", 7.2) or 7.2)
    cap = float(os.environ.get("AILIFE_BUDGET") or c.get("daily_cny", 0) or 0)
    bots = {}
    for b in BOTS:
        p = bdir(b) / b["db"]
        if not p.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=20)
            table = "kv" if con.execute("SELECT 1 FROM sqlite_master WHERE name='kv'").fetchone() else "state"
            s = _kv(con, table, f"llm_stats_{day}").get(f"llm_stats_{day}") or {}
            con.close()
            bots[b["key"]] = round(float(s.get("usd", 0) or 0) * rate, 4) if isinstance(s, dict) else 0.0
        except sqlite3.Error:
            continue
    out = {"day": day, "bots": bots, "cap": cap, "total": round(sum(bots.values()), 3), "ts": time.time()}
    try:
        tmp = HOME / "budget.json.tmp"
        tmp.write_text(json.dumps(out), encoding="utf-8")
        os.chmod(tmp, 0o644)
        tmp.replace(HOME / "budget.json")
    except OSError as e:
        print("写不了 budget.json：", e, file=sys.stderr)
    return out


# ---------------- watch ----------------
def http_json(port: int, path: str = "/api/state", timeout: float = 8.0) -> dict | None:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def svc_active(name: str) -> bool:
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", name], timeout=10).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def machine() -> dict:
    du = shutil.disk_usage("/")
    mem = {}
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            k, v = ln.split(":", 1)
            mem[k] = int(v.split()[0])
    except Exception:  # noqa: BLE001
        pass
    tot, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    try:
        load = os.getloadavg()[0]
    except OSError:
        load = 0.0
    return {"disk_used": round(du.used / du.total * 100, 1), "disk_free_gb": round(du.free / 2 ** 30, 1),
            "mem_used": round((tot - avail) / tot * 100, 1) if tot else 0, "mem_total_gb": round(tot / 2 ** 20, 1),
            "load": round(load, 2), "cpus": os.cpu_count() or 1}


async def check_bot(b: dict) -> dict:
    out = {"key": b["key"], "label": b["label"], "kind": b["kind"], "path": b["path"], "service": svc_active(b["svc"]),
           "ok": False, "why": "", "today": 0, "her_today": 0, "name": b["label"]}
    st = http_json(b["port"])
    if not out["service"]:
        out["why"] = "程序没在运行"
        return out
    if st is None:
        out["why"] = "小屋打不开（程序可能卡住了）"
        return out
    p = st.get("persona") or {}
    out["name"] = p.get("name") or st.get("name") or b["label"]
    if b["key"] == "yuki":
        out["today"] = int((st.get("stats") or {}).get("today") or 0)
        link = bool((st.get("apps") or {}).get("qq_online"))
    elif b["key"] == "wx":
        w = st.get("wechat") or {}
        out["today"] = int((st.get("kpi") or {}).get("today") or 0)
        out["her_today"] = int((st.get("kpi") or {}).get("her_today") or 0)
        link = bool(w.get("ready"))
        if not link:
            out["why"] = "微信没登录" if w.get("not_logged_in") else ("微信没在运行" if not w.get("running") else
                                                                    (w.get("error") or "微信没接上"))
    else:
        out["today"] = int((st.get("kpi") or {}).get("today") or 0)
        out["her_today"] = int((st.get("kpi") or {}).get("her_today") or 0)
        link = bool((st.get("qq") or {}).get("ready"))
    if b["key"] in ("yuki", "qq"):
        online, why = await qq_online(b["key"])
        if online is False:
            link, out["why"] = False, why
        elif not link:
            out["why"] = "程序没连上 NapCat"
    out["ok"] = bool(link)
    return out


async def watch():
    st = load_state()
    bots = [await check_bot(b) for b in BOTS]
    m = machine()
    alerts = []
    for x in bots:
        s = st.setdefault("bots", {}).setdefault(x["key"], {"fails": 0, "alerted": 0, "down_since": 0})
        if x["ok"]:
            if s.get("alerted"):
                alerts.append(f"{x['label']}恢复了 ✓")
            s.update(fails=0, alerted=0, down_since=0)
        else:
            s["fails"] = int(s.get("fails", 0)) + 1
            s["down_since"] = s.get("down_since") or time.time()
            x["down_since"] = s["down_since"]
            again = s.get("alerted") and time.time() - float(s["alerted"]) > 6 * 3600       # 一直没好：6 小时再说一次
            if s["fails"] >= 2 and (not s.get("alerted") or again):
                alerts.append(f"{x['label']}掉线了：{x['why']}。打开小屋看看：{x['path']}")
                s["alerted"] = time.time()
    ms = st.setdefault("machine", {"alerted": 0})
    if (m["disk_used"] > 90 or m["mem_used"] > 92) and time.time() - float(ms.get("alerted") or 0) > 12 * 3600:
        alerts.append(f"服务器快满了：硬盘 {m['disk_used']}%、内存 {m['mem_used']}%")
        ms["alerted"] = time.time()
    sp = spend_today()
    bs = st.setdefault("budget", {})
    if sp["cap"] > 0:
        for lvl, name in ((1.0, "到上限了，后台的活先停，聊天也快停了"), (0.8, "用到八成了")):
            if sp["total"] >= sp["cap"] * lvl and bs.get(str(lvl)) != sp["day"]:
                alerts.append(f"今天的花费{name}：约 ¥{sp['total']:.2f} / ¥{sp['cap']:g}")
                bs[str(lvl)] = sp["day"]
                break
    sent = False
    if alerts:
        sent = await notify("\n".join(alerts))
        st.setdefault("alerts", []).append({"ts": time.time(), "text": "\n".join(alerts), "sent": sent})
        st["alerts"] = st["alerts"][-30:]
    save_state(st)
    last_bk = sorted(BACKUPS.glob("ailife-*.tar.gz"))[-1:] if BACKUPS.exists() else []
    write_www("status.json", {"ts": time.time(), "bots": bots, "machine": m, "spend": sp,
                              "alerts": list(reversed(st.get("alerts", [])))[:8],
                              "backup": {"last": last_bk[0].name if last_bk else "",
                                         "size_mb": round(last_bk[0].stat().st_size / 2 ** 20, 2) if last_bk else 0,
                                         "count": len(list(BACKUPS.glob("ailife-*.tar.gz"))) if BACKUPS.exists() else 0}})
    for a in alerts:
        print(("已提醒：" if sent else "没提醒出去：") + a)
    return bots


# ---------------- backup ----------------
def backup() -> Path:
    BACKUPS.mkdir(parents=True, exist_ok=True)
    day = now_cn().strftime("%Y-%m-%d")
    dst = BACKUPS / f"ailife-{day}.tar.gz"
    with tempfile.TemporaryDirectory() as td:
        stage = Path(td)
        for b in BOTS:
            d = bdir(b)
            if not d.exists():
                continue
            out = stage / b["dir"]
            (out / "data").mkdir(parents=True, exist_ok=True)
            for db in (d / "data").glob("*.db"):
                try:                                    # sqlite 自带的在线备份：程序在写也不会备坏
                    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
                    tgt = sqlite3.connect(out / "data" / db.name)
                    src.backup(tgt)
                    tgt.close()
                    src.close()
                except sqlite3.Error as e:
                    print("备份", db, "失败：", e, file=sys.stderr)
            for pat in ("config.yaml", "config.llm.yaml", "persona/local_*.md", "data/seed*.yaml", "data/llm_custom*.yaml"):
                for f in d.glob(pat):
                    (out / f.relative_to(d)).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, out / f.relative_to(d))
            photos = d / "data" / "photos"
            if photos.exists():
                shutil.copytree(photos, out / "data" / "photos", dirs_exist_ok=True)
        with tarfile.open(dst, "w:gz") as tar:
            tar.add(stage, arcname=f"ailife-{day}")
    os.chmod(dst, 0o600)
    try:                                              # root 的定时任务做的备份：交给普通用户，电脑上才能拉下来
        o = HOME.stat()
        os.chown(dst, o.st_uid, o.st_gid)
        os.chown(BACKUPS, o.st_uid, o.st_gid)
    except OSError:
        pass
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in BACKUPS.glob("ailife-*.tar.gz"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
    latest = BACKUPS / "latest.tar.gz"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(dst.name)
    except OSError:
        pass
    print("备份好了：", dst, f"{dst.stat().st_size / 2 ** 20:.2f} MB")
    return dst


# ---------------- report ----------------
def _kv(con, table: str, prefix: str) -> dict:
    col_k, col_v = ("k", "v") if table == "kv" else ("key", "value")
    out = {}
    for r in con.execute(f"SELECT {col_k}, {col_v} FROM {table} WHERE {col_k} LIKE ?", (prefix + "%",)):
        try:
            out[r[0]] = json.loads(r[1])
        except (TypeError, ValueError):
            out[r[0]] = r[1]
    return out


def journal_errors(svc: str, since: str = "7 days ago") -> int:
    try:
        r = subprocess.run(["journalctl", "-u", svc, "--since", since, "--no-pager", "-o", "cat"],
                           capture_output=True, text=True, timeout=60)
        return sum(1 for ln in r.stdout.splitlines() if re.search(r"\b(ERROR|CRITICAL)\b|Traceback", ln))
    except Exception:  # noqa: BLE001
        return -1


def report() -> dict:
    today = now_cn().date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    t0 = (datetime.combine(today - timedelta(days=6), datetime.min.time()) - TZ_OFFSET).replace(tzinfo=timezone.utc).timestamp()
    out = {"ts": time.time(), "days": days, "bots": []}
    for b in BOTS:
        p = bdir(b) / b["db"]
        x = {"key": b["key"], "label": b["label"], "calls": [0] * 7, "fail": [0] * 7, "usd": [0.0] * 7,
             "tok_in": [0] * 7, "tok_out": [0] * 7, "msgs": [0] * 7, "her": [0] * 7, "errors": journal_errors(b["svc"])}
        if not p.exists():
            out["bots"].append(x)
            continue
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=20)
        table = "kv" if con.execute("SELECT 1 FROM sqlite_master WHERE name='kv'").fetchone() else "state"
        stats = _kv(con, table, "llm_stats_")
        for i, d in enumerate(days):
            s = stats.get(f"llm_stats_{d}") or {}
            if isinstance(s, dict):
                x["calls"][i] = int(s.get("calls", 0))
                x["fail"][i] = int(s.get("calls", 0)) - int(s.get("ok", 0))
                x["usd"][i] = round(float(s.get("usd", 0.0)), 4)
                x["tok_in"][i] = int(s.get("tok_hit", 0)) + int(s.get("tok_miss", 0))
                x["tok_out"][i] = int(s.get("tok_out", 0))
        if b["key"] == "yuki":
            q = "SELECT ts, role FROM messages WHERE role IN ('user','assistant') AND ts>=?"
        else:
            q = "SELECT ts, role FROM qq_group_messages WHERE ts>=?"
        for ts, role in con.execute(q, (t0,)):
            d = (datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None) + TZ_OFFSET).date().isoformat()
            if d in days:
                i = days.index(d)
                if role == "assistant":
                    x["her"][i] += 1
                else:
                    x["msgs"][i] += 1
        if b["key"] != "yuki":
            try:
                x["learned"] = con.execute("SELECT COUNT(*) FROM qq_knowledge WHERE ts>=?", (t0,)).fetchone()[0]
            except sqlite3.Error:
                x["learned"] = 0
            sh = _kv(con, "kv", "qq_shares").get("qq_shares") or []
            x["shares"] = sum(1 for s in sh if isinstance(s, dict) and s.get("kind") in ("news", "photo") and s.get("ts", 0) >= t0)
            try:
                x["plans"] = con.execute("SELECT COUNT(*) FROM qq_plans WHERE fired>=?", (t0,)).fetchone()[0]
            except sqlite3.Error:
                x["plans"] = 0
        else:
            x["learned"] = con.execute("SELECT COUNT(*) FROM memories WHERE owner='self' AND content LIKE '我查过「%' "
                                       "AND ts>=?", (t0,)).fetchone()[0]
        con.close()
        out["bots"].append(x)
    out["total_usd"] = round(sum(sum(b["usd"]) for b in out["bots"]), 3)
    out["machine"] = machine()
    write_www("report.json", out)
    if today.weekday() == 0:                          # 每周一留一份
        hist = HOME / "reports"
        hist.mkdir(exist_ok=True)
        (hist / f"{today:%G-W%V}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("体检写好了：最近 7 天约 $%.3f" % out["total_usd"])
    return out


def main():
    cmd = (sys.argv[1:] or ["watch"])[0]
    if cmd in ("backup", "all"):
        backup()
    if cmd in ("report", "all"):
        report()
    if cmd in ("watch", "all"):
        asyncio.run(watch())
    if cmd == "test-notify":
        print("发出去了" if asyncio.run(notify("这是一条测试提醒，收到就说明掉线提醒能用")) else "没发出去")


if __name__ == "__main__":
    main()
