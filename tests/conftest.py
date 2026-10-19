import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ailife.core.config import load_config  # noqa: E402
from fakes import FIXTURE, FakeLLM, FakeTGBot, cfg_root  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = load_config()
    cfg["root"] = tmp_path
    import shutil
    shutil.copytree(cfg_root() / "persona", tmp_path / "persona")
    shutil.copy(FIXTURE, tmp_path / "persona" / "student.md")
    cfg["persona_file"] = "persona/student.md"
    cfg["telegram"]["bot_token"] = "1:x"
    cfg["telegram"]["owner_id"] = 42
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
    from ailife.channels.telegram.bot import ChatBot
    bot = ChatBot(cfg, brain)
    fake = FakeTGBot()
    monkeypatch.setattr(type(bot.app), "bot", property(lambda self: fake))
    return cfg, brain, bot, fake
