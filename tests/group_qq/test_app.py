"""配置向导写出来的 config、单实例锁、配置检查。"""
import yaml

from aigroup.app import check, single_instance
from aigroup.qq.wizard import build_config, pick_numbers


def test_build_config_roundtrip(tmp_path, monkeypatch):
    old = {"proxy": "", "keys": {"groq": "${GROQ_KEY}"}, "qq": {"group": {"interject_chance": 0.02}}}
    t = build_config(old, persona_file="persona/xiaoman.md", keys={"zhipu": "ZHIPU_KEY"}, port=3002, has_token=True,
                     owner=2233445566, groups=[111, 222], aliases=["满满"], owner_note="老朋友",
                     napcat_dir="C:/x/napcat", napcat_qq=3344556677)
    d = yaml.safe_load(t)
    assert d["keys"] == {"groq": "${GROQ_KEY}", "zhipu": "${ZHIPU_KEY}"}
    q = d["qq"]
    assert q["groups"] == [111, 222] and q["owner_id"] == 2233445566 and q["access_token"] == "${QQ_ACCESS_TOKEN}"
    assert q["group"] == {"interject_chance": 0.02, "owner_note": "老朋友"}          # 原来手动改的保留
    assert pick_numbers("1, 3，9 x 3", 4) == [0, 2]
    # 载入：和默认值合并、${} 从 .env 取
    import aigroup.core.config as C
    monkeypatch.setattr(C, "ROOT", C.ROOT)
    (tmp_path / "config.yaml").write_text(t, encoding="utf-8")


def test_check_and_lock(tmp_path):
    from aigroup.core.config import load_config
    c = load_config(tmp_path / "none.yaml")
    assert any("groups" in p for p in check(c))
    c["qq"]["groups"] = [1]
    c["llm"] = {**c["llm"], "profiles": {}, "routes": {"chat": []}}     # 本机可能有自己加的 config.llm.yaml
    assert [p for p in check(c)] and "llm.bat" in check(c)[0]          # 仓库里不预置任何接口：要自己加
    c["llm"]["profiles"] = {"m": {"base_url": "https://x.example/v1", "model": "m", "api_key": "k"}}
    c["llm"]["routes"]["chat"] = ["m"]
    assert not check(c)
    a = single_instance(tmp_path / "run.lock")
    assert a is not None and single_instance(tmp_path / "run.lock") is None
    a.close()


def test_settings_in_sync():
    """设置只有一个“真”默认值：config.example.yaml 里 qq.group 写的每一项都要在 chat/settings.py 的 DEFAULTS 里、值一样；
    小屋能调的 KNOBS 也都要有默认值。加设置时忘了同步，这个测试会提醒。"""
    import yaml

    from aigroup.chat.settings import KNOBS, defaults_for
    from aigroup.core.config import ROOT
    DEFAULTS = defaults_for("qq")
    ex = yaml.safe_load((ROOT / "config.qq.example.yaml").read_text(encoding="utf-8"))["qq"]["group"]
    assert not [k for k in ex if k not in DEFAULTS], "config.example.yaml 里有 DEFAULTS 没有的设置"
    assert not {k: (ex[k], DEFAULTS[k]) for k in ex if ex[k] != DEFAULTS[k]}, "两边默认值不一样"
    assert not [k for k in KNOBS if k not in DEFAULTS], "KNOBS 里有没有默认值的开关"
