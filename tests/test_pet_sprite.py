"""桌宠的像素宠物：认地址、只从 codex-pets.net 下载、整只写入、小屋里换样子。（不联网：假的下载）"""
import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from ailife import pet_sprite as ps
from test_flow import env  # noqa: F401  （复用完整流程的假环境）

WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 16


def fake_site(monkeypatch, sheet_url="https://codex-pets.net/assets/pets/v/1/claude-chan/spritesheet.webp",
              atlas="1536x2288", body=WEBP):
    calls = []

    async def _get(url, proxy, *, as_json, limit=ps.MAX_SHEET):
        calls.append(url)
        if "/api/pets/" in url:
            pid = url.rsplit("/", 1)[-1]
            if pid == "nope":
                raise ps.PetError("codex-pets.net 上没有这只宠物")
            return {"pet": {"id": pid, "displayName": "Claude小姐", "description": "原始角色形象作者：ZipZipPipe",
                            "ownerName": "zhoupengjie", "spritesheetUrl": sheet_url, "spriteVersionNumber": "2",
                            "kind": "person", "validationReport": {"atlasSize": atlas}}}
        if "/api/pets?" in url:
            return {"pets": [{"id": "claude-chan", "displayName": "Claude小姐", "ownerName": "zhoupengjie",
                              "likeCount": 4, "spriteVersionNumber": "2",
                              "posterUrl": "https://codex-pets.net/a/poster.webp"},
                             {"id": "bad id!", "displayName": "x"},
                             {"id": "evil", "posterUrl": "https://evil.com/x.png"}],
                    "page": 1, "totalPages": 1, "total": 3}
        return body
    monkeypatch.setattr(ps, "_get", _get)
    return calls


def test_parse_ref():
    assert ps.parse_ref("claude-chan") == "claude-chan"
    assert ps.parse_ref("https://codex-pets.net/#/pets/deepseek-chan?x=1") == "deepseek-chan"
    assert ps.parse_ref("codex-pets.net/api/pets/feibi") == "feibi"
    assert ps.parse_ref("https://codex-pets.net/assets/pets/v/179/atri/spritesheet.webp") == "atri"
    for bad in ("https://evil.com/#/pets/x", "https://codex-pets.net.evil.com/#/pets/x", "a b"):
        with pytest.raises(ps.PetError):
            ps.parse_ref(bad)


def test_download_and_local(tmp_path, monkeypatch):
    fake_site(monkeypatch)
    pet = asyncio.run(ps.download(tmp_path, "https://codex-pets.net/#/pets/claude-chan"))
    assert pet["id"] == "claude-chan" and pet["author"] == "zhoupengjie" and pet["version"] == 2
    d = ps.sprites_dir(tmp_path) / "claude-chan"
    assert (d / "spritesheet.webp").read_bytes() == WEBP
    assert json.loads((d / "pet.json").read_text(encoding="utf-8"))["source"].endswith("/#/pets/claude-chan")
    assert not list(ps.sprites_dir(tmp_path).glob(".*"))           # 没留下临时目录
    asyncio.run(ps.download(tmp_path, "claude-chan"))               # 再下一次：整只换掉
    assert [p["id"] for p in ps.local_pets(tmp_path)] == ["claude-chan"]
    assert ps.remove_local(tmp_path, "claude-chan") and not ps.local_pets(tmp_path)
    assert not ps.remove_local(tmp_path, "../x")


@pytest.mark.parametrize("kw,msg", [({"sheet_url": "https://evil.com/s.webp"}, "不在 codex-pets.net"),
                                    ({"atlas": "512x512"}, "图集"),
                                    ({"body": b"<html>"}, "不是 webp")])
def test_download_refuses(tmp_path, monkeypatch, kw, msg):
    fake_site(monkeypatch, **kw)
    with pytest.raises(ps.PetError, match=msg):
        asyncio.run(ps.download(tmp_path, "claude-chan"))
    assert not ps.local_pets(tmp_path)


def test_gallery_filters(monkeypatch):
    fake_site(monkeypatch)
    g = asyncio.run(ps.gallery("金发"))
    assert [x["id"] for x in g["items"]] == ["claude-chan", "evil"]
    assert g["items"][1]["poster"] == ""                             # 站外的图片不交给网页


def test_pet_switches_skin(env, monkeypatch):  # noqa: F811
    cfg, brain, bot, fake = env
    fake_site(monkeypatch)
    from ailife.pet import Pet
    pet = Pet(cfg, brain, bot)
    app = web.Application()
    pet.routes(app)

    async def go():
        async with TestClient(TestServer(app)) as c:
            conf = await (await c.get("/api/pet/config")).json()
            assert conf["kind"] == "live2d" and conf["skin"] == ""
            r = await (await c.post("/api/pet/sprite/use", json={"id": "https://codex-pets.net/#/pets/claude-chan"})).json()
            assert r["ok"] and r["current"] == "claude-chan"
            conf = await (await c.get("/api/pet/config")).json()
            assert conf["kind"] == "sprite" and conf["sheet"] == "/pet/sprite/claude-chan/spritesheet.webp"
            assert (await c.get(conf["sheet"])).status == 200
            assert (await c.get("/pet/sprite/nope/spritesheet.webp")).status == 404
            assert (await (await c.get("/api/pet/poll")).json())["skin"] == "claude-chan"
            s = await (await c.get("/api/pet/sprites")).json()
            assert s["current"] == "claude-chan" and s["local"][0]["name"] == "Claude小姐"
            g = await (await c.get("/api/pet/sprite/gallery?q=x")).json()
            assert g["ok"] and g["items"]
            r = await (await c.post("/api/pet/sprite/use", json={"id": "nope"})).json()
            assert not r["ok"] and brain.db.get("pet_sprite") == "claude-chan"
            await c.post("/api/pet/sprite/use", json={"id": ""})
            assert (await (await c.get("/api/pet/config")).json())["kind"] == "live2d"
    asyncio.run(go())


def test_configured_sprite_downloads_on_first_use(env, monkeypatch):  # noqa: F811
    cfg, brain, bot, fake = env
    calls = fake_site(monkeypatch)
    cfg["pet"] = {**dict(cfg.get("pet") or {}), "sprite": "claude-chan"}
    from ailife.pet import Pet
    pet = Pet(cfg, brain, bot)
    sp = asyncio.run(pet._sprite_config())
    assert sp["kind"] == "sprite" and any("/api/pets/claude-chan" in u for u in calls)
    n = len(calls)
    asyncio.run(pet._sprite_config())
    assert len(calls) == n                                          # 下过了就不再下


def test_prefs_and_pc_state(env):  # noqa: F811
    cfg, brain, bot, fake = env
    import time
    from ailife.pet import Pet, win_env
    pet = Pet(cfg, brain, bot)
    app = web.Application()
    pet.routes(app)

    async def go():
        async with TestClient(TestServer(app)) as c:
            assert (await (await c.get("/api/pet/prefs")).json())["alert"] == "bubble"
            r = await (await c.post("/api/pet/prefs", json={"alert": "dot", "stay": 0})).json()
            assert r["alert"] == "dot" and r["stay"] == 0
            r = await (await c.post("/api/pet/prefs", json={"alert": "shout", "stay": 9999})).json()
            assert r["alert"] == "dot" and r["stay"] == 0                    # 不认识的值不改
            brain.db.set("sense_pc", {"kind": "打游戏", "ts": time.time()})
            p = await (await c.get("/api/pet/poll")).json()
            assert p["prefs"]["alert"] == "dot" and p["pc"] == {"busy": True, "away": False}
            brain.db.set("sense_pc", {"kind": "离开电脑了", "ts": time.time() - 999})
            assert (await (await c.get("/api/pet/poll")).json())["pc"]["away"] is False   # 太久以前的不算
    asyncio.run(go())
    assert win_env() == (False, 0.0)                                         # 不是 Windows：什么都不说


def test_links_and_update_endpoints(env, monkeypatch):  # noqa: F811
    cfg, brain, bot, fake = env
    from ailife.web import Dashboard
    dash = Dashboard(cfg, brain, bot)

    async def go():
        async with TestClient(TestServer(dash.app())) as c:
            links = (await (await c.get("/api/links")).json())["links"]
            keys = {x["key"]: x["state"] for x in links}
            assert {"qq", "tg", "llm", "pet", "sense"} <= set(keys)
            assert set(keys.values()) <= {"green", "blue", "yellow", "gray"}
            brain.db.set("update_info", {"newer": True, "latest": "99.0", "current": "9.6"})
            links = (await (await c.get("/api/links")).json())["links"]
            assert any(x["key"] == "update" and x["state"] == "yellow" for x in links)
            u = await (await c.get("/api/update")).json()
            assert u["current"] and u["info"]["latest"] == "99.0"
            s = await (await c.get("/api/state")).json()
            assert "skin" in s
    asyncio.run(go())
