"""读取配置：先读 config.example.yaml 作为默认值，再用 config.yaml 覆盖。

两个目录（12.0 起三个项目合成一个仓库）：
- CODE：程序所在的仓库目录（config.example.yaml、自带的人设模板、脚本都在这）
- ROOT：她自己的家（config.yaml、.env、data/、你自己写的人设）。用环境变量 AILIFE_HOME 指定
  （python run.py yuki 会设成 home/yuki）；没指定就是 CODE（老版本就是这样放的，照样能用）
"""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

CODE = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ["AILIFE_HOME"]).expanduser().resolve() if os.environ.get("AILIFE_HOME") else CODE


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
    with open(CODE / "config.example.yaml", encoding="utf-8") as f:
        base = yaml.safe_load(f) or {}
    # 想用别的配置文件：set AILIFE_CONFIG=xxx.yaml
    path = path or os.environ.get("AILIFE_CONFIG") or "config.yaml"
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    over = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            over = yaml.safe_load(f) or {}
    from ailife.core.secrets import read_env, substitute
    root = path.parent if path.exists() else ROOT
    from ailife.llm import store
    merged = _merge(base, over)
    if (ROOT / store.CUSTOM_FILE).exists():      # 你加的大模型（小屋 / python run.py llm 写的）
        merged = store.merge(merged, store.load(ROOT))
    cfg = _wrap(substitute(merged, read_env(root)))
    cfg["tz"] = ZoneInfo(cfg.timezone)
    cfg["root"] = ROOT
    cfg["code"] = CODE
    cfg["has_user_config"] = path.exists()
    pf = str(cfg.get("persona_file") or "").replace("\\", "/")
    while pf.startswith("./"):
        pf = pf[2:]
    if pf == "persona/persona.md" and not (ROOT / "persona" / "persona.md").exists() \
            and not (CODE / "persona" / "persona.md").exists() \
            and ((ROOT / "persona" / "linxi.md").exists() or (CODE / "persona" / "linxi.md").exists()):
        pf = "persona/linxi.md"                  # 旧版本的默认人设改名了
    if pf:
        cfg["persona_file"] = pf
    return cfg


def resolve(cfg: Cfg, p: str) -> Path:
    """相对路径先在她的家里找，没有再到程序目录里找（自带的人设模板在程序目录的 persona/ 里）。"""
    q = Path(p)
    if q.is_absolute():
        return q
    mine = Path(cfg.root) / q
    if mine.exists() or not cfg.get("code"):
        return mine
    shipped = Path(cfg.code) / q
    return shipped if shipped.exists() else mine
