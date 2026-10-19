"""备份 / 换电脑：打包和导入。"""
import socket
import zipfile



def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_export_import_roundtrip(env, tmp_path):
    cfg, brain, bot, fake = env
    root = cfg.root
    (root / "run.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "config.yaml").write_text('keys:\n  zhipu: "abcdefgh12345678"\n', encoding="utf-8")
    (root / "data" / "photos").mkdir(parents=True, exist_ok=True)
    (root / "data" / "photos" / "1.jpg").write_bytes(b"jpg")
    (root / "data" / "asr").mkdir(parents=True, exist_ok=True)
    (root / "data" / "asr" / "big.onnx").write_bytes(b"x" * 1000)
    brain.db.add_memory("他养了一只橘猫叫团子", 4)
    cfg["web"]["port"] = free_port()
    from ailife.core import backup as move
    out = move.export(cfg, log=lambda *a: None)
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert "ai-life/data/ailife.db" in names and "ai-life/data/photos/1.jpg" in names
        assert "ai-life/.env" in names and "ai-life/run.py" in names
        assert not any("asr" in n for n in names)
        assert "abcdefgh" not in z.read("ai-life/config.yaml").decode()     # 密钥在 .env 里，不在设置里
        assert b"abcdefgh" in z.read("ai-life/.env")
    # 导入到另一台电脑
    other = tmp_path / "other"
    (other / "data").mkdir(parents=True)
    from ailife.core.config import Cfg
    cfg2 = Cfg(dict(cfg))
    cfg2["root"] = other
    move.import_(cfg2, str(out), log=lambda *a: None)
    from ailife.core.db import DB
    db2 = DB(other / "data" / "ailife.db")
    assert any("团子" in m["content"] for m in db2.all_memories())
    assert (other / "config.yaml").exists() and "abcdefgh" in (other / ".env").read_text(encoding="utf-8")


def test_house_local_only(env):
    import asyncio
    import httpx
    cfg, brain, bot, fake = env
    cfg["web"]["port"] = free_port()
    from ailife.web.server import Dashboard
    dash = Dashboard(cfg, brain, bot)

    async def run():
        await dash.start()
        base = f"http://127.0.0.1:{cfg['web']['port']}"
        async with httpx.AsyncClient() as c:
            assert (await c.get(base + "/api/state")).status_code == 200
            assert (await c.get(base + "/api/state", headers={"Host": "evil.example"})).status_code == 403
            assert (await c.get(base + "/api/state", headers={"Sec-Fetch-Site": "cross-site"})).status_code == 403
        await dash.stop()
    asyncio.run(run())
