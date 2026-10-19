"""命令行里管理大模型：`python run.py llm`（或 scripts\\llm.bat）。

加一个：填接口地址和 Key → 测连通、列出模型 → 选一个 → 试一下 → 选它干什么（聊天 / 后台 / 看图 / 画图 / 记忆检索）。
任何 OpenAI 兼容的接口都行（云上的、本机 Ollama / LM Studio / vLLM），程序里不内置地址，填什么用什么。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from ailife.core.config import ROOT
from ailife.llm import store
from ailife.llm.probe import list_models, probe
from ailife.llm.roles import ROLES, default_roles, guess_kind


def _ask(prompt: str, default: str = "", secret: bool = False) -> str:
    try:
        if secret:
            import getpass
            v = getpass.getpass(f"{prompt}（输入时不显示）：")
        else:
            v = input(f"{prompt}{f' [{default}]' if default else ''}：")
    except EOFError:
        v = ""
    return v.strip() or default


def _yes(prompt: str, default: bool = True) -> bool:
    v = _ask(prompt + ("（Y/n）" if default else "（y/N）")).lower()
    return default if not v else v.startswith("y") or v in ("是", "要", "好")


def _pick_roles(model: str) -> tuple[list[str], bool]:
    keys = list(ROLES)
    default = default_roles(model)
    print("\n它用来干什么？（可以多选，用逗号隔开）")
    for i, k in enumerate(keys, 1):
        print(f"  {i}. {ROLES[k]}{'  ← 推荐' if k in default else ''}")
    v = _ask("选哪些", ",".join(str(keys.index(k) + 1) for k in default) or "1")
    roles = [keys[int(x) - 1] for x in v.replace("，", ",").split(",") if x.strip().isdigit()
             and 1 <= int(x) <= len(keys)]
    first = True
    if roles:
        first = _ask("排在最前面当主力（1），还是排最后当备用（2）", "1") != "2"
    return roles, first


async def add_interactive(cfg, root: Path = ROOT) -> str | None:
    proxy = str(cfg.get("proxy") or "")
    print("\n粘贴接口地址（OpenAI 兼容，一般以 /v1 结尾；本机 Ollama 是 http://127.0.0.1:11434/v1）")
    url = _ask("接口地址")
    if not url:
        return None
    key = _ask("API Key（本机模型没有就直接回车）", secret=True)
    print("测连通、读模型列表……")
    models, url, via_proxy, err = await list_models(url, key, proxy)
    if err and not models:
        print(f"❌ 读不到模型列表：{err}")
        if "Key" in err:
            return None
        name = _ask("这个接口不支持列模型的话，直接输入模型名（回车＝放弃）")
        if not name:
            return None
        models = [name]
    else:
        print(f"✅ 连上了：{url}" + ("（走代理）" if via_proxy else "") + f"，有 {len(models)} 个模型")
    shown = models
    if len(shown) > 40:
        kw = _ask(f"模型太多了（{len(shown)} 个），输入关键词筛一下（回车＝全部列出）")
        if kw:
            shown = [m for m in shown if kw.lower() in m.lower()] or shown
    for i, m in enumerate(shown[:80], 1):
        print(f"  {i}. {m}")
    pick = _ask("用哪个？输入编号或者模型名", "1" if len(shown) == 1 else "")
    model = shown[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= min(80, len(shown)) else pick
    if not model:
        return None
    kind = guess_kind(model)
    print(f"试一下（{model}）……")
    ok, secs, text, extra = await probe(url, key, model, kind, proxy, via_proxy)
    if not ok:
        print(f"❌ 没成功：{text}")
        if not _yes("还是要加上吗", False):
            return None
    else:
        print(f"✅ {text}（{secs:.1f} 秒）")
        if kind in ("chat", "vision") and secs > 15:
            print("   有点慢：当主力的话回消息会比较慢，建议当备用或者只干后台的活。")
    roles, first = _pick_roles(model)
    taken = {m["name"] for m in store.overview(cfg, root)["models"]}
    name = _ask("给它起个名字（英文、数字、下划线）", store.unique_name(model, taken))
    name = store.add_model(cfg, url=url, key=key, model=model, roles=roles, first=first, name=name,
                           extra=extra, via_proxy=via_proxy, root=root)
    print(f"✅ 加好了：{name}（写在 {store.CUSTOM_FILE}，Key 在 .env）。重启程序后生效。")
    show(cfg_reload(), root)
    return name


def cfg_reload():
    from ailife.core.config import load_config
    return load_config()


def show(cfg, root: Path = ROOT):
    ov = store.overview(cfg, root)
    if not ov["models"]:
        print("还没有模型。")
        return
    print("\n现在的模型：")
    for m in ov["models"]:
        print(f"  {m['name']:<24}{m['model']}  {m['base_url']}")
    print("每种用途按顺序用：")
    for r, desc in ROLES.items():
        names = ov["effective"][r]
        mark = "" if ov["routes"].get(r) else ("（跟着上一级）" if names else "（没配，不做）")
        print(f"  {desc:<16}{' → '.join(names) if names else '—'}{mark}")


async def test_all(cfg):
    from ailife.llm.client import LLM, strip_think
    llm = LLM(cfg)
    print(f"{'模型':<24}{'用时':>7}  结果")
    for name, p in llm.profiles.items():
        t0 = time.time()
        try:
            r = await p.client.chat.completions.create(
                model=p.model, messages=[{"role": "user", "content": "用一句话、十个字以内跟我打个招呼。"}],
                max_tokens=200, **({"extra_body": p.extra_body} if p.extra_body else {}))
            print(f"{name:<24}{time.time() - t0:>6.1f}s  ✅ {strip_think(r.choices[0].message.content or '')[:40]}")
        except Exception as e:  # noqa: BLE001
            print(f"{name:<24}{time.time() - t0:>6.1f}s  ❌ {str(e)[:80]}")


async def bench():
    """每个模型测一次首字延迟和速度（流式）。"""
    import time
    from ailife.core.config import load_config
    cfg = load_config()
    from ailife.llm.client import LLM
    llm = LLM(cfg)
    msgs = [{"role": "system", "content": "你是一个说话自然的女生，用口语回复，两三句话。"},
            {"role": "user", "content": "今天好累啊，下班路上还下雨了。"}]
    print(f"{'接口':<14}{'首字延迟':>8}{'总用时':>8}{'字/秒':>7}  回复")
    for name, p in llm.profiles.items():
        t0 = time.time()
        first = None
        text = ""
        try:
            resp = await p.client.chat.completions.create(
                messages=msgs, stream=True, **llm._kwargs(p, 0.8, 200))
            async for ch in resp:
                d = ch.choices[0].delta.content if ch.choices else None
                if d:
                    first = first or time.time()
                    text += d
            total = time.time() - t0
            from ailife.llm.client import strip_think
            text = strip_think(text).replace("\n", " ")
            speed = len(text) / max(0.01, total - (first - t0)) if first else 0
            print(f"{name:<14}{(first - t0) if first else 0:>7.2f}s{total:>7.2f}s{speed:>7.0f}  {text[:40]}")
        except Exception as e:  # noqa: BLE001
            print(f"{name:<14}  失败：{str(e)[:120]}")
    print("聊天（chat）建议把首字延迟最低、又稳定的放第一位。")


def remove_interactive(cfg, root: Path = ROOT) -> bool:
    mine = [m for m in store.overview(cfg, root)["models"] if m["editable"]]
    if not mine:
        print("还没有加过模型。")
        return False
    for i, m in enumerate(mine, 1):
        print(f"  {i}. {m['name']}  {m['model']}  {m['base_url']}")
    v = _ask("删哪个？输入编号（回车＝不删）")
    if not (v.isdigit() and 1 <= int(v) <= len(mine)):
        return False
    store.remove_model(mine[int(v) - 1]["name"], root)
    print(f"✅ 删掉了 {mine[int(v) - 1]['name']}（.env 里的 Key 没动）。重启程序后生效。")
    return True


def main(argv: list[str] | None = None) -> int:
    while True:
        cfg = cfg_reload()
        show(cfg)
        print("\n=== 大模型 ===")
        print("  1. 加一个模型（填地址和 Key → 测连通 → 选模型 → 试一下 → 选用途）")
        print("  2. 把所有模型都试一遍")
        print("  3. 删掉一个")
        print("  回车. 退出")
        v = _ask("选哪个")
        if v == "1":
            asyncio.run(add_interactive(cfg))
        elif v == "2":
            asyncio.run(test_all(cfg))
        elif v == "3":
            remove_interactive(cfg)
        else:
            return 0
