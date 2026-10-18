"""依赖自检：启动时和 /diag 里告诉你缺了什么、缺了会少什么功能、怎么补。"""
from __future__ import annotations

import importlib.util
import shutil
import sys

from . import BATCH, __version__

# (模块名, pip 包名, 缺了会怎样, 只在哪个平台需要, 是否必需)
CHECKS = [
    ("telegram", "python-telegram-bot", "没法连 Telegram", None, True),
    ("openai", "openai", "没法调用大模型", None, True),
    ("aiohttp", "aiohttp", "小屋网页、桌宠用不了", None, True),
    ("yaml", "pyyaml", "读不了配置", None, True),
    ("numpy", "numpy", "记忆的向量检索用不了", None, True),
    ("sherpa_onnx", "sherpa-onnx", "本地语音合成 / 识别用不了（会改用在线）", None, False),
    ("lunar_python", "lunar_python", "农历节日、节气她不知道", None, False),
    ("winrt.windows.media.control", "winrt-Windows.Media.Control", "她不知道你电脑上在放什么歌", "win32", False),
    ("PySide6.QtWebEngineWidgets", "PySide6", "桌宠没法真透明（会有浅色方框、占任务栏；双击 pet_setup.bat 装上）", "win32", False),
]


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def missing(cfg=None) -> list[tuple[str, str, bool]]:
    """[(包名, 影响, 是否必需)]"""
    out = []
    for mod, pkg, why, plat, req in CHECKS:
        if plat and sys.platform != plat:
            continue
        if not _has(mod):
            out.append((pkg, why, req))
    if cfg is not None and (cfg.get("call") or {}).get("enabled") and not _has("telethon"):
        out.append(("telethon", "打电话用不了", False))
    if not shutil.which("ffmpeg"):
        out.append(("ffmpeg（不是 pip 包）", "语音条和打电话用不了", False))
    return out


def fix_hint() -> str:
    if sys.platform == "win32":
        return "双击 install.bat 重新装一次"
    return "pip install -r requirements.txt"


def version_line() -> str:
    return f"ai-life {__version__}（{BATCH}）"


def report(cfg=None) -> str:
    m = missing(cfg)
    if not m:
        return "依赖齐全 ✅"
    lines = [f"  {'❌' if req else '⚠️'} {pkg}：{why}" for pkg, why, req in m]
    return "缺少的组件：\n" + "\n".join(lines) + f"\n  → {fix_hint()}"
