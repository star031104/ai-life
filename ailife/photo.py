"""她的“照片”：用智谱免费的 cogview-3-flash 画图（自拍、吃的、看到的风景）。"""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("ailife.photo")

# 形象描述写在人设的 look 里（保证每张“自拍”是同一个人）；没写就只拍景物、不画人脸
DEFAULT_LOOK = ""


class PhotoMaker:
    def __init__(self, cfg, db):
        c = cfg.get("photo") or {}
        self.enabled = bool(c.get("enabled", True))
        self.model = c.get("model", "cogview-3-flash")
        self.size = c.get("size", "768x1344")
        self.max_per_day = int(c.get("max_per_day", 3))
        self._cfg_look = c.get("look") or DEFAULT_LOOK
        self.persona = None             # Brain 挂上：人设里的 look 优先
        self.base_url = c.get("base_url", "https://open.bigmodel.cn/api/paas/v4")
        self.key = c.get("api_key") or (cfg.get("keys") or {}).get(c.get("key", "zhipu"), "")
        self.db = db
        self.tz = cfg.tz

    def _today_key(self) -> str:
        from datetime import datetime
        return "photos_" + datetime.now(self.tz).strftime("%Y-%m-%d")

    def remaining_today(self) -> int:
        return max(0, self.max_per_day - int(self.db.get(self._today_key(), 0)))

    @property
    def look(self) -> str:
        return (getattr(self.persona, "look", "") or self._cfg_look or "").strip()

    def build_prompt(self, scene: str) -> str:
        scene = scene.strip()
        selfie_words = ("自拍", "我", "穿", "表情", "比", "脸", "合照")
        if any(w in scene for w in selfie_words):
            if not self.look:                   # 人设里没写长相：拍她看到的东西，不画人脸
                return f"日系动漫插画风格的第一人称手机照片，画面里不出现人脸：{scene}。生活感，自然光，画面干净"
            return f"{self.look}。画面：{scene}。手机自拍视角，自然光，温馨日常，画面干净"
        return f"日系动漫插画风格的手机随手拍：{scene}。生活感，自然光，画面干净，不出现文字"

    async def make(self, scene: str) -> bytes | None:
        """返回图片字节；超出当日上限或失败返回 None。"""
        if not self.enabled or not self.key:
            return None
        if self.remaining_today() <= 0:
            log.info("今天的照片额度用完了")
            return None
        prompt = self.build_prompt(scene)
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=90) as cli:
                r = await cli.post(f"{self.base_url}/images/generations",
                                   headers={"Authorization": f"Bearer {self.key}"},
                                   json={"model": self.model, "prompt": prompt, "size": self.size})
                r.raise_for_status()
                url = r.json()["data"][0]["url"]
                img = await cli.get(url)
                img.raise_for_status()
        except Exception as e:  # noqa: BLE001
            log.warning("画图失败：%s", e)
            return None
        self.db.set(self._today_key(), int(self.db.get(self._today_key(), 0)) + 1)
        log.info("画好一张照片（%.1fs）：%s", time.time() - t0, scene)
        return img.content
