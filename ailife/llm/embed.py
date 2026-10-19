"""把文字变成向量（记忆检索用）：用路由里 embed 用途的模型；没配就只用关键词检索。失败后冷却 10 分钟。"""
from __future__ import annotations

import logging
import time

import numpy as np

log = logging.getLogger("ailife.retrieval")


class Embedder:
    def __init__(self, cfg):
        llm = cfg.get("llm") or {}
        names = list((llm.get("routes") or {}).get("embed") or [])
        prof = next((dict((llm.get("profiles") or {})[n]) for n in names if n in (llm.get("profiles") or {})), None)
        self.enabled = bool(prof) and bool(((cfg.get("memory") or {}).get("embed") or {}).get("enabled", True))
        self.model = prof.get("model") if prof else ""
        self.cool_until = 0.0
        self.client = None
        if self.enabled:
            import httpx
            from openai import AsyncOpenAI
            http = httpx.AsyncClient(proxy=cfg.get("proxy"), timeout=20) if prof.get("use_proxy") and cfg.get("proxy") \
                else None
            self.client = AsyncOpenAI(base_url=prof["base_url"], api_key=prof.get("api_key") or "none",
                                      http_client=http, timeout=20, max_retries=0)

    @property
    def available(self) -> bool:
        return self.enabled and time.time() >= self.cool_until

    async def embed(self, texts: list[str]) -> list[np.ndarray] | None:
        if not self.available or not texts:
            return None
        try:
            out: list[np.ndarray] = []
            for i in range(0, len(texts), 32):
                resp = await self.client.embeddings.create(model=self.model, input=texts[i:i + 32])
                for d in resp.data:
                    v = np.asarray(d.embedding, dtype=np.float32)
                    out.append(v / (np.linalg.norm(v) or 1.0))
            return out
        except Exception as e:  # noqa: BLE001
            self.cool_until = time.time() + 600
            log.warning("向量接口不可用，先只用关键词检索：%s", str(e)[:150])
            return None
