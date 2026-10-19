"""画图（她的照片）：用路由里 image 用途的模型，调 OpenAI 兼容的 /images/generations；没配就不发照片。"""
from __future__ import annotations

import base64
import logging

import httpx

log = logging.getLogger("ailife.photo")


class ImageGen:
    def __init__(self, cfg):
        llm = cfg.get("llm") or {}
        profiles = llm.get("profiles") or {}
        self.chain = [dict(profiles[n], name=n) for n in ((llm.get("routes") or {}).get("image") or []) if n in profiles]
        self.proxy = cfg.get("proxy") or None

    @property
    def ready(self) -> bool:
        return bool(self.chain)

    async def generate(self, prompt: str, size: str) -> bytes | None:
        """按顺序试，返回图片字节；都失败返回 None。"""
        for p in self.chain:
            try:
                async with httpx.AsyncClient(timeout=float(p.get("timeout", 90)),
                                             proxy=self.proxy if p.get("use_proxy") else None) as cli:
                    r = await cli.post(f"{str(p['base_url']).rstrip('/')}/images/generations",
                                       headers={"Authorization": f"Bearer {p.get('api_key') or 'none'}"},
                                       json={"model": p["model"], "prompt": prompt, "size": size,
                                             **dict(p.get("extra_body") or {})})
                    r.raise_for_status()
                    item = r.json()["data"][0]
                    if item.get("b64_json"):
                        return base64.b64decode(item["b64_json"])
                    img = await cli.get(item["url"])
                    img.raise_for_status()
                    return img.content
            except Exception as e:  # noqa: BLE001
                log.warning("用 %s 画图失败：%s", p["name"], str(e)[:200])
        return None
