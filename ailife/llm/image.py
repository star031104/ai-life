"""画图（她的照片）：用路由里 image 用途的模型，调 OpenAI 兼容的 /images/generations；没配就不发照片。

17.0：route=image_ref 是“画她自己”——带 data/avatar/ 里的参考图（Qwen-Image-Edit-2509 一类，参考图走 image / image2 / image3，
最多 3 张；这类编辑模型不收 size）。没配 image_ref 或没放参考图，就照旧只用文字画。"""
from __future__ import annotations

import base64
import logging

import httpx

log = logging.getLogger("ailife.photo")


class ImageGen:
    def __init__(self, cfg, route: str = "image"):
        llm = cfg.get("llm") or {}
        profiles = llm.get("profiles") or {}
        self.chain = [dict(profiles[n], name=n) for n in ((llm.get("routes") or {}).get(route) or []) if n in profiles]
        self.proxy = cfg.get("proxy") or None

    @staticmethod
    def _uri(b: bytes) -> str:
        mime = "image/png" if b[:4] == b"\x89PNG" else "image/webp" if b[8:12] == b"WEBP" else "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(b).decode()}"

    @property
    def ready(self) -> bool:
        return bool(self.chain)

    async def generate(self, prompt: str, size: str, refs: list[bytes] | None = None) -> bytes | None:
        """按顺序试，返回图片字节；都失败返回 None。refs：参考图（画她自己）。"""
        for p in self.chain:
            try:
                body = {"model": p["model"], "prompt": prompt, **dict(p.get("extra_body") or {})}
                for i, b in enumerate((refs or [])[:3]):
                    body["image" if i == 0 else f"image{i + 1}"] = self._uri(b)
                if not (refs and "edit" in str(p["model"]).lower()):
                    body.update(size=size, image_size=size)
                async with httpx.AsyncClient(timeout=float(p.get("timeout", 90)),
                                             proxy=self.proxy if p.get("use_proxy") else None) as cli:
                    r = await cli.post(f"{str(p['base_url']).rstrip('/')}/images/generations",
                                       headers={"Authorization": f"Bearer {p.get('api_key') or 'none'}"},
                                       json=body)
                    r.raise_for_status()
                    d = r.json()
                    item = (d.get("data") or d.get("images") or [{}])[0]      # OpenAI 是 data，硅基流动是 images
                    if item.get("b64_json"):
                        return base64.b64decode(item["b64_json"])
                    img = await cli.get(item["url"])
                    img.raise_for_status()
                    return img.content
            except Exception as e:  # noqa: BLE001
                log.warning("用 %s 画图失败：%s", p["name"], str(e)[:200])
        return None
