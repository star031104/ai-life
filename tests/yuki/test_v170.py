"""17.0 小屋 B 版：tokens.css、立绘 / 头像路由（data/avatar/，不进 GitHub）、页面里的路径都是相对的。"""
import asyncio
import re
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer


def test_tokens_and_avatar(env):
    cfg, brain, bot, _ = env
    from ailife.web.server import Dashboard, avatar_file

    async def go():
        async with TestClient(TestServer(Dashboard(cfg, brain, bot).app())) as c:
            r = await c.get("/tokens.css")
            assert r.status == 200 and "--hero-bg" in await r.text()
            assert (await c.get("/avatar/portrait")).status == 404
            d = Path(cfg.root) / "data" / "avatar"
            d.mkdir(parents=True, exist_ok=True)
            (d / "youxi-portrait.webp").write_bytes(b"RIFF0000WEBP")
            (d / "youxi-avatar.webp").write_bytes(b"RIFF1111WEBP")
            assert (await (await c.get("/avatar/avatar")).read()).startswith(b"RIFF1111")
            assert (await (await c.get("/avatar/portrait")).read()).startswith(b"RIFF0000")
            assert (await c.get("/avatar/secret")).status == 404
            page = await (await c.get("/")).text()
            assert 'href="tokens.css"' in page and 'id="mBooks"' in page
    asyncio.run(go())
    assert avatar_file(Path("/nope"), "portrait") is None


def test_page_relative_paths():
    html = (Path(__file__).resolve().parents[2] / "ailife" / "web" / "page.html").read_text(encoding="utf-8")
    assert not re.search(r"""(src|href)=["']/(api|files)/""", html)
    assert not re.search(r"""fetch\(\s*["'`]/""", html)
    for need in ('id="life"', 'id="threads"', 'id="soon"', 'id="memRow"', 'data-go="notes"', 'id="meters"', 'id="kToday"'):
        assert need in html, need


def test_selfie_uses_avatar_refs(env):
    """17.0 有希画她自己：配了 routes.image_ref、data/avatar/ 里有图时，自拍带参考图；景物照照旧只用文字。"""
    cfg, brain, bot, _ = env
    from ailife.media.photo import PhotoMaker
    cfg["llm"] = {**dict(cfg.get("llm") or {}),
                  "profiles": {**dict((cfg.get("llm") or {}).get("profiles") or {}),
                               "img": {"base_url": "http://x/v1", "model": "Z-Image-Turbo"},
                               "edit": {"base_url": "http://x/v1", "model": "Qwen-Image-Edit-2509"}},
                  "routes": {**dict((cfg.get("llm") or {}).get("routes") or {}), "image": ["img"], "image_ref": ["edit"]}}
    pm = PhotoMaker(cfg, brain.db)
    assert pm.ref_gen.ready and not pm.can_draw_self              # 还没放图
    d = Path(cfg.root) / "data" / "avatar"
    d.mkdir(parents=True, exist_ok=True)
    (d / "youxi-avatar.webp").write_bytes(b"RIFF1111WEBP")
    (d / "youxi-portrait.webp").write_bytes(b"RIFF0000WEBP")
    assert pm.can_draw_self and pm.avatar_refs()[0].startswith(b"RIFF0000")   # 立绘排前面
    calls = []

    async def fake(self, prompt, size, refs=None):
        calls.append((self.chain[0]["name"], prompt, refs))
        return b"img"
    import ailife.llm.image as I
    orig = I.ImageGen.generate
    I.ImageGen.generate = fake
    try:
        assert asyncio.run(pm.make("我在窗边自拍")) == b"img"
        assert asyncio.run(pm.make("窗外的银杏")) == b"img"
    finally:
        I.ImageGen.generate = orig
    assert calls[0][0] == "edit" and calls[0][2] and "参考图" in calls[0][1]
    assert calls[1][0] == "img" and not calls[1][2]
