"""调用大模型（聊天、看图）：按用途找路由里的模型，前一个出错（限流、超时、5xx）自动换下一个并让它冷却一会儿。

    llm.profiles   你加的每个模型：base_url / api_key / model / extra_body / use_proxy / timeout
    llm.routes     每种用途按顺序用哪些（用途见 ailife/llm/roles.py）
程序不内置任何接口：一个都没加时 ready 为 False，调用会报“还没加大模型”。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import httpx
from openai import AsyncOpenAI

log = logging.getLogger("ailife.llm")


class NoModel(RuntimeError):
    """这个用途一个模型都没配。"""


class Profile:
    def __init__(self, name: str, c: dict, proxy: str | None):
        self.name = name
        api_key = c.get("api_key") or ""
        self.base_url = c["base_url"]
        self.api_key = api_key
        self.use_proxy = bool(c.get("use_proxy"))
        self.model = c["model"]
        self.extra_body = dict(c.get("extra_body") or {})
        self.temperature = c.get("temperature")
        self.max_tokens = c.get("max_tokens")
        # 每百万 token 的价格（命中缓存, 没命中, 输出），用来估算花费（小屋首页的每周体检用）；DeepSeek 官方接口有默认值
        self.price = list(c.get("price") or ([0.003, 0.15, 0.6] if "api.deepseek.com" in str(c["base_url"]).lower()
                                             else [0, 0, 0]))
        http_client = None
        if c.get("use_proxy") and proxy:
            http_client = httpx.AsyncClient(proxy=proxy, timeout=c.get("timeout", 90))
        self.client = AsyncOpenAI(base_url=c["base_url"], api_key=api_key or "none",
                                  http_client=http_client, timeout=c.get("timeout", 90), max_retries=0)
        self.cool_until = 0.0

    def available(self) -> bool:
        return time.time() >= self.cool_until

    def cool(self, err: Exception):
        msg = str(err)
        secs = 60.0
        if "429" in msg or "rate" in msg.lower() or "1302" in msg or "1305" in msg:
            secs = 30.0
        if "quota" in msg.lower() or "insufficient" in msg.lower() or "余额" in msg or "额度" in msg:
            secs = 3600.0
        self.cool_until = time.time() + secs
        log.warning("接口 %s 出错，冷却 %.0f 秒：%s", self.name, secs, msg[:200])


class LLM:
    def __init__(self, cfg):
        c = cfg.llm
        self.temperature = float(c.get("temperature", 0.9))
        self.max_tokens = int(c.get("max_tokens", 800))
        self.profiles: dict[str, Profile] = {}
        for n, p in (c.get("profiles") or {}).items():
            try:
                self.profiles[n] = Profile(n, dict(p), cfg.get("proxy"))
            except (KeyError, TypeError) as e:
                log.warning("模型 %s 的配置不完整（要有 base_url 和 model），先不用它：%s", n, e)
        self.routes: dict[str, list[str]] = {}
        for role, names in (c.get("routes") or {}).items():
            known = [n for n in (names or []) if n in self.profiles]
            for n in set(names or []) - set(known):
                log.warning("llm.routes.%s 里的 %s 没有对应的模型，跳过", role, n)
            self.routes[role] = known
        # 聊天优先：后台的活（整理记忆、日记、独白……）一次只跑一个，而且他在聊天时先让一让
        self._bg_sem = asyncio.Semaphore(1)
        self._fg_active = 0
        self._fg_last = 0.0
        self.yield_seconds = float(c.get("background_yield_seconds", 25))
        self.db = None                     # Brain 会把数据库挂上来，用来记每天的调用统计
        self.tz = cfg.get("tz")
        self._stats: dict = {}
        from .budget import Budget
        self.budget = Budget(cfg)                 # 每天的花费上限（llm.budget）

    FOREGROUND = ("chat", "vision")
    pronoun_note = ""                      # 角色 / 用户不是默认性别时，Brain 会设成一句代词说明

    def _with_note(self, messages: list[dict]) -> list[dict]:
        """把代词说明加在第一条文字消息前面（提示词里默认“她”=角色、“他”=你）。"""
        if not self.pronoun_note or not messages:
            return messages
        out = list(messages)
        for i, m in enumerate(out):
            if isinstance(m.get("content"), str) and m.get("role") in ("system", "user"):
                if self.pronoun_note not in m["content"]:
                    out[i] = {**m, "content": self.pronoun_note + "\n" + m["content"]}
                break
        return out

    # ---------------- 用量统计 ----------------
    def _today(self) -> str:
        from datetime import datetime
        return datetime.now(self.tz).strftime("%Y-%m-%d")          # 按配置时区（手机的 proot 里系统时间常是 UTC）

    def _record(self, role: str, profile: str | None, ok: bool, limited: bool = False, ms: float = 0.0,
                fallback: bool = False, usage=None, price=None):
        day = self._today()
        st = self._stats.get(day)
        if st is None:
            st = (self.db.get(f"llm_stats_{day}") if self.db is not None else None) or {}
            self._stats = {day: st}
        st["calls"] = st.get("calls", 0) + 1
        st["ok"] = st.get("ok", 0) + (1 if ok else 0)
        st["limited"] = st.get("limited", 0) + (1 if limited else 0)
        st["fallback"] = st.get("fallback", 0) + (1 if fallback else 0)
        kind = "fg" if role in self.FOREGROUND else "bg"
        st[kind] = st.get(kind, 0) + 1
        if usage is not None:                                    # 缓存命中、输出 token、估算花费（美元）
            hit = int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
            miss = int(getattr(usage, "prompt_cache_miss_tokens", 0) or 0)
            if not hit and not miss:
                miss = int(getattr(usage, "prompt_tokens", 0) or 0)
            out = int(getattr(usage, "completion_tokens", 0) or 0)
            st["tok_hit"] = st.get("tok_hit", 0) + hit
            st["tok_miss"] = st.get("tok_miss", 0) + miss
            st["tok_out"] = st.get("tok_out", 0) + out
            if price:
                peak = peak_factor(self.tz)
                st["usd"] = round(st.get("usd", 0.0) + peak * (hit * price[0] + miss * price[1] + out * price[2]) / 1e6, 6)
        if profile:
            pp = st.setdefault("profiles", {}).setdefault(profile, {"ok": 0, "fail": 0, "ms": 0.0})
            pp["ok" if ok else "fail"] += 1
            if ok:
                pp["ms"] += ms
        if self.db is not None:
            try:
                self.db.set(f"llm_stats_{day}", st)
            except Exception:  # noqa: BLE001
                pass

    def add_cost(self, usd: float, what: str = "image"):
        """不是聊天的花费（画图按张算）也记进今天的账。"""
        day = self._today()
        st = self._stats.get(day)
        if st is None:
            st = (self.db.get(f"llm_stats_{day}") if self.db is not None else None) or {}
            self._stats = {day: st}
        st["usd"] = round(st.get("usd", 0.0) + float(usd), 6)
        st[what] = st.get(what, 0) + 1
        if self.db is not None:
            try:
                self.db.set(f"llm_stats_{day}", st)
            except Exception:  # noqa: BLE001
                pass

    def stats(self, day: str | None = None) -> dict:
        day = day or self._today()
        if day in self._stats:
            return self._stats[day]
        return (self.db.get(f"llm_stats_{day}") if self.db is not None else None) or {}

    async def _wait_turn(self, role: str):
        """后台调用：等前台（聊天）空闲一会儿再开始，最多等 2 分钟。"""
        t_end = time.time() + 120
        while time.time() < t_end and (self._fg_active or time.time() - self._fg_last < self.yield_seconds):
            await asyncio.sleep(2)

    @property
    def ready(self) -> bool:
        """至少有一个聊天模型。"""
        return bool(self.routes.get("chat"))

    @property
    def vision(self) -> bool:
        return bool(self.routes.get("vision"))

    def profiles_for(self, role: str) -> list[Profile]:
        """这个用途按顺序可用的模型（考虑兜底规则；都在冷却就按原顺序硬试）。"""
        from ailife.llm.roles import chain
        ps = [self.profiles[n] for n in chain(self.routes, role)]
        return [p for p in ps if p.available()] or ps

    def _chain(self, role: str) -> list[Profile]:
        chain = self.profiles_for(role)
        if not chain:
            raise NoModel(f"还没加{'聊天' if role in ('chat', 'helper', 'memory', 'night') else role}模型："
                          "打开小屋“设置 → 大模型”，或者运行 python run.py llm")
        return chain

    def _kwargs(self, p: Profile, temperature, max_tokens):
        kw = dict(model=p.model,
                  temperature=p.temperature if p.temperature is not None else temperature,
                  max_tokens=p.max_tokens or max_tokens)
        if p.extra_body:
            kw["extra_body"] = p.extra_body
        return kw

    async def chat(self, messages: list[dict], helper: bool = False, role: str | None = None,
                   temperature: float | None = None, max_tokens: int | None = None, background: bool = False) -> str:
        role = role or ("helper" if helper else "chat")
        self.budget.check(float(self.stats().get("usd", 0) or 0), self._today(), role in self.FOREGROUND and not background)
        if role in self.FOREGROUND and not background:
            self._fg_active += 1
            try:
                return await self._chat(messages, role, temperature, max_tokens)
            finally:
                self._fg_active -= 1
                self._fg_last = time.time()
        async with self._bg_sem:
            await self._wait_turn(role)
            return await self._chat(messages, role, temperature, max_tokens)

    async def _chat(self, messages: list[dict], role: str, temperature: float | None,
                    max_tokens: int | None) -> str:
        temperature = self.temperature if temperature is None else temperature
        max_tokens = max_tokens or self.max_tokens
        messages = self._with_note(messages)
        last: Exception | None = None
        # 后台的活（整理记忆、日记）不赶时间：首选模型限流时等几秒再试一次，尽量用好模型
        patient = role in ("memory", "night")
        for p in self._chain(role):
            for attempt in range(2 if patient else 1):
                try:
                    t0 = time.time()
                    resp = await p.client.chat.completions.create(
                        messages=messages, **self._kwargs(p, temperature, max_tokens))
                    text = strip_think(resp.choices[0].message.content or "")
                    if not text:
                        raise RuntimeError("返回了空内容")
                    log.debug("%s 用时 %.1fs", p.name, time.time() - t0)
                    chain0 = self.routes.get(role) or []
                    fb = bool(chain0 and p.name != chain0[0])
                    if fb:
                        log.info("这次由备用接口 %s 回答（%s）", p.name, role)
                    self._record(role, p.name, True, ms=(time.time() - t0) * 1000, fallback=fb,
                                 usage=getattr(resp, "usage", None), price=p.price)
                    return text
                except Exception as e:  # noqa: BLE001
                    last = e
                    busy = any(x in str(e) for x in ("429", "1305", "1302", "rate"))
                    self._record(role, p.name, False, limited=busy)
                    if patient and attempt == 0 and busy:
                        await asyncio.sleep(6)
                        continue
                    p.cool(e)
                    break
        raise RuntimeError(f"所有 {role} 接口都失败了：{last}")

    async def json(self, messages: list[dict], default=None, role: str = "helper"):
        """让模型输出 JSON，并尽量容错地解析。"""
        try:
            text = await self.chat(messages, role=role, temperature=0.3, max_tokens=1800)
        except Exception as e:  # noqa: BLE001
            log.warning("json 调用失败: %s", e)
            return default
        return parse_json(text, default)

    async def label_image(self, image_b64: str, prompt: str, mime: str = "image/png") -> str:
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
        ]}]
        return await self.chat(msgs, role="vision", temperature=0.2, max_tokens=60, background=True)  # 给贴纸打标签：后台的活

    async def describe_image(self, image_b64: str, hint: str = "") -> str:
        """用看图模型把图片变成文字描述，再交给聊天模型。"""
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "用中文简洁描述这张图片的内容（人物、场景、文字、氛围），80 字以内。"
                                     + (f"发图的人附言：{hint}" if hint else "")},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ]}]
        return await self.chat(msgs, role="vision", temperature=0.3, max_tokens=300)


def peak_factor(tz=None) -> float:
    """DeepSeek 高峰时段（北京时间工作日 09:00-12:00、14:00-18:00）价格翻倍；周末全天闲时（法定节假日这里不算）。"""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    if now.weekday() >= 5:
        return 1.0
    h = now.hour
    return 2.0 if (9 <= h < 12 or 14 <= h < 18) else 1.0


def strip_think(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    if "</think>" in text:                  # 有的接口只返回结尾标签
        text = text.split("</think>", 1)[1]
    return text.strip()


def parse_json(text: str, default=None):
    text = strip_think(text)
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if m:
        text = m.group(1)
    pairs = [("[", "]"), ("{", "}")]
    pairs.sort(key=lambda p: (text.find(p[0]) if text.find(p[0]) != -1 else 10**9))
    for opener, closer in pairs:
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    return default
