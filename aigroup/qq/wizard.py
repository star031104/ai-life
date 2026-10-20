"""配置向导（setup.bat → python run.py setup）：一次配好群聊角色。

1. 大模型接口：填你自己的接口地址和 Key（任何 OpenAI 兼容的）→ 测连通 → 选模型（放在最后一步做）
2. 她的 NapCat：电脑上有别的 NapCat（比如 ai-life 那份）就克隆一份，没有就下载；WebSocket 3002、WebUI 6100
3. 用她的 QQ 小号扫一次码，之后自动登录
4. 选她待哪几个群、你自己的 QQ 号、人设、群里怎么叫她、她怎么看你
5. 写 config.yaml（只写和默认不一样的项）和 .env（密钥）；问要不要开机自启、现在启动

    python run.py setup            全部来一遍（已经配好的项回车就保留原样）
    python run.py setup --manual   不碰 NapCat：手动填 WebSocket 地址和 token（Linux / Docker 版 NapCat 用）
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

from aigroup.core.config import CODE, ROOT
from aigroup.core.util import ask, say, yes


def pick_numbers(v: str, n: int) -> list[int]:
    """“1,3 4” → [0, 2, 3]（编号从 1 开始；越界的丢掉）。"""
    out = []
    for x in re.split(r"[\s,，、]+", v.strip()):
        if x.isdigit() and 1 <= int(x) <= n and int(x) - 1 not in out:
            out.append(int(x) - 1)
    return out


def list_personas(root: Path = ROOT) -> list[dict]:
    from aigroup.persona import load_persona
    out = []
    for f in sorted((root / "persona").glob("*.md")):
        if f.name == "template.md":
            continue
        try:
            p = load_persona(f)
            out.append({"file": f"persona/{f.name}", "name": p.name, "identity": getattr(p, "identity", "")})
        except Exception:  # noqa: BLE001
            continue
    return out


def build_config(old: dict, *, persona_file: str, keys: dict[str, str], port: int, has_token: bool, owner: int,
                 groups: list[int], aliases: list[str], owner_note: str, napcat_dir: str, napcat_qq: int) -> str:
    """config.yaml 只写和 config.example.yaml 不一样的项（别的用默认值）；原来手动加的设置保留。"""
    d = dict(old or {})
    d["persona_file"] = persona_file
    if keys:
        d["keys"] = {**(d.get("keys") or {}), **{k: f"${{{v}}}" for k, v in keys.items()}}
    q = dict(d.get("qq") or {})
    q.update({"ws_url": f"ws://127.0.0.1:{port}", "access_token": "${QQ_ACCESS_TOKEN}" if has_token else "",
              "owner_id": owner, "groups": groups, "aliases": aliases, "napcat_dir": napcat_dir,
              "napcat_qq": napcat_qq})
    g = dict(q.get("group") or {})
    g["owner_note"] = owner_note
    q["group"] = g
    d["qq"] = q
    head = ("# ai-life-group 的设置（setup.bat 写的；没写的项用 config.example.yaml 里的默认值，想改就从那里抄过来）\n"
            "# 密钥都在 .env 里，这里只写 ${名字} 引用\n\n")
    return head + yaml.safe_dump(d, allow_unicode=True, sort_keys=False, default_flow_style=None)


def other_napcat_uin(nc: Path) -> int | None:
    """电脑上另一份 NapCat（比如 ai-life 的）自动登录的号：她不能用同一个号。"""
    from aigroup.qq.napcat.install import find_other
    src = find_other(nc)
    if not src:
        return None
    for c in (src / "napcat" / "config" / "webui.json", src / "config" / "webui.json"):
        try:
            v = str(json.loads(c.read_text(encoding="utf-8")).get("autoLoginAccount") or "")
            if v.isdigit():
                return int(v)
        except (OSError, ValueError):
            continue
    return None


def main(argv: list[str] | None = None) -> int:
    from aigroup.core.secrets import read_env, write_env
    from aigroup.qq.onebot import send_once_sync
    argv = argv or []
    manual = "--manual" in argv
    print("=" * 56 + "\n  ai-life-group 配置向导（QQ 群聊角色）\n" + "=" * 56)
    print("要准备：给她一个 QQ 小号，并用你的 QQ 把它拉进想让她待的群。\n"
          "⚠️ NapCat 是非官方协议端，有封号风险，一定用小号，别用主号。\n")
    cfg_path = ROOT / "config.yaml"
    old: dict = {}
    if cfg_path.exists():
        try:
            old = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            old = {}
    old_q = old.get("qq") or {}
    env_old = read_env(ROOT)

    # 1. 大模型接口（程序里不预置任何接口，用你自己的）
    from aigroup.llm.custom import load_custom
    have_llm = bool(((load_custom(ROOT).get("llm") or {}).get("profiles") or {})) or any(
        ((old.get("llm") or {}).get("routes") or {}).get("chat") or [])
    print("① 大模型接口：用你自己的（任何 OpenAI 兼容的接口，云端的、本机跑的都行），要准备好接口地址和 Key")
    if have_llm:
        own_llm = yes("  已经配过接口了，要再加一个吗", False)
    else:
        own_llm = True
        say(None, "还没有接口：配完 QQ 之后会让你填地址和 Key、测连通、选模型")
    key_names, env_new = {}, {}

    # 2. NapCat + 扫码
    print("\n② 她的 QQ（NapCat）")
    if manual or sys.platform != "win32":
        url = ask("NapCat 的 WebSocket 地址", str(old_q.get("ws_url") or "ws://127.0.0.1:3002"))
        token = ask("它的 token（没设就回车）", env_old.get("QQ_ACCESS_TOKEN", ""))
        port = int(re.search(r":(\d+)", url).group(1)) if re.search(r":(\d+)", url) else 3002
        nc_dir, uin = str(old_q.get("napcat_dir") or ""), int(old_q.get("napcat_qq") or 0)
        try:
            info = send_once_sync(url, token, "get_login_info", {}, 6)
            uin = int(info.get("user_id") or uin)
            say(True, f"连上了：{info.get('nickname')}（{uin}）")
        except Exception as e:  # noqa: BLE001
            say(False, f"连不上 NapCat：{e}（先把 NapCat 开起来、小号登录好再来）")
            return 1
    else:
        from aigroup.qq.napcat.install import oneclick
        nc = Path(str(old_q.get("napcat_dir") or "").strip() or ROOT / "napcat")
        avoid = other_napcat_uin(nc)
        if avoid:
            say(None, f"电脑上另一份 NapCat 登录的是 {avoid}（比如 ai-life 的号）：她要用另一个小号扫码")
        m = re.search(r":(\d+)", str(old_q.get("ws_url") or ""))
        try:
            r = oneclick(ROOT, proxy=str(old.get("proxy") or ""), nc=nc, port=int(m.group(1)) if m else 3002,
                         avoid_uin=avoid)
        except Exception as e:  # noqa: BLE001
            say(False, str(e))
            return 1
        if not r:
            return 1
        port, token, uin, nc_dir = r["port"], r["token"], r["uin"], str(r["dir"])
        url = f"ws://127.0.0.1:{port}"

    # 3. 选群
    print("\n③ 她待哪几个群")
    groups: list[dict] = []
    for _ in range(6):
        try:
            gl = send_once_sync(url, token, "get_group_list", {}, 8)
            groups = [g for g in (gl if isinstance(gl, list) else []) if g.get("group_id")]
        except Exception as e:  # noqa: BLE001
            say(False, f"读不到群列表：{e}")
        if groups:
            break
        ask(f"这个号（{uin}）还不在任何群里：先用你的 QQ 把它拉进群，然后回车刷新")
    if not groups:
        say(False, "还是没有群。拉它进群后再运行一次 setup.bat（已经弄好的不会重来）")
        return 1
    for i, g in enumerate(groups[:40], 1):
        print(f"  {i}. {g.get('group_name') or ''}（{g['group_id']}，{g.get('member_count') or '?'} 人）")
    old_groups = [int(x) for x in old_q.get("groups") or [] if str(x).isdigit()]
    default = ",".join(str(i + 1) for i, g in enumerate(groups[:40]) if int(g["group_id"]) in old_groups) or \
        ("1" if len(groups) == 1 else "")
    picked: list[int] = []
    for _ in range(5):
        idx = pick_numbers(ask("输入编号，多个用逗号隔开（白名单：别的群她一律不说话）", default), min(40, len(groups)))
        picked = [int(groups[i]["group_id"]) for i in idx]
        if picked:
            break
        say(False, "至少选一个群")
    if not picked:
        return 1

    # 4. 你、人设、名字
    print("\n④ 你和她")
    owner_default = str(old_q.get("owner_id") or "")
    owner = 0
    for _ in range(5):
        v = ask("你自己的 QQ 号（她把你当群里的老朋友）", "" if owner_default == "0" else owner_default)
        if v.isdigit() and len(v) >= 5 and int(v) != uin:
            owner = int(v)
            break
        say(False, "要输入你自己的 QQ 号（5 位以上，不是她的小号）")
    if not owner:
        return 1
    ps = list_personas()
    print("她用哪个人设？（persona/ 里的文件；自己写：复制 persona/template.md 改好放进去，再来选）")
    for i, p in enumerate(ps, 1):
        print(f"  {i}. {p['name']}（{p.get('identity') or '—'}）  {p['file']}")
    old_pf = str(old.get("persona_file") or "persona/xiaoman.md")
    dflt = next((str(i) for i, p in enumerate(ps, 1) if old_pf.endswith(p["file"])), "1")
    v = ask("选哪个", dflt)
    chosen = ps[int(v) - 1] if v.isdigit() and 1 <= int(v) <= len(ps) else (ps[0] if ps else
                                                                          {"file": old_pf, "name": "她"})
    old_alias = [a for a in old_q.get("aliases") or [] if a]
    al = ask(f"群里除了“{chosen['name']}”，大家还会怎么叫她？（逗号隔开，可以不填）", "，".join(old_alias))
    aliases = [a.strip() for a in re.split(r"[,，、\s]+", al) if len(a.strip()) >= 2]
    note = ask("她怎么看你？（一句话）",
               str((old_q.get("group") or {}).get("owner_note") or "把你拉进群的老朋友，爱和你互损"))

    # 5. 写配置
    text = build_config(old, persona_file=chosen["file"], keys=key_names, port=port, has_token=bool(token),
                        owner=owner, groups=picked, aliases=aliases, owner_note=note, napcat_dir=nc_dir,
                        napcat_qq=uin)
    if cfg_path.exists():
        b = ROOT / "data" / "backup" / f"config.yaml.{time.strftime('%Y%m%d-%H%M%S')}"
        b.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cfg_path, b)
        say(None, f"原来的 config.yaml 备份在 {b.relative_to(ROOT)}")
    if token:
        env_new["QQ_ACCESS_TOKEN"] = token
    if env_new:
        write_env(ROOT, env_new)
    tmp = cfg_path.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(cfg_path)
    try:
        from aigroup.core.config import load_config
        c = load_config(cfg_path)
        q = c.get("qq") or {}
        assert [int(x) for x in q.get("groups")] == picked and int(q.get("owner_id")) == owner
        assert (q.get("access_token") or "") == token
    except Exception as e:  # noqa: BLE001
        say(False, f"写完核对不对（{e}）：看一下 config.yaml")
        return 1
    say(True, f"config.yaml 写好了：{chosen['name']}，群 {picked}；密钥在 .env 里（别发给别人）")
    if own_llm:
        import asyncio

        from aigroup.llm.custom import add_interactive
        try:
            asyncio.run(add_interactive(load_config(cfg_path), ("helper", "memory", "night"), ROOT))
        except Exception as e:  # noqa: BLE001
            say(False, f"大模型接口没加上：{e}（之后双击 scripts\\llm.bat 再加）")

    if sys.platform == "win32":
        print()
        if yes("开机自动启动她吗", False):
            subprocess.run(["cmd", "/c", str(CODE / "scripts" / "autostart_on.bat"), "qq", "nopause"], cwd=str(CODE))
        if yes("现在就启动她吗", True):
            subprocess.Popen(["cmd", "/c", str(CODE / "start.bat"), "qq"], cwd=str(CODE),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            say(True, "已经启动了：任务栏右下角多了一个绿色的 G 图标。去群里 @ 她试试")
            return 0
    print("以后双击 start.bat 选 2（或者 start.bat qq；Linux：bash start.sh qq）启动她。日志在 home/qq/data/logs/group.log。")
    return 0
