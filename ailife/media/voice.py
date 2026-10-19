"""11.2 她的语音：很短、想让他听见声音的时候（撒娇、早安、晚安、哄他），这一条用语音发。

用 OpenAI 兼容的 /audio/speech 接口（默认硅基流动的 CosyVoice2，和看图用同一个 Key；一条二三十个字不到 1 分钱）。
声音在 config.yaml 的 voice.voice 里选，人设里写了 voice: {voice: …} 就用人设的。每天最多 max_per_day 条，
太长的话（超过 max_chars 个字）不念，照样发文字。没配 / 失败也照样发文字。
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

import httpx

log = logging.getLogger("ailife.voice")


class Voice:
    def __init__(self, cfg, db, persona=None):
        c = dict(cfg.get("voice") or {})
        self.enabled = bool(c.get("enabled", False))
        self.base_url = str(c.get("base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
        self.api_key = str(c.get("api_key") or "").strip()
        self.model = str(c.get("model") or "FunAudioLLM/CosyVoice2-0.5B")
        pv = dict(getattr(persona, "voice", None) or {})
        self.voice = str(pv.get("voice") or c.get("voice") or f"{self.model}:diana")
        self.speed = float(pv.get("speed") or c.get("speed") or 1.0)
        self.max_per_day = int(c.get("max_per_day", 6))
        self.max_chars = int(c.get("max_chars", 60))
        self.proxy = cfg.get("proxy") if c.get("use_proxy") else None
        self.db, self.tz = db, cfg.get("tz") or getattr(cfg, "tz", None)

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.api_key) and self.max_per_day > 0

    def _key(self) -> str:
        return f"voice_{datetime.now(self.tz):%Y-%m-%d}"

    def remaining_today(self) -> int:
        return max(0, self.max_per_day - int(self.db.get(self._key(), 0) or 0)) if self.ready else 0

    @staticmethod
    def speakable(text: str) -> str:
        """念出来的字：去掉表情符号、颜文字、括号里的东西。"""
        t = re.sub(r"[\(（][^\)）]{0,20}[\)）]", "", text or "")
        t = re.sub(r"[^\w\s，。！？、,.!?~～…：:；;“”\"'—-]", "", t)
        return t.strip()

    async def speak(self, text: str) -> bytes | None:
        """返回 mp3 字节；不能念 / 失败返回 None（调用方照样发文字）。"""
        words = self.speakable(text)
        if not self.ready or self.remaining_today() <= 0 or not words or len(words) > self.max_chars:
            return None
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=40, proxy=self.proxy) as cli:
                r = await cli.post(f"{self.base_url}/audio/speech", headers={"Authorization": f"Bearer {self.api_key}"},
                                   json={"model": self.model, "input": words, "voice": self.voice,
                                         "response_format": "mp3", "speed": self.speed})
                r.raise_for_status()
                data = r.content
        except Exception as e:  # noqa: BLE001
            log.warning("念语音失败：%s", str(e)[:200])
            return None
        if len(data) < 500:
            log.warning("语音接口返回的不像音频（%d 字节）", len(data))
            return None
        self.db.set(self._key(), int(self.db.get(self._key(), 0) or 0) + 1)
        log.info("念了一条语音（%.1fs，%d 字）", time.time() - t0, len(words))
        return data
