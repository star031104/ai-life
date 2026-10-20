import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ailife.core.config import load_config  # noqa: E402
from fakes import FIXTURE, FakeLLM, FakeQQ, cfg_root  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = load_config()
    cfg["root"] = tmp_path
    import shutil
    shutil.copytree(cfg_root() / "persona", tmp_path / "persona")
    shutil.copy(FIXTURE, tmp_path / "persona" / "student.md")
    cfg["persona_file"] = "persona/student.md"
    cfg["qq"] = {**dict(cfg.get("qq") or {}), "enabled": True, "owner_id": 42}
    cfg["style"]["typing_chars_per_sec"] = 1000
    cfg["proactive"]["morning"] = ""      # 早安晚安和真实时间有关，这里关掉免得测试不稳定
    cfg["proactive"]["night"] = ""
    cfg["proactive"]["quiet_hours"] = ""   # 免打扰时段和真实时间有关（半夜跑测试会失败）
    cfg["memory"]["embed"]["enabled"] = False        # 测试不连网
    cfg["style"]["typo_rate"] = 0
    cfg["sense"]["phone"]["enabled"] = False
    from ailife.mind.brain import Brain
    brain = Brain(cfg)
    brain.llm = FakeLLM()
    brain.memory.llm = brain.llm
    from ailife.chat.companion import Companion
    bot = Companion(cfg, brain)
    bot.qq = FakeQQ(bot)
    fake = bot.qq.out
    return cfg, brain, bot, fake
