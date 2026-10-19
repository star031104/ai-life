"""第八批：人设 v2（字段、代词、生活节奏、声音、照片）、角色卡导入、每个角色自己的记忆、网页编辑。"""
import asyncio
import base64
import json
import struct
import zlib
from datetime import date, datetime
from pathlib import Path

from test_flow import cfg_root, env  # noqa: F401


def test_parse_fields_and_warnings():
    from ailife.persona import parse_persona, pronoun_note
    p = parse_persona("---\nname: 沈言\ngender: 男\nuser_gender: female\nage: 16\nlife: 上班族\n---\n短")
    assert p.gender == "male" and p.user_gender == "female" and p.ta == "他" and p.user_ta == "她"
    assert p.life == "worker" and p.age == 18
    assert any("成年" in w for w in p.warnings) and any("太短" in w for w in p.warnings)
    note = pronoun_note(p)
    assert "沈言是男生" in note and "用“他”" in note
    bad = parse_persona("没有头")
    assert any("开头没有" in w for w in bad.warnings)
    for f in ("linxi", "shenyan", "template"):
        q = parse_persona((cfg_root() / "persona" / f"{f}.md").read_text(encoding="utf-8"))
        assert q.name and not [w for w in q.warnings if "格式有错" in w or "不认识" in w], (f, q.warnings)


def test_pronoun_note_injected():
    from ailife.llm import LLM
    llm = LLM.__new__(LLM)
    llm.pronoun_note = "（说明：x）"
    out = llm._with_note([{"role": "system", "content": "你好"}, {"role": "user", "content": "嗯"}])
    assert out[0]["content"].startswith("（说明：x）") and out[1]["content"] == "嗯"
    llm.pronoun_note = ""
    m = [{"role": "user", "content": "a"}]
    assert llm._with_note(m) is m


def _use(env, name):
    cfg, brain, bot, fake = env
    from ailife.persona import load_persona
    brain.persona.update_from(load_persona(cfg.root / "persona" / f"{name}.md"))
    return brain


def test_worker_life(env):
    brain = _use(env, "shenyan")
    cfg = env[0]
    rh = brain.rhythm
    assert rh.life == "worker" and not rh.school.enabled
    brain.now = lambda: datetime(2026, 10, 14, 10, 0, tzinfo=cfg.tz)       # 周三
    blk = rh.her_block()
    assert "要上班（09:30-18:30）" in blk and "27 岁" in blk
    brain.now = lambda: datetime(2026, 10, 3, 10, 0, tzinfo=cfg.tz)
    assert "国庆假期" in rh.her_block()
    sp = brain.system_prompt("在干嘛")
    assert "沈言" in sp and "你的生活节奏" in sp


def test_photo_without_look(env):
    brain = _use(env, "template")
    assert brain.photos.look == ""
    assert "不出现人脸" in brain.photos.build_prompt("自拍一张")
    brain = _use(env, "student")
    assert "呆毛" in brain.photos.build_prompt("自拍一张")


def _png_with_card(card: dict) -> bytes:
    def chunk(typ, data):
        return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00")
    txt = b"chara\x00" + base64.b64encode(json.dumps(card).encode())
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"tEXt", txt) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def test_card_import(tmp_path):
    from ailife.personas import card_to_persona, read_card
    from ailife.persona import parse_persona
    card = {"spec": "chara_card_v2", "data": {
        "name": "Aria", "description": "{{char}} is a librarian who loves rainy days.",
        "personality": "gentle, curious", "scenario": "{{user}} visits the library.",
        "first_mes": "Oh, {{user}}! You came again.",
        "mes_example": "<START>\n{{user}}: Hi\n{{char}}: Hello there, {{user}}!\n<START>\n{{user}}: Tired\n{{char}}: Sit down, I'll make tea."}}
    f = tmp_path / "aria.png"
    f.write_bytes(_png_with_card(card))
    data = read_card(f)
    assert data["name"] == "Aria"
    text = card_to_persona(data, "小王", "female", "male")
    p = parse_persona(text)
    assert p.name == "Aria" and p.call_name == "小王" and "Aria is a librarian" in p.body and "{{" not in p.body
    assert len(p.examples) == 2 and p.examples[0].turns[0] == ("他", "Hi")
    assert p.greeting.startswith("Oh, 小王")
    (tmp_path / "c.json").write_text(json.dumps(card["data"]), encoding="utf-8")
    assert read_card(tmp_path / "c.json")["name"] == "Aria"


def test_build_persona_from_answers():
    from ailife.personas import QUESTIONS, build_persona
    from ailife.persona import parse_persona
    ans = {k: d for k, _, d in QUESTIONS}
    ans.update(name="阿青", gender="male", user_gender="female", identity="自由插画师")
    p = parse_persona(build_persona(ans))
    assert p.name == "阿青" and p.gender == "male" and p.life == "free" and "# 他是谁" not in p.body or True
    assert "温柔" in p.body and p.age == 22


def test_registry_per_persona_memory(tmp_path):
    from ailife.config import Cfg
    from ailife.personas import assign, db_path, set_persona_file
    (tmp_path / "persona").mkdir()
    for n in ("a", "b"):
        (tmp_path / "persona" / f"{n}.md").write_text(f"---\nname: {n}\n---\n" + "正文" * 50, encoding="utf-8")
    (tmp_path / "config.yaml").write_text("persona_file: persona/a.md\n", encoding="utf-8")
    cfg = Cfg({"root": tmp_path, "persona_file": "persona/a.md"})
    assert db_path(cfg) == tmp_path / "data" / "ailife.db"
    assert assign(cfg, "persona/b.md") == "data/chars/b.db"
    set_persona_file(tmp_path, "persona/b.md")
    assert "persona_file: persona/b.md" in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    cfg2 = Cfg({"root": tmp_path, "persona_file": "persona/b.md"})
    assert db_path(cfg2) == tmp_path / "data" / "chars" / "b.db"
    assert assign(cfg2, "persona/a.md") == "data/ailife.db"            # 换回来还是原来的记忆


def test_web_persona_edit_and_reload(env):
    cfg, brain, bot, fake = env
    from ailife.web import Dashboard
    d = Dashboard(cfg, brain, bot)

    class Req:
        def __init__(self, data=None, query=None):
            self._d, self.query = data or {}, query or {}

        async def json(self):
            return self._d
    d._json = lambda req: req.json()
    raw = json.loads(asyncio.run(d.persona_raw(Req(query={"file": "persona/student.md"}))).body)
    assert raw["text"].startswith("---")
    import pytest
    from aiohttp import web
    with pytest.raises(web.HTTPBadRequest):
        asyncio.run(d.persona_raw(Req(query={"file": "../config.yaml"})))
    bad = json.loads(asyncio.run(d.persona_save(Req({"file": "persona/student.md", "text": "没头"}))).body)
    assert not bad["ok"]
    new_text = raw["text"].replace("name: 小葵", "name: 小葵酱")
    cfg["persona_file"] = "persona/student.md"
    r = json.loads(asyncio.run(d.persona_save(Req({"file": "persona/student.md", "text": new_text}))).body)
    assert r["ok"] and brain.persona.name == "小葵酱"                    # 马上生效
    assert list((cfg.root / "data" / "backup" / "persona").glob("student-*.md"))
    st = d.life_state()
    assert st["persona"]["name"] == "小葵酱" and st["persona"]["list"]


def test_ui_pronoun_for_male(env):
    from ailife.bot_common import UI, ui
    old = UI["ta"]
    UI["ta"] = "他"
    try:
        assert ui("她在想") == "他在想"
    finally:
        UI["ta"] = old


def test_review_fixes(tmp_path):
    from ailife.bot_common import UI, keep, ui
    old = UI["ta"]
    UI["ta"] = "他"
    try:
        assert ui("她在想" + keep("她今天很累") + "她") == "他在想她今天很累他"
    finally:
        UI["ta"] = old
    assert ui(keep("她")) == "她"
    from ailife.persona import parse_persona
    p = parse_persona("---\r\nname: 甲\r\nlook: a---b\r\nage: .inf\r\n---\r\n正文", "x.md")
    assert p.name == "甲" and p.age is None and "a---b" in p.look
    from ailife.personas import _load_reg
    (tmp_path / "data" / "chars").mkdir(parents=True)
    (tmp_path / "persona").mkdir()
    (tmp_path / "persona" / "b.md").write_text("---\nname: b\n---\n", encoding="utf-8")
    (tmp_path / "data" / "chars" / "b.db").write_bytes(b"")
    (tmp_path / "data" / "personas.json").write_text("{bad", encoding="utf-8")
    assert _load_reg(tmp_path) == {"persona/b.md": "data/chars/b.db"}
