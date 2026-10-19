"""QQ 一键配置：找 NapCat、读它的配置、改 config.yaml（保留别的内容）、整套流程。"""
import asyncio
import json
import shutil
import threading

import yaml

from test_qq import FakeNapCat, OWNER

ONEBOT = {"network": {"httpServers": [], "websocketServers": [
    {"enable": True, "name": "star", "host": "127.0.0.1", "port": 3001, "reportSelfMessage": False,
     "enableForcePush": True, "messagePostFormat": "array", "token": "OVz7D-.zC.7oe8T3", "debug": False,
     "heartInterval": 30000}]}}

USER_CFG = """# 我自己的配置
llm:
  temperature: 0.8   # 别动
telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN}"
  owner_id: 1234567890
qq:
  enabled: false
  groups: [123456789]
  aliases: ["小希"]

sense:
  pc:
    detail: content
"""


def _napcat(tmp_path, port=3001, fmt="array"):
    d = tmp_path / "NapCat.Shell.Windows.Node" / "napcat" / "config"
    d.mkdir(parents=True)
    ob = json.loads(json.dumps(ONEBOT))
    ob["network"]["websocketServers"][0].update(port=port, messagePostFormat=fmt)
    (d / "onebot11_2918273645.json").write_text(json.dumps(ob), encoding="utf-8")
    (d / "napcat.json").write_text("{}", encoding="utf-8")
    return d


def test_find_and_read(tmp_path):
    from ailife.qq_setup import config_dir_from, find_napcat_configs, read_onebot
    d = _napcat(tmp_path)
    assert find_napcat_configs([tmp_path]) == [d]
    assert config_dir_from(f'"{tmp_path / "NapCat.Shell.Windows.Node"}"') == d      # 拖进窗口时带引号
    ob = read_onebot(d)
    assert ob["uin"] == 2918273645 and ob["url"] == "ws://127.0.0.1:3001"
    assert ob["token"] == "OVz7D-.zC.7oe8T3" and ob["format"] == "array"


def test_set_qq_block_and_disable_tg():
    from ailife.qq_setup import disable_tg, set_qq_block
    q = {"enabled": True, "ws_url": "ws://127.0.0.1:3001", "access_token": "${QQ_ACCESS_TOKEN}", "owner_id": 2233445566,
         "groups": [123456789], "aliases": ["小希"]}
    out = set_qq_block(USER_CFG, q)
    data = yaml.safe_load(out)
    assert data["qq"] == q and data["sense"]["pc"]["detail"] == "content" and data["llm"]["temperature"] == 0.8
    assert "# 别动" in out and out.count("qq:") == 1
    # 没有 qq 一节：加到末尾
    out2 = set_qq_block("llm:\n  temperature: 0.8\n", q)
    assert yaml.safe_load(out2)["qq"]["owner_id"] == 2233445566
    off = disable_tg(out)
    assert yaml.safe_load(off)["telegram"]["bot_token"] == "" and "${TELEGRAM_BOT_TOKEN}" in off
    assert disable_tg(off) == off


def test_full_setup_flow(tmp_path, monkeypatch):
    import ailife.config as C
    import ailife.qq_setup as S
    root = tmp_path / "ai-life"
    root.mkdir()
    shutil.copy(C.ROOT / "config.example.yaml", root / "config.example.yaml")
    (root / "config.yaml").write_text(USER_CFG, encoding="utf-8")
    (root / ".env").write_text("TELEGRAM_BOT_TOKEN=123:abcdefgh\n", encoding="utf-8")
    monkeypatch.setattr(C, "ROOT", root)
    monkeypatch.setattr(S, "ROOT", root)
    monkeypatch.delenv("AILIFE_CONFIG", raising=False)

    nap = FakeNapCat()
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(nap.start())
        ready.set()
        loop.run_forever()
    threading.Thread(target=serve, daemon=True).start()
    assert ready.wait(5)
    nap.friends = [OWNER, 99999999]
    _napcat(tmp_path / "disk", port=nap.port)
    monkeypatch.setattr(S, "candidate_roots", lambda: [tmp_path / "disk"])
    answers = iter(["1", "n"])                      # 我是第 1 个好友；不要 Telegram 了
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    try:
        assert S.main([]) == 0
    finally:
        loop.call_soon_threadsafe(loop.stop)
    c = C.load_config()
    assert c.qq.enabled and c.qq.owner_id == OWNER and c.qq.access_token == "OVz7D-.zC.7oe8T3"
    assert c.qq.ws_url == f"ws://127.0.0.1:{nap.port}" and c.qq.groups == [123456789] and c.qq.aliases == ["小希"]
    assert c.telegram.bot_token == "" and c.sense.pc.detail == "content"
    text = (root / "config.yaml").read_text(encoding="utf-8")
    assert "OVz7D" not in text                       # token 只在 .env 里
    assert "QQ_ACCESS_TOKEN=OVz7D-.zC.7oe8T3" in (root / ".env").read_text(encoding="utf-8")
    assert list((root / "data" / "backup").glob("config.yaml.before-qq-*"))
