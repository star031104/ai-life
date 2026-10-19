"""连一个 OpenAI 兼容的接口：整理地址、列出模型、试聊一句（推理模型自动试几种“关掉思考”的参数）。"""
from __future__ import annotations

import re
import time

TEST_PROMPT = "用一句话、十个字以内跟我打个招呼。"
NO_THINK = ({"enable_thinking": False}, {"thinking": {"type": "disabled"}}, {"reasoning_effort": "none"},
            {"think": False})


def normalize_url(u: str) -> str:
    u = (u or "").strip().strip('"').strip("'").rstrip("/")
    if u and not re.match(r"^https?://", u):
        u = ("http://" if re.match(r"^(localhost|127\.|192\.168\.|10\.)", u) else "https://") + u
    for tail in ("/chat/completions", "/completions", "/embeddings", "/images/generations", "/models"):
        if u.endswith(tail):
            u = u[: -len(tail)]
    return u


def url_candidates(u: str) -> list[str]:
    """填的地址没带版本号（/v1 这种）的话，两个都试一下。"""
    u = normalize_url(u)
    out = [u]
    if u and not re.search(r"/v\d+[a-z]*$", u):
        out.append(u + "/v1")
    return out


async def list_models(base_url: str, key: str, proxy: str = "") -> tuple[list[str], str, bool, str]:
    """返回 (模型 id 列表, 能用的地址, 是否要走代理, 出错信息)。"""
    import httpx
    errs = []
    for url in url_candidates(base_url):
        for px in ([None, proxy] if proxy else [None]):
            try:
                async with httpx.AsyncClient(timeout=20, proxy=px, trust_env=False) as c:
                    r = await c.get(url + "/models", headers={"Authorization": f"Bearer {key}"} if key else {})
                if r.status_code in (401, 403):
                    return [], url, bool(px), f"Key 不对或没有权限（HTTP {r.status_code}）：{r.text[:150]}"
                r.raise_for_status()
                data = r.json()
                items = data.get("data") if isinstance(data, dict) else data
                ids = [str(m.get("id") or m.get("name") or "") if isinstance(m, dict) else str(m) for m in items or []]
                if isinstance(data, dict) and not ids and data.get("models"):          # Ollama 原生格式
                    ids = [str(m.get("name") or m.get("model")) for m in data["models"]]
                return sorted({i for i in ids if i}), url, bool(px), ""
            except Exception as e:  # noqa: BLE001
                errs.append(f"{url}{'（走代理）' if px else ''}：{str(e)[:120]}")
    return [], normalize_url(base_url), False, "；".join(errs)


async def test_chat(base_url: str, key: str, model: str, proxy: str = "", via_proxy: bool = False,
                    extra_body: dict | None = None) -> tuple[bool, float, str]:
    """试聊一句。返回 (成功, 用时秒, 回复或出错信息)。"""
    import httpx
    from openai import AsyncOpenAI
    from ailife.llm.client import strip_think
    http = httpx.AsyncClient(proxy=proxy, timeout=60) if via_proxy and proxy else None
    client = AsyncOpenAI(base_url=base_url, api_key=key or "none", http_client=http, timeout=60, max_retries=0)
    t0 = time.time()
    try:
        r = await client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": TEST_PROMPT}],
            max_tokens=200, temperature=0.7, **({"extra_body": extra_body} if extra_body else {}))
        text = strip_think(r.choices[0].message.content or "").strip()
        if not text:
            return False, time.time() - t0, "回复是空的（可能是推理模型把字数都用在思考上了）"
        return True, time.time() - t0, text
    except Exception as e:  # noqa: BLE001
        return False, time.time() - t0, str(e)[:300]
    finally:
        await client.close()


async def test_embed(base_url: str, key: str, model: str, proxy: str = "", via_proxy: bool = False
                     ) -> tuple[bool, float, str]:
    import httpx
    from openai import AsyncOpenAI
    http = httpx.AsyncClient(proxy=proxy, timeout=30) if via_proxy and proxy else None
    client = AsyncOpenAI(base_url=base_url, api_key=key or "none", http_client=http, timeout=30, max_retries=0)
    t0 = time.time()
    try:
        r = await client.embeddings.create(model=model, input=["你好"])
        return True, time.time() - t0, f"向量 {len(r.data[0].embedding)} 维"
    except Exception as e:  # noqa: BLE001
        return False, time.time() - t0, str(e)[:300]
    finally:
        await client.close()


async def probe(base_url: str, key: str, model: str, kind: str = "chat", proxy: str = "",
                via_proxy: bool = False) -> tuple[bool, float, str, dict | None]:
    """按模型的种类试一下。返回 (成功, 用时, 回复/说明/错误, 聊天时要带的 extra_body)。
    画图模型不试（每画一张都要钱或额度），直接算通过。"""
    if kind == "embed":
        ok, secs, text = await test_embed(base_url, key, model, proxy, via_proxy)
        return ok, secs, text, None
    if kind == "image":
        return True, 0.0, "画图模型不试画（省额度），第一次发照片时就知道行不行", None
    ok, secs, text = await test_chat(base_url, key, model, proxy, via_proxy)
    if not ok and ("空的" in text or "think" in text.lower()):
        for eb in NO_THINK:
            ok2, secs2, text2 = await test_chat(base_url, key, model, proxy, via_proxy, eb)
            if ok2:
                return ok2, secs2, text2, eb
    return ok, secs, text, None
