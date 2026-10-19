"""小屋的门卫：只认本机地址、拒绝跨站调用（服务器上用 SSH 隧道在自己电脑打开）。"""
from __future__ import annotations

from urllib.parse import urlparse

from aiohttp import web

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


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
