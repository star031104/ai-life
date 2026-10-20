"""4.2 她的“照片”：偶尔在群里晒一张日常（吃的、桌面、窗外、宠物），不拍自己的脸。

用 config.llm.yaml 里 routes.image 的画图接口（OpenAI 兼容的 /images/generations；硅基流动的 Z-Image-Turbo / Kolors 都行）；
没配就不发。每天最多 photo_per_day 张（默认 1）。画风固定写在人设的 look 里（没写就用日系插画风）。
"""
from __future__ import annotations

import base64
import logging
import time
from datetime import datetime

import httpx

log = logging.getLogger("aigroup.photo")

STYLE = "日系插画风格的手机随手拍，生活感，自然光，画面干净，不出现文字，不出现人脸"


class ImageGen:
    def __init__(self, cfg):
        llm = cfg.get("llm") or {}
        profiles = llm.get("profiles") or {}
        self.chain = [dict(profiles[n], name=n) for n in ((llm.get("routes") or {}).get("image") or []) if n in profiles]
        self.proxy = cfg.get("proxy") or None

    @property
    def ready(self) -> bool:
        return bool(self.chain)

    async def generate(self, prompt: str, size: str = "768x1024") -> bytes | None:
        for p in self.chain:
            try:
                async with httpx.AsyncClient(timeout=float(p.get("timeout", 90)),
                                             proxy=self.proxy if p.get("use_proxy") else None) as cli:
                    r = await cli.post(f"{str(p['base_url']).rstrip('/')}/images/generations",
                                       headers={"Authorization": f"Bearer {p.get('api_key') or 'none'}"},
                                       json={"model": p["model"], "prompt": prompt, "size": size, "image_size": size,
                                             **dict(p.get("extra_body") or {})})
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


class PhotoMaker:
    def __init__(self, cfg, db, persona=None, per_day: int = 1):
        self.gen = ImageGen(cfg)
        self.db, self.tz, self.persona, self.per_day = db, cfg.tz, persona, int(per_day)

    @property
    def ready(self) -> bool:
        return self.gen.ready and self.per_day > 0

    def _key(self) -> str:
        return f"qq_photos_{datetime.now(self.tz):%Y-%m-%d}"

    def remaining_today(self) -> int:
        return max(0, self.per_day - int(self.db.get(self._key(), 0) or 0))

    def prompt(self, scene: str) -> str:
        look = str(getattr(self.persona, "photo_style", "") or "").strip()
        return f"{look or STYLE}。画面：{scene.strip()}"

    async def make(self, scene: str) -> bytes | None:
        if not self.ready or self.remaining_today() <= 0:
            return None
        llm = getattr(self, "llm", None)
        if llm is not None and hasattr(llm, "budget"):
            from aigroup.llm.budget import BudgetExceeded
            try:
                llm.budget.check(float(llm.stats().get("usd", 0) or 0), llm._today(), False)
            except BudgetExceeded:
                return None
        t0 = time.time()
        data = await self.gen.generate(self.prompt(scene))
        if data:
            if llm is not None and hasattr(llm, "add_cost"):
                llm.add_cost(llm.budget.image_usd)          # 画图按张记账
            self.db.set(self._key(), int(self.db.get(self._key(), 0) or 0) + 1)
            log.info("画好一张照片（%.1fs）：%s", time.time() - t0, scene)
        return data
