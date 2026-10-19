"""离线自检：python run.py selftest（或双击 selftest.bat）

不联网、不连 Telegram、不碰你的数据：在一个临时文件夹里用“假的大模型”把核心流程跑一遍，
再检查你这台电脑上的配置、依赖、数据库能不能被新版本正常打开（用数据库的一份副本）。
每次更新完代码跑一下，全是 ✅ 就可以放心重启。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import tempfile
import traceback
from pathlib import Path


class StubLLM:
    """按提示词内容给固定答案的假大模型。"""
    vision = False

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, helper=False, role=None, **kw):
        self.calls += 1
        last = messages[-1]["content"] if isinstance(messages[-1]["content"], str) else ""
        if "固定会出现的 5~7 个人" in last:
            return json.dumps([{"name": "小林", "role": "室友", "desc": "爱熬夜"}], ensure_ascii=False)
        if "整理记忆" in last:
            return json.dumps({"facts": [{"about": "他", "content": "他养了一只猫叫团子", "importance": 4}],
                               "routine": [{"when": "周三 19:00-21:00", "what": "上课"}]}, ensure_ascii=False)
        if "主动给他发消息" in last:
            return "在干嘛呀"
        return "收到啦|||明早八点叫你[提醒:08:00|叫他起床][愿望:一起去看海]"

    async def json(self, messages, default=None, role="helper"):
        from .llm import parse_json
        return parse_json(await self.chat(messages, role=role), default)

    def stats(self, day=None):
        return {}


def run(cfg, log=print) -> bool:
    return asyncio.run(_run(cfg, log))


async def _run(cfg, log) -> bool:
    from . import BATCH, __version__
    results: list[tuple[bool, str, str]] = []

    def check(name):
        def deco(fn):
            async def wrapped():
                try:
                    r = fn()
                    if asyncio.iscoroutine(r):
                        r = await r
                    results.append((True, name, r or ""))
                except Exception as e:  # noqa: BLE001
                    tb = traceback.format_exc(limit=2).strip().splitlines()[-1]
                    results.append((False, name, f"{e}（{tb[:120]}）"))
            return wrapped
        return deco

    log(f"ai-life {__version__}（{BATCH}）离线自检\n")
    tmp = Path(tempfile.mkdtemp(prefix="ailife-selftest-"))
    state: dict = {}

    @check("依赖")
    def deps():
        from .deps import missing
        m = missing(cfg)
        req = [p for p, _, r in m if r]
        if req:
            raise RuntimeError("缺少必需组件：" + "、".join(req))
        return ("可选组件缺：" + "、".join(p for p, _, _ in m)) if m else "齐全"

    @check("配置文件")
    def config():
        if not cfg.get("has_user_config"):
            raise RuntimeError("没有 config.yaml")
        tok = str(cfg.telegram.get("bot_token") or "")
        if ":" not in tok or tok.startswith("123456:"):
            raise RuntimeError("telegram.bot_token 没填（或 .env 里没有）")
        from .llm import LLM
        llm = LLM(cfg)                                  # 只检查配置能不能解析，不发请求
        keyless = [n for n, p in llm.profiles.items() if (p.client.api_key or "none") == "none"
                   and n in (llm.routes.get("chat") or [])]
        return f"聊天接口 {len(llm.routes['chat'])} 个" + (f"（没填 key：{'、'.join(keyless)}）" if keyless else "")

    @check("你的数据库（副本）")
    def real_db():
        from .personas import db_path
        src = db_path(cfg)
        if not src.exists():
            return "还没有数据库（第一次运行）"
        dst = tmp / "copy.db"
        a, b = sqlite3.connect(str(src)), sqlite3.connect(str(dst))
        try:
            a.backup(b)
        finally:
            b.close()
            a.close()
        from .db import DB
        db = DB(dst)                                    # 在副本上跑一遍升级
        n = db.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        m = len(db.all_memories())
        db.conn.close()
        return f"能正常升级打开：{n} 条聊天、{m} 条记忆"

    @check("人设文件")
    def persona():
        from .config import resolve
        from .persona import load_persona
        p = load_persona(resolve(cfg, cfg.persona_file))
        return f"{p.name}，对话示例 {len(p.examples)} 段"

    # ---------- 在临时文件夹里跑一遍核心流程 ----------
    @check("大脑：回复 + 标签")
    async def brain_flow():
        from .config import Cfg
        c = Cfg(dict(cfg))
        c["root"] = tmp
        c["memory"] = Cfg(dict(cfg.get("memory") or {}))
        c["memory"]["embed"] = Cfg({**dict((cfg.get("memory") or {}).get("embed") or {}), "enabled": False})
        from .config import resolve
        c["persona_file"] = str(resolve(cfg, cfg.persona_file))
        from .brain import Brain
        b = Brain(c)
        b.llm = StubLLM()
        b.memory.llm = b.llm
        b.weather.fetch = _none                         # 不联网
        state["brain"] = b
        b.db.add_message("user", "明天早上8点叫我起床，以后想和你一起去看海")
        p = await b.reply()
        texts = [x.text for x in p.bubbles if x.text]
        assert texts, "没生成任何话"
        assert b.db.pending_reminders(), "提醒没记上"
        assert b.db.wishes(), "愿望没记上"
        return f"回了 {len(texts)} 条，提醒和愿望都记上了"

    @check("记忆整理 + 作息学习")
    async def memory_flow():
        b = state["brain"]
        for i in range(3):
            b.db.add_message("user", f"随便聊聊 {i}")
        await b.memory.maybe_extract(force=True)
        assert any("团子" in m["content"] for m in b.db.all_memories()), "记忆没整理出来"
        assert b.rhythm.routines(), "作息没记上"
        return f"{len(b.db.all_memories())} 条记忆，作息 {len(b.rhythm.routines())} 条"

    @check("提示词各部分")
    def prompt():
        b = state["brain"]
        sp = b.system_prompt("今天好累")
        for must in ("你此刻的心情", "此刻", "回复格式", "你的生活节奏"):
            assert must in sp, f"提示词里少了“{must}”"
        return f"{len(sp)} 字"

    @check("主动消息 + 调度")
    async def proactive():
        b = state["brain"]
        from types import SimpleNamespace
        from .proactive import Scheduler
        sent = []

        async def deliver(p, **kw):
            sent.append(p)
            return True
        bot = SimpleNamespace(owner_id=1, _lock=asyncio.Lock(), deliver=deliver, pc=None,
                              spawn=lambda c: asyncio.ensure_future(c))
        s = Scheduler(b.cfg, b, bot)
        await s._send_proactive("你突然想到他了")
        assert sent, "主动消息没发出"
        return "能正常发出"

    @check("小屋网页数据")
    async def web():
        b = state["brain"]
        from .web import Dashboard
        d = Dashboard(b.cfg, b, None)
        r = await d.state(None)
        data = json.loads(r.body)
        for k in ("wishes", "report", "her_world", "habits"):
            assert k in data, f"少了 {k}"
        return "正常"

    @check("周报")
    def report():
        from .report import weekly_report
        text, cur, _ = weekly_report(state["brain"], save=False)
        return "正常"

    for fn in (deps, config, real_db, persona, brain_flow, memory_flow, prompt, proactive, web, report):
        await fn()
    try:
        b = state.get("brain")
        if b:
            b.db.conn.close()
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(tmp, ignore_errors=True)
    ok = all(r[0] for r in results)
    for good, name, msg in results:
        log(f"{'✅' if good else '❌'} {name}：{msg}")
    log("\n全部正常，可以放心重启。" if ok else "\n有问题：把上面 ❌ 的那几行发给帮你改代码的人。")
    return ok


async def _none(*a, **k):
    return None
