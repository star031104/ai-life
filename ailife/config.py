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
    # 第二个角色（比如群聊角色）用自己的配置文件：start_group.bat 里 set AILIFE_CONFIG=config.group.yaml
    path = path or os.environ.get("AILIFE_CONFIG") or "config.yaml"
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    over = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            over = yaml.safe_load(f) or {}
    ext = str(over.get("extends") or "").strip()
    if ext:             # 群聊角色的配置：先继承你的 config.yaml（大模型 key、代理……），再用自己的覆盖
        ep = Path(ext) if Path(ext).is_absolute() else ROOT / ext
        if ep.exists() and ep.resolve() != path.resolve():
            with open(ep, encoding="utf-8") as f:
                base = _merge(base, yaml.safe_load(f) or {})
    from .secrets import read_env, substitute
    root = path.parent if path.exists() else ROOT
    cfg = _wrap(substitute(_merge(base, over), read_env(root)))
    cfg["tz"] = ZoneInfo(cfg.timezone)
    cfg["root"] = ROOT
    inst = str(cfg.get("instance_root") or "").strip()
    if inst:            # 这个角色的数据（数据库、日志、人设、照片……）都放在自己的文件夹里，和别的角色互不相干
        r = Path(inst)
        r = r if r.is_absolute() else ROOT / r
        (r / "data").mkdir(parents=True, exist_ok=True)
        cfg["root"] = r
    cfg["has_user_config"] = path.exists()
    pf = str(cfg.get("persona_file") or "").replace("\\", "/")
    while pf.startswith("./"):
        pf = pf[2:]
    if pf == "persona/persona.md" and not (ROOT / "persona" / "persona.md").exists() \
            and (ROOT / "persona" / "linxi.md").exists():
        pf = "persona/linxi.md"                  # 旧版本的默认人设改名了
    if pf:
        if inst and not (cfg.root / pf).exists() and (ROOT / pf).exists():
            pf = str(ROOT / pf)              # 角色文件夹里没有：用程序目录下 persona/ 里的那份
        cfg["persona_file"] = pf
    return cfg


def resolve(cfg: Cfg, p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else cfg.root / q
