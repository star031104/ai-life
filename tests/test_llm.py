"""大模型：全部由用户自己加——地址规整、列模型、试一下、按用途放进路由、写 config.llm.yaml、和 config.yaml 合并、
没配时的表现、小屋接口。（本机假接口，不联网）"""
import asyncio
import json

import pytest
import yaml

from ailife.llm import store
from ailife.llm.probe import list_models, normalize_url, probe, url_candidates
from ailife.llm.roles import chain, default_roles, guess_kind


def test_no_builtin_models():
    from ailife.core.config import ROOT
    ex = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    assert ex["llm"]["profiles"] == {} and ex["llm"]["routes"] == {}
    assert "keys" not in ex
    text = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    import re
    assert not re.search(r"^\s*base_url:", text, re.M)               # 只有注释里的占位写法，没有真实地址


def test_pure_helpers():
    assert normalize_url("api.example.com/v1/") == "https://api.example.com/v1"
    assert normalize_url("127.0.0.1:11434/v1/chat/completions") == "http://127.0.0.1:11434/v1"
    assert url_candidates("https://api.x.com") == ["https://api.x.com", "https://api.x.com/v1"]
    assert url_candidates("https://api.x.com/api/paas/v4") == ["https://api.x.com/api/paas/v4"]
    assert store.unique_name("Qwen/Qwen3-8B", set()) == "qwen3_8b"
    assert store.unique_name("Qwen/Qwen3-8B", {"qwen3_8b"}) == "qwen3_8b_2"
    assert guess_kind("BAAI/bge-m3") == "embed" and guess_kind("cogview-4") == "image"
    assert guess_kind("glm-4.6v-flash") == "vision" and guess_kind("deepseek-chat") == "chat"
    assert default_roles("Qwen2.5-VL-7B") == ["chat", "vision"]
    routes = {"chat": ["a"], "helper": ["h"]}
    assert chain(routes, "memory") == ["h"] and chain(routes, "night") == ["h"]
    assert chain({"chat": ["a"]}, "memory") == ["a"] and chain({"chat": ["a"]}, "vision") == []
    merged = store.merge({"llm": {"profiles": {"a": {}}, "routes": {"chat": ["a"], "helper": ["a"]}}},
                         {"llm": {"profiles": {"x": {"model": "m"}}, "routes": {"chat": ["x", "a"]}}})
    assert set(merged["llm"]["profiles"]) == {"a", "x"} and merged["llm"]["routes"] == {"chat": ["x", "a"], "helper": ["a"]}


async def _fake_server():
    from aiohttp import web

    async def models(req):
        if req.headers.get("Authorization") != "Bearer sk-ok":
            return web.json_response({"error": "bad key"}, status=401)
        return web.json_response({"object": "list", "data": [{"id": "good-chat"}, {"id": "BAAI/bge-m3"}]})

    async def chat(req):
        d = await req.json()
        txt = "你好呀" if d.get("model") == "good-chat" else ""
        if d.get("enable_thinking") is False:
            txt = "关掉思考就好了"
        return web.json_response({"id": "1", "object": "chat.completion", "created": 0, "model": d["model"],
                                  "choices": [{"index": 0, "finish_reason": "stop",
                                               "message": {"role": "assistant", "content": txt}}]})

    async def emb(req):
        return web.json_response({"object": "list", "model": "x", "data": [
            {"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
            "usage": {"prompt_tokens": 1, "total_tokens": 1}})

    async def img(req):
        return web.json_response({"data": [{"b64_json": "aGVsbG8="}]})
    app = web.Application()
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", chat)
    app.router.add_post("/v1/embeddings", emb)
    app.router.add_post("/v1/images/generations", img)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]


def test_probe_against_fake_server():
    async def go():
        runner, port = await _fake_server()
        base = f"http://127.0.0.1:{port}"
        models, url, via, err = await list_models(base, "sk-ok")          # 没写 /v1：自动补上
        assert url == base + "/v1" and models == ["BAAI/bge-m3", "good-chat"] and not err
        models, url, via, err = await list_models(base + "/v1", "sk-bad")
        assert not models and "Key" in err
        ok, secs, text, extra = await probe(url, "sk-ok", "good-chat")
        assert ok and text == "你好呀" and extra is None
        ok, secs, text, extra = await probe(url, "sk-ok", "thinker")      # 回复为空：关掉思考再试
        assert ok and extra == {"enable_thinking": False}
        ok, secs, text, extra = await probe(url, "sk-ok", "BAAI/bge-m3", "embed")
        assert ok and "3 维" in text
        await runner.cleanup()
    asyncio.run(go())


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from ailife.core import config as C
    (tmp_path / "config.example.yaml").write_text((C.ROOT / "config.example.yaml").read_text(encoding="utf-8"),
                                                  encoding="utf-8")
    monkeypatch.setattr(C, "ROOT", tmp_path)
    (tmp_path / "config.yaml").write_text("persona_file: persona/linxi.md\n", encoding="utf-8")
    return C, tmp_path


def test_add_route_remove(isolated):
    C, root = isolated
    cfg = C.load_config(root / "config.yaml")
    from ailife.llm.client import LLM, NoModel
    llm = LLM(cfg)
    assert not llm.ready
    with pytest.raises(NoModel):
        asyncio.run(llm.chat([{"role": "user", "content": "hi"}]))
    a = store.add_model(cfg, url="https://api.example.com/v1", key="sk-secret", model="chat-a", roles=["chat"],
                        root=root)
    cfg = C.load_config(root / "config.yaml")
    b = store.add_model(cfg, url="https://api.example.com/v1", key="", model="chat-b", roles=["chat", "night"],
                        first=False, root=root)
    data = yaml.safe_load((root / "config.llm.yaml").read_text(encoding="utf-8"))
    assert data["llm"]["profiles"][a]["api_key"] == "${LLM_CHAT_A_API_KEY}" and "sk-secret" not in json.dumps(data)
    assert "sk-secret" in (root / ".env").read_text(encoding="utf-8")
    assert data["llm"]["routes"]["chat"] == [a, b] and data["llm"]["routes"]["night"] == [b]
    cfg2 = C.load_config(root / "config.yaml")                         # 重新读：合并进来、Key 从 .env 取
    assert cfg2.llm.profiles[a]["api_key"] == "sk-secret"
    llm = LLM(cfg2)
    assert llm.ready and [p.name for p in llm.profiles_for("memory")] == [a, b]   # memory 没配：跟着 chat
    assert [p.name for p in llm.profiles_for("night")] == [b]
    ov = store.overview(cfg2, root)
    assert {m["name"]: m["roles"] for m in ov["models"]} == {a: ["chat"], b: ["chat", "night"]}
    store.set_route("chat", [b, a], root)
    assert C.load_config(root / "config.yaml").llm.routes.chat == [b, a]
    with pytest.raises(ValueError):
        store.set_route("nope", [a], root)
    assert store.remove_model(a, root)
    cfg3 = C.load_config(root / "config.yaml")
    assert a not in cfg3.llm.profiles and cfg3.llm.routes.chat == [b]
    assert not store.remove_model("not-mine", root)


def test_unknown_route_names_are_skipped(isolated):
    C, root = isolated
    cfg = C.load_config(root / "config.yaml")
    cfg["llm"]["profiles"] = {"x": {"base_url": "http://127.0.0.1:1/v1", "model": "m"}, "broken": {"model": "m"}}
    cfg["llm"]["routes"] = {"chat": ["ghost", "x", "broken"]}
    from ailife.llm.client import LLM
    llm = LLM(cfg)
    assert llm.routes["chat"] == ["x"] and "broken" not in llm.profiles


def test_embed_and_image_use_routes(isolated):
    C, root = isolated

    async def go():
        runner, port = await _fake_server()
        cfg = C.load_config(root / "config.yaml")
        base = f"http://127.0.0.1:{port}/v1"
        cfg["llm"]["profiles"] = {"e": {"base_url": base, "model": "bge", "api_key": "sk-ok"},
                                  "i": {"base_url": base, "model": "img", "api_key": "sk-ok"}}
        from ailife.llm.embed import Embedder
        from ailife.llm.image import ImageGen
        assert not Embedder(cfg).enabled and not ImageGen(cfg).ready          # 没放进路由：不做
        cfg["llm"]["routes"] = {"embed": ["e"], "image": ["i"]}
        vecs = await Embedder(cfg).embed(["你好"])
        assert vecs and len(vecs[0]) == 3
        assert await ImageGen(cfg).generate("一只猫", "512x512") == b"hello"
        await runner.cleanup()
    asyncio.run(go())


def test_web_endpoints(env, monkeypatch, tmp_path):
    cfg, brain, bot, fake = env
    from types import SimpleNamespace
    from ailife.core import config as C
    monkeypatch.setattr(C, "ROOT", tmp_path)
    from ailife.web.server import Dashboard

    async def go():
        runner, port = await _fake_server()
        d = Dashboard(cfg, brain, bot)

        def req(body):
            async def j():
                return body
            return SimpleNamespace(json=j, headers={}, query={})
        d._json = lambda r: r.json()
        base = f"http://127.0.0.1:{port}/v1"
        r = json.loads((await d.llm_models(req({"url": base, "key": "sk-ok"}))).body)
        assert r["ok"] and {m["id"]: m["kind"] for m in r["models"]} == {"BAAI/bge-m3": "embed", "good-chat": "chat"}
        r = json.loads((await d.llm_test(req({"url": base, "key": "sk-ok", "model": "good-chat"}))).body)
        assert r["ok"] and r["text"] == "你好呀"
        r = json.loads((await d.llm_add(req({"url": base, "key": "sk-ok", "model": "good-chat",
                                              "roles": ["chat", "bogus"], "first": False}))).body)
        assert r["ok"]
        lst = json.loads((await d.llm_list(None)).body)
        m = next(x for x in lst["models"] if x["name"] == r["name"])
        assert m["editable"] and m["pending"] and m["roles"] == ["chat"] and "chat" in lst["roles"]
        assert json.loads((await d.llm_route(req({"role": "chat", "names": [r["name"]]}))).body)["ok"]
        assert not json.loads((await d.llm_route(req({"role": "x", "names": []}))).body)["ok"]
        assert json.loads((await d.llm_remove(req({"name": r["name"]}))).body)["ok"]
        await runner.cleanup()
    asyncio.run(go())
