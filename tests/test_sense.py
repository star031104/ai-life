"""电脑感知：认程序 / 网站、今天的时长、找他聊的话题、暂停。"""
import asyncio
from datetime import datetime

from test_flow import env  # noqa: F401


def test_recognize_levels():
    from ailife.sense import recognize
    a = recognize(r"C:\Games\Genshin Impact\YuanShen.exe", "原神")
    assert (a.kind, a.app) == ("打游戏", "原神") and a.text() == "在玩《原神》"
    edge = "【某视频】好看的视频_哔哩哔哩_bilibili - 个人 - Microsoft​ Edge"
    assert recognize(r"C:\x\msedge.exe", edge).text() == "在B站看视频"                     # app：不带标题
    assert "好看的视频" in recognize(r"C:\x\msedge.exe", edge, "content").text()
    assert recognize(r"C:\x\Code.exe", "bot.py - ai-life - Visual Studio Code", "content").content == "项目 ai-life"
    assert recognize(r"C:\x\chrome.exe", "InPrivate - 秘密", "content").text() == "在上网"     # 无痕不说内容
    assert recognize(r"C:\x\WINWORD.EXE", "minecraft攻略.docx - Word").kind == "学习/办公"   # 标题里的字不算游戏
    assert recognize(r"C:\x\chrome.exe", "原神攻略 - 哔哩哔哩 - Google Chrome").kind == "看视频"
    s = recognize(r"D:\Steam\steamapps\common\Hades\Hades.exe", "Hades")
    assert (s.kind, s.app) == ("打游戏", "Hades")
    assert recognize(r"C:\x\chrome.exe", "我的网银 - Google Chrome", "content", ignore=("网银",)).text() == "在用电脑"


def test_pc_sense_day(env, monkeypatch):
    cfg, brain, bot, fake = env
    from ailife import sense
    from ailife.sense import PCSense
    cfg["sense"]["pc"]["detail"] = "content"
    pc = PCSense(cfg, brain.db)
    clock = [1_800_000_000.0]
    monkeypatch.setattr(sense.time, "time", lambda: clock[0])
    state = {"exe": r"C:\Games\Genshin Impact\YuanShen.exe", "title": "原神", "idle": 1.0}
    monkeypatch.setattr(PCSense, "_probe", staticmethod(lambda: (state["exe"], state["title"], state["idle"])))

    async def no_media():
        return None
    monkeypatch.setattr(PCSense, "_media", staticmethod(no_media))
    monkeypatch.setattr(pc, "_now", lambda: datetime(2026, 10, 18, 15, 0, tzinfo=cfg.tz))

    async def run_for(minutes):
        for _ in range(int(minutes * 2)):
            await pc.tick()
            clock[0] += 30

    async def main():
        await run_for(50)                                     # 玩了 50 分钟原神
        assert "在玩《原神》" in brain.db.get("sense_pc")["desc"]
        assert any("开始玩《原神》" in t["text"] for t in pc.topics)
        state.update(exe=r"C:\x\Code.exe", title="bot.py - ai-life - Visual Studio Code")
        await run_for(1)
        assert any("打完《原神》" in t["text"] for t in pc.topics)
        await run_for(124)                                    # 然后连续写了两个多小时代码
        assert any("连续用 VS Code 写代码" in t["text"] and t["w"] >= 4 for t in pc.topics)
        state.update(exe=r"C:\x\Telegram.exe", title="Telegram")
        await run_for(1)
        d = brain.db.get("sense_pc")["desc"]
        assert d.startswith("刚才在用 VS Code 写代码（项目 ai-life）") and "和你聊天" in d, d
        today = pc.today_summary()
        assert "VS Code写代码 2.1 小时" in today and "《原神》 50 分钟" in today, today
        assert "他今天在电脑上" in brain._now_block()
        # 暂停：不再更新
        pc.pause(1)
        before = brain.db.get(pc._day_key())
        await run_for(5)
        assert brain.db.get("sense_pc") is None and brain.db.get(pc._day_key()) == before
        pc.pause(None)
        assert not pc.paused()
    asyncio.run(main())


def test_late_night_topic(env, monkeypatch):
    cfg, brain, bot, fake = env
    from ailife.sense import Activity, PCSense
    pc = PCSense(cfg, brain.db)
    monkeypatch.setattr(pc, "_now", lambda: datetime(2026, 10, 18, 1, 30, tzinfo=cfg.tz))
    pc._on_going(Activity("打游戏", "原神"), 20, 3)
    pc._on_going(Activity("打游戏", "原神"), 21, 3)
    late = [t for t in pc.topics if "01:30" in t["text"]]
    assert len(late) == 1 and "还在电脑前玩《原神》" in late[0]["text"]


def test_pet_finds_model(env):
    cfg, brain, bot, fake = env
    from ailife.pet import PetAssets
    d = cfg.root / "data" / "pet" / "model" / "我的角色"
    d.mkdir(parents=True, exist_ok=True)
    (d / "char.model3.json").write_text("{}", encoding="utf-8")
    assert PetAssets(cfg).model == "我的角色/char.model3.json"
