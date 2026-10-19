"""你加的大模型存在哪：config.llm.yaml（程序写的，也可以手改），Key 放 .env。

程序里不内置任何接口地址：所有模型都是你自己加的（小屋“设置 → 大模型”、`python run.py llm`、配置向导）。
config.llm.yaml 在 config.yaml 之后读：llm.profiles 合并进去，llm.routes 里写了的用途整个替换。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ailife.core.config import ROOT
from ailife.llm.roles import ROLES

CUSTOM_FILE = "config.llm.yaml"


def load(root: Path = ROOT) -> dict:
    p = Path(root) / CUSTOM_FILE
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def save(data: dict, root: Path = ROOT):
    p = Path(root) / CUSTOM_FILE
    head = ("# 你加的大模型（程序写的，也可以手改）。Key 在 .env 里，这里只写 ${名字} 引用。\n"
            "# llm.profiles：每个模型的地址和名字；llm.routes：每种用途按顺序用哪些（前一个出错换下一个）。\n"
            "# 用途：" + "、".join(f"{k}（{v}）" for k, v in ROLES.items()) + "\n\n")
    tmp = p.with_suffix(".yaml.tmp")
    tmp.write_text(head + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=None),
                   encoding="utf-8")
    tmp.replace(p)


def merge(base: dict, custom: dict) -> dict:
    """config.yaml 读完后再叠加 config.llm.yaml。"""
    c = (custom or {}).get("llm") or {}
    if not c:
        return base
    llm = dict(base.get("llm") or {})
    llm["profiles"] = {**(llm.get("profiles") or {}), **(c.get("profiles") or {})}
    llm["routes"] = {**(llm.get("routes") or {}), **(c.get("routes") or {})}
    return {**base, "llm": llm}


def env_var(name: str) -> str:
    return "LLM_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper() + "_API_KEY"


def unique_name(model: str, taken: set[str]) -> str:
    """从模型名起一个名字（英文、数字、下划线），重名就加序号。"""
    base = re.sub(r"[^a-z0-9]+", "_", (model or "model").lower().split("/")[-1]).strip("_")[:32] or "model"
    name, i = base, 2
    while name in taken:
        name, i = f"{base}_{i}", i + 1
    return name


def _merged(cfg, custom: dict) -> dict:
    base = {"llm": {"profiles": dict((cfg.get("llm") or {}).get("profiles") or {}),
                    "routes": dict((cfg.get("llm") or {}).get("routes") or {})}}
    return merge(base, custom)["llm"]


def add_model(cfg, *, url: str, key: str, model: str, roles: list[str], first: bool = True, name: str = "",
              extra: dict | None = None, via_proxy: bool = False, root: Path = ROOT) -> str:
    """加一个模型，并按 roles 放进对应用途的路由（first＝排最前面当主力，否则排最后当备用）。返回名字。"""
    from ailife.core.secrets import write_env
    from ailife.llm.probe import normalize_url
    custom = load(root)
    c = custom.setdefault("llm", {})
    profiles = c.setdefault("profiles", {})
    merged = _merged(cfg, custom)
    taken = set(merged["profiles"])
    name = re.sub(r"[^A-Za-z0-9_]", "_", (name or "").strip()) or unique_name(model, taken)
    if name in taken and name not in profiles:
        name = unique_name(name, taken)
    prof: dict = {"base_url": normalize_url(url), "model": model}
    if key:
        var = env_var(name)
        write_env(root, {var: key})
        prof["api_key"] = f"${{{var}}}"
    else:
        prof["api_key"] = "none"
    if extra:
        prof["extra_body"] = extra
    if via_proxy:
        prof["use_proxy"] = True
    if re.search(r"127\.0\.0\.1|localhost", url):
        prof["timeout"] = 180                     # 本机模型第一次加载慢
    profiles[name] = prof
    routes = c.setdefault("routes", {})
    for role in roles:
        if role not in ROLES:
            continue
        cur = [n for n in (routes.get(role) or merged["routes"].get(role) or []) if n != name]
        routes[role] = [name] + cur if first else cur + [name]
    save(custom, root)
    return name


def set_route(role: str, names: list[str], root: Path = ROOT):
    """直接设定一个用途的顺序（小屋里拖动排序 / 勾掉）。空列表＝这个用途不单独配。"""
    if role not in ROLES:
        raise ValueError(f"没有这个用途：{role}")
    custom = load(root)
    routes = custom.setdefault("llm", {}).setdefault("routes", {})
    if names:
        routes[role] = list(dict.fromkeys(names))
    else:
        routes[role] = []
    save(custom, root)


def remove_model(name: str, root: Path = ROOT) -> bool:
    """删掉一个你加的模型（.env 里的 Key 不动），所有路由里也去掉它。"""
    custom = load(root)
    profiles = (custom.get("llm") or {}).get("profiles") or {}
    if name not in profiles:
        return False
    profiles.pop(name)
    routes = custom["llm"].get("routes") or {}
    for r in list(routes):
        routes[r] = [x for x in routes[r] if x != name]
    save(custom, root)
    return True


def overview(cfg, root: Path = ROOT) -> dict:
    """给小屋 / 命令行看的：每个模型的信息 + 它在哪些用途里 + 每个用途的顺序。"""
    from ailife.llm.roles import chain
    custom = load(root)
    merged = _merged(cfg, custom)
    mine = set(((custom.get("llm") or {}).get("profiles") or {}))
    routes = {r: list(merged["routes"].get(r) or []) for r in ROLES}
    models = []
    for n, p in merged["profiles"].items():
        models.append({"name": n, "model": p.get("model"), "base_url": p.get("base_url"), "editable": n in mine,
                       "roles": [r for r in ROLES if n in (routes.get(r) or [])]})
    return {"models": models, "routes": routes, "effective": {r: chain(routes, r) for r in ROLES},
            "roles": ROLES}
