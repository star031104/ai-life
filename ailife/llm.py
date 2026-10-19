"""大模型调用：多个免费接口按用途分组，按顺序自动兜底。

config.yaml 里：
    llm.profiles   各个接口（base_url / api_key / model / extra_body ...）
    llm.routes     每种用途按顺序用哪些接口：chat（聊天）、helper（记忆/日记）、vision（看图）
某个接口报错（限流、并发超限、超时、5xx），会自动换下一个，并让它“冷却”一会儿。
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


class Profile:
    def __init__(self, name: str, c: dict, proxy: str | None, keys: dict | None = None):
        self.name = name
        api_key = c.get("api_key") or (keys or {}).get(c.get("key") or "", "")
        self.model = c["model"]
        self.extra_body = dict(c.get("extra_body") or {})
        self.temperature = c.get("temperature")
        self.max_tokens = c.get("max_tokens")
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
        keys = dict(cfg.get("keys") or {})
        self.profiles = {n: Profile(n, dict(p), cfg.proxy, keys)
                         for n, p in (c.get("profiles") or {}).items()}
        self.routes: dict[str, list[str]] = {k: list(v or []) for k, v in (c.get("routes") or {}).items()}
        for role, names in self.routes.items():
            for n in names:
                if n not in self.profiles:
                    raise ValueError(f"llm.routes.{role} 里的 {n} 不在 llm.profiles 里")
        if not self.routes.get("chat"):
            raise ValueError("至少要在 llm.routes.chat 里配置一个接口")
        # 聊天优先：后台的活（整理记忆、日记、独白……）一次只跑一个，而且他在聊天时先让一让
        self._bg_sem = asyncio.Semaphore(1)
        self._fg_active = 0
        self._fg_last = 0.0
        self.yield_seconds = float(c.get("background_yield_seconds", 25))
        self.db = None                     # Brain 会把数据库挂上来，用来记每天的调用统计
        self.tz = cfg.get("tz")
        self._stats: dict = {}

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
                fallback: bool = False):
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
    def vision(self) -> bool:
        return bool(self.routes.get("vision"))

    def _chain(self, role: str) -> list[Profile]:
        # night（日记/日程/周信）、memory（整理记忆）没单独配置时，用 helper 的设置
        names = self.routes.get(role) or (self.routes.get("helper") if role in ("night", "memory") else None) \
            or self.routes["chat"]
        chain = [self.profiles[n] for n in names]
        ok = [p for p in chain if p.available()]
        return ok or chain           # 全在冷却就硬着头皮按顺序再试

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
                    self._record(role, p.name, True, ms=(time.time() - t0) * 1000, fallback=fb)
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
