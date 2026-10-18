"""读取配置：先读 config.example.yaml 作为默认值，再用 config.yaml 覆盖。"""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent


class Cfg(dict):
    """支持 cfg.llm.model 这种点号访问的字典。"""

    def __getattr__(self, item):
        try:
            v = self[item]
        except KeyError as e:
            raise AttributeError(item) from e
        return Cfg(v) if isinstance(v, dict) and not isinstance(v, Cfg) else v

    def __setattr__(self, key, value):
        self[key] = value


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _wrap(d):
    if isinstance(d, dict):
        return Cfg({k: _wrap(v) for k, v in d.items()})
    return d


def load_config(path: str | os.PathLike | None = None) -> Cfg:
    with open(ROOT / "config.example.yaml", encoding="utf-8") as f:
        base = yaml.safe_load(f) or {}
    path = Path(path) if path else ROOT / "config.yaml"
    over = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            over = yaml.safe_load(f) or {}
    from .secrets import read_env, substitute
    root = path.parent if path.exists() else ROOT
    cfg = _wrap(substitute(_merge(base, over), read_env(root)))
    cfg["tz"] = ZoneInfo(cfg.timezone)
    cfg["root"] = ROOT
    cfg["has_user_config"] = path.exists()
    pf = str(cfg.get("persona_file") or "").replace("\\", "/")
    while pf.startswith("./"):
        pf = pf[2:]
    if pf == "persona/persona.md" and not (ROOT / "persona" / "persona.md").exists() \
            and (ROOT / "persona" / "linxi.md").exists():
        pf = "persona/linxi.md"                  # 旧版本的默认人设改名了
    if pf:
        cfg["persona_file"] = pf
    return cfg


def resolve(cfg: Cfg, p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else cfg.root / q
