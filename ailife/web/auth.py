"""小屋的门卫：Telegram Mini App 的签名校验，本机入口只认本机地址、拒绝跨站调用。"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl, urlparse

from aiohttp import web

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


def verify_init_data(init_data: str, bot_token: str, max_age: int = 7 * 86400) -> int | None:
    """校验 Telegram Mini App 的 initData，返回用户 id；不合法返回 None。"""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        got = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not got or not hmac.compare_digest(calc, got):
            return None
        if time.time() - int(pairs.get("auth_date", 0)) > max_age:
            return None
        return int(json.loads(pairs.get("user") or "{}").get("id") or 0) or None
    except Exception:  # noqa: BLE001
        return None


@web.middleware
async def local_only(req, handler):
    host = (req.host or "").split(":")[0].lower()
    if host not in LOCAL_HOSTS:
        raise web.HTTPForbidden(text="只能在本机打开")
    if req.path.startswith(("/api/", "/v1/", "/hook/")):
        # 别的网站在你浏览器里偷偷调用本机接口（跨站请求）一律拒绝；本机程序（没有 Origin）不受影响
        if req.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
            raise web.HTTPForbidden(text="不允许跨站调用")
        origin = req.headers.get("Origin")
        if origin and origin != "null":
            if (urlparse(origin).hostname or "").lower() not in LOCAL_HOSTS:
                raise web.HTTPForbidden(text="不允许跨站调用")
    return await handler(req)
