"""记忆检索：关键词（BM25）+ 语义向量，两路结果用 RRF 融合。

- 关键词：中文按“单字 + 相邻两字”切词，英文/数字按整词，不需要装分词库。
- 向量：OpenAI 兼容的 /embeddings 接口（默认硅基流动 BAAI/bge-m3），算过的向量存进数据库，
  只有新记忆和你刚说的话需要现算。接口不可用时自动只用关键词，不影响聊天。
"""
from __future__ import annotations

import logging
import math
import re
import time
from collections import Counter

import numpy as np

log = logging.getLogger("ailife.retrieval")

_WORD = re.compile(r"[a-zA-Z0-9]+|[一-鿿㐀-䶿]")
_STOP = set("的了是我你他她在吗呢吧啊呀哦嗯和也就都还又很那这个有没不要会说去来")


def tokens(text: str) -> list[str]:
    parts = _WORD.findall((text or "").lower())
    out: list[str] = []
    prev_cn = None
    for p in parts:
        if len(p) == 1 and "㐀" <= p <= "鿿":
            if p not in _STOP:
                out.append(p)
            if prev_cn:
                out.append(prev_cn + p)
            prev_cn = p
        else:
            out.append(p)
            prev_cn = None
    return out


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.4, b: float = 0.75):
        self.toks = [tokens(d) for d in docs]
        self.k1, self.b = k1, b
        self.N = len(docs)
        self.avg = (sum(len(t) for t in self.toks) / self.N) if self.N else 0
        df: Counter = Counter()
        for t in self.toks:
            df.update(set(t))
        self.idf = {w: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for w, n in df.items()}
        self.tf = [Counter(t) for t in self.toks]

    def scores(self, query: str) -> list[float]:
        q = [w for w in set(tokens(query)) if w in self.idf]
        out = []
        for i, tf in enumerate(self.tf):
            L = len(self.toks[i]) or 1
            s = 0.0
            for w in q:
                f = tf.get(w, 0)
                if f:
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * L / (self.avg or 1)))
            out.append(s)
        return out


def rrf(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
    """多路排名融合：每一路里排第 r 名得 1/(k+r) 分。"""
    score: dict[int, float] = {}
    for ranking in rankings:
        for r, idx in enumerate(ranking):
            score[idx] = score.get(idx, 0.0) + 1.0 / (k + r + 1)
    return score


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


class Embedder:
    """把文字变成向量。失败后冷却 10 分钟，期间检索只用关键词。"""

    def __init__(self, cfg):
        c = dict((cfg.get("memory") or {}).get("embed") or {})
        self.enabled = bool(c.get("enabled", True))
        self.model = c.get("model", "BAAI/bge-m3")
        base = c.get("base_url", "https://api.siliconflow.cn/v1")
        key = c.get("api_key") or (cfg.get("keys") or {}).get(c.get("key") or "siliconflow", "")
        self.enabled = self.enabled and bool(key)
        self.cool_until = 0.0
        self.client = None
        if self.enabled:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(base_url=base, api_key=key, timeout=20, max_retries=0)

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


def hybrid_rank(query: str, docs: list[str], doc_vecs: list[np.ndarray | None] | None = None,
                qvec: np.ndarray | None = None, top: int = 20) -> list[tuple[int, float]]:
    """返回 [(文档下标, 融合分)]，分数越高越相关。只返回至少一路命中的。"""
    if not docs:
        return []
    rankings = []
    bm = BM25(docs).scores(query)
    kw = [i for i in sorted(range(len(docs)), key=lambda i: -bm[i]) if bm[i] > 0][:top * 2]
    rankings.append(kw)
    if qvec is not None and doc_vecs:
        sims = [(i, float(np.dot(qvec, v))) for i, v in enumerate(doc_vecs) if v is not None and len(v) == len(qvec)]
        sims.sort(key=lambda x: -x[1])
        rankings.append([i for i, s in sims[:top * 2] if s > 0.35])
    fused = rrf(rankings)
    return sorted(fused.items(), key=lambda x: -x[1])[:top]


def cosine_dupe(qvec, vecs, thr: float = 0.92) -> int | None:
    best, bi = 0.0, None
    for i, v in enumerate(vecs):
        if v is not None and len(v) == len(qvec):
            s = float(np.dot(qvec, v))
            if s > best:
                best, bi = s, i
    return bi if best >= thr else None
