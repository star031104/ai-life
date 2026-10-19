"""QQ 一键配置（scripts\\qq_setup.bat → python run.py qqsetup）。

1. 找到 NapCat，读它自己的配置：WebSocket 端口、token（不用你手抄 token）
2. 连上去试一下：她的小号是谁、好友里哪个是你（列出来让你选）
3. 问一句：Telegram 还要不要
4. 写 config.yaml 的 qq 一节（token 放进 .env），先备份，写完重新读一遍核对，不对就恢复
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from ailife.core.config import ROOT

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
                    if "ai-life-group" in str(d).lower() or d.name.lower().endswith("-group"):
                        continue                         # 群聊角色（ai-life-group）那份 NapCat，是另一个号
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


# ---------------- 改 config.yaml（只动 qq 一节和 telegram.bot_token 那一行） ----------------
def set_qq_block(text: str, qq: dict) -> str:
    """把顶层的 qq: 一节换成新的（没有就加到末尾）。别的内容和注释原样保留。"""
    import yaml
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"^qq:\s*(#.*)?$", ln)), None)
    block = "qq:\n" + "\n".join("  " + ln for ln in yaml.safe_dump(qq, allow_unicode=True, sort_keys=False,
                                                                     default_flow_style=None).splitlines())
    if start is None:
        body = text.rstrip("\n")
        return (body + "\n\n" if body else "") + "# ---------------- QQ（qq_setup 写的） ----------------\n" + block + "\n"
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][:1] in (" ", "\t")):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():       # 一节后面的空行留着
        end -= 1
    return "\n".join(lines[:start] + block.splitlines() + lines[end:]) + "\n"


def disable_tg(text: str) -> str:
    """telegram.bot_token 改成空（原来的值留在注释里，想恢复就改回去）。"""
    lines = text.splitlines()
    in_tg = False
    for i, ln in enumerate(lines):
        if re.match(r"^telegram:\s*(#.*)?$", ln):
            in_tg = True
            continue
        if in_tg and ln[:1] not in (" ", "\t", "") and ln.strip():
            break
        m = re.match(r"^(\s+)bot_token:\s*(.*?)\s*(#.*)?$", ln) if in_tg else None
        if m:
            old = m.group(2)
            if old in ('""', "''", ""):
                return text
            lines[i] = f'{m.group(1)}bot_token: ""   # 只用 QQ（原来是 {old}，想恢复 Telegram 就改回去）'
            return "\n".join(lines) + "\n"
    return text


# ---------------- 交互 ----------------
def ask(prompt: str, default: str = "") -> str:
    try:
        v = input(f"{prompt}{f' [{default}]' if default else ''}：").strip()
    except EOFError:
        v = ""
    return v or default


def yes(prompt: str, default: bool = True) -> bool:
    v = ask(prompt + ("（Y/n）" if default else "（y/N）")).lower()
    return default if not v else v.startswith("y") or v in ("是", "要", "好")


def say(ok: bool | None, text: str):
    print(("✅ " if ok else "❌ " if ok is False else "   ") + text)


def main(argv: list[str]) -> int:
    from ailife.channels.qq.onebot import send_once_sync
    print("=" * 56 + "\n  QQ 一键配置\n" + "=" * 56)
    print("还没装 NapCat 的话，直接关掉这个窗口，运行 setup.bat 选 QQ（会自动下载、配置 NapCat）。\n"
          "已经装好的：确认 NapCat 窗口开着、她的小号登录着。\n")

    # 1. NapCat
    dirs = find_napcat_configs()
    ob = None
    for d in dirs:
        ob = read_onebot(d)
        if ob:
            break
    while not ob:
        if dirs:
            say(False, "找到了 NapCat，但它的网络配置里没有启用的 WebSocket 服务器。")
            print("   打开 NapCat 的 WebUI → 网络配置 → 新建 WebSocket 服务器（127.0.0.1、3001、消息格式 Array），保存后再来。")
        p = ask("NapCat 文件夹在哪（把文件夹拖进这个窗口再回车；直接回车＝退出）")
        if not p:
            return 1
        d = config_dir_from(p)
        ob = read_onebot(d) if d else None
        dirs = [d] if d else []
        if not d:
            say(False, "这个文件夹里没找到 NapCat 的配置（napcat\\config）")
    say(True, f"NapCat 配置：{ob['file']}")
    say(True, f"WebSocket：{ob['url']}" + ("，有 token（自动读出来了）" if ob["token"] else "，没设 token"))
    if ob["format"] != "array":
        say(False, "消息格式是 String，要改成 Array：WebUI → 网络配置 → 编辑这个 WebSocket 服务器 → 消息格式选 Array → 保存")
        if not yes("改好了吗？没改的话她会看不懂图片、表情、@，继续吗", False):
            return 1
    if ob["self_msg"]:
        say(None, "提示：“上报自身消息”开着，建议关掉（程序会自己过滤，不关也能用）")

    # 2. 连一下，列好友
    bot_uin, friends, connected = ob["uin"], [], False
    try:
        info = send_once_sync(ob["url"], ob["token"], "get_login_info", {}, 6)
        bot_uin = int(info.get("user_id") or bot_uin)
        say(True, f"连上了：她的小号是 {info.get('nickname')}（{bot_uin}）")
        connected = True
        fl = send_once_sync(ob["url"], ob["token"], "get_friend_list", {}, 8)
        friends = [x for x in (fl if isinstance(fl, list) else []) if int(x.get("user_id") or 0) != bot_uin]
    except Exception as e:  # noqa: BLE001
        say(False, f"连不上 NapCat：{e}")
        print("   NapCat 窗口要开着、小号要登录着。先把配置写好，等会儿开 start.bat 时会自动重连。")

    # 3. 你是哪个
    from ailife.core.config import load_config
    try:
        old_cfg = load_config()
    except Exception:  # noqa: BLE001
        old_cfg = {}
    try:                                         # 你 config.yaml 里自己写过的 qq 设置（不是默认值）原样保留
        import yaml
        raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        raw = {}
    old_q = dict(raw.get("qq") or {}) if isinstance(raw, dict) else {}
    default_owner = str(old_q.get("owner_id") or "") if str(old_q.get("owner_id") or "0") != "0" else ""
    if friends:
        print("\n她的小号的好友：")
        for i, x in enumerate(friends[:30], 1):
            name = x.get("remark") or x.get("nickname") or ""
            print(f"  {i}. {name}（{x.get('user_id')}）")
        if not default_owner and len(friends) == 1:
            default_owner = "1"
    owner = 0
    q = "你自己的 QQ 是哪个？输入上面的编号，或者直接输入 QQ 号" if friends else "输入你自己的 QQ 号（不是她的小号）"
    for _ in range(5):
        v = ask(q, default_owner)
        if v.isdigit() and friends and 1 <= int(v) <= min(30, len(friends)) and len(v) <= 2:
            owner = int(friends[int(v) - 1]["user_id"])
        elif v.isdigit() and len(v) >= 5:
            owner = int(v)
        if owner:
            break
        say(False, "要输入 5 位以上的 QQ 号" + ("，或者上面列表里的编号" if friends else ""))
    if not owner:
        return 1
    is_friend = any(int(x.get("user_id") or 0) == owner for x in friends) if connected else None
    if owner == bot_uin:
        say(False, "这是她的小号自己的号，要填你自己的 QQ")
        return 1

    # 4. Telegram
    from ailife.channels.telegram.bot import tg_configured
    keep_tg = True
    if old_cfg and tg_configured(old_cfg):
        print()
        keep_tg = yes("Telegram 也继续用吗？选 n 就只用 QQ（随时可以改回来）", True)

    # 5. 写配置
    cfg_path = ROOT / "config.yaml"
    if not cfg_path.exists():
        say(False, "还没有 config.yaml：先运行 setup.bat 做完基本配置")
        return 1
    text = cfg_path.read_text(encoding="utf-8")
    backup = ROOT / "data" / "backup" / f"config.yaml.before-qq-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cfg_path, backup)
    qq = {"enabled": True, "ws_url": ob["url"],
          "access_token": "${QQ_ACCESS_TOKEN}" if ob["token"] else "",
          "owner_id": owner, "private": True,
          "groups": list(old_q.get("groups") or []),
          "napcat_dir": str(napcat_root_of(ob["file"].parent)),       # start.bat 会顺便启动 NapCat
          "napcat_qq": bot_uin or ob["uin"]}
    for k in ("aliases", "poke_back", "signature", "http_url", "group"):
        if k in old_q and old_q[k] not in (None, "", [], {}):
            v = old_q[k]
            qq[k] = dict(v) if isinstance(v, dict) else v
    new = set_qq_block(text, qq)
    if not keep_tg:
        new = disable_tg(new)
    from ailife.core.secrets import read_env, write_env
    env_old = read_env(ROOT)
    if ob["token"]:
        write_env(ROOT, {"QQ_ACCESS_TOKEN": ob["token"]})
    tmp = cfg_path.with_suffix(".yaml.tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, cfg_path)
    try:                                         # 重新读一遍核对
        c = load_config()
        q = c.get("qq") or {}
        assert q.get("enabled") and q.get("ws_url") == ob["url"] and int(q.get("owner_id")) == owner
        assert (q.get("access_token") or "") == ob["token"]
        assert keep_tg == tg_configured(c) or not tg_configured(old_cfg)
    except Exception as e:  # noqa: BLE001
        shutil.copy2(backup, cfg_path)
        if ob["token"] and "QQ_ACCESS_TOKEN" not in env_old:
            write_env(ROOT, {"QQ_ACCESS_TOKEN": ""})
        say(False, f"写完核对不对（{e}），已经恢复原来的 config.yaml，什么都没改")
        return 1
    say(True, f"config.yaml 写好了（原来的备份在 {backup.relative_to(ROOT)}）")
    if ob["token"]:
        say(True, "token 放进了 .env（config.yaml 里只写引用）")
    say(True, "只用 QQ（Telegram 不启动）" if not keep_tg else "QQ 和 Telegram 都用：你在哪边说话她就在哪边回")

    # 6. 还差什么
    print()
    if is_friend is False:
        say(False, f"你（{owner}）还不是她的小号（{bot_uin}）的好友：用你的 QQ 搜 {bot_uin} 加好友，程序开着时会自动同意。")
        print("   不加好友的话，她只能回你，不能主动找你。")
    print("接下来：双击 start.bat（已经开着的话关掉重开）。以后只要开 start.bat，它会顺便把 NapCat 也启动（用快速登录，一般不用再扫码）。")
    print("日志里出现“QQ 已连上 NapCat”后，用你的 QQ 跟她说句话。")
    print("手机上看小屋：在 QQ 里给她发 /小屋。")
    return 0
