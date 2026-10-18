"""本地免费语音：sherpa-onnx + Kokoro v1.1 中文模型（约 150MB，第一次用时自动下载）。

不联网、不花钱，CPU 就能跑（笔记本上一句话大约 1~3 秒）。
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import tarfile
import threading
import time
import wave
from pathlib import Path

import httpx
import numpy as np

log = logging.getLogger("ailife.local_tts")

MODEL_NAME = "kokoro-int8-multi-lang-v1_1"
MODEL_URLS = [
    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/{MODEL_NAME}.tar.bz2",
    f"https://hf-mirror.com/csukuangfj/{MODEL_NAME}/resolve/main/{MODEL_NAME}.tar.bz2",
]


class LocalTTS:
    def __init__(self, root: Path, proxy: str | None = None, threads: int | None = None):
        self.dir = root / "data" / "tts" / MODEL_NAME
        self.proxy = proxy
        self.threads = threads or max(2, min(8, (os.cpu_count() or 4) - 1))
        self._tts = None
        self._lock = threading.RLock()
        self._download_lock = asyncio.Lock()
        self.status = "未下载" if not self.ready_on_disk() else "未加载"

    def ready_on_disk(self) -> bool:
        return (self.dir / "model.int8.onnx").exists() and (self.dir / "voices.bin").exists()

    # ---------------- 下载 ----------------
    async def ensure_model(self):
        if self.ready_on_disk():
            return
        async with self._download_lock:
            if self.ready_on_disk():
                return
            self.dir.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.dir.parent / f"{MODEL_NAME}.tar.bz2.part"
            last = None
            for url in MODEL_URLS:
                for proxy in ([self.proxy, None] if self.proxy else [None]):
                    try:
                        self.status = "下载中"
                        log.info("正在下载本地语音模型（约 150MB，只需一次）：%s%s", url, "（走代理）" if proxy else "")
                        async with httpx.AsyncClient(proxy=proxy, timeout=60, follow_redirects=True) as cli:
                            async with cli.stream("GET", url) as r:
                                r.raise_for_status()
                                total = int(r.headers.get("content-length") or 0)
                                got, shown = 0, 0
                                with open(tmp, "wb") as f:
                                    async for chunk in r.aiter_bytes(1 << 20):
                                        f.write(chunk)
                                        got += len(chunk)
                                        if total and got * 10 // total > shown:
                                            shown = got * 10 // total
                                            log.info("语音模型下载 %d%%", shown * 10)
                        await asyncio.to_thread(self._extract, tmp)
                        tmp.unlink(missing_ok=True)
                        self.status = "未加载"
                        log.info("本地语音模型下载完成")
                        return
                    except Exception as e:  # noqa: BLE001
                        last = e
                        log.warning("下载失败（%s）：%s", url, str(e)[:150])
            self.status = "下载失败"
            raise RuntimeError(f"本地语音模型下载失败：{last}")

    def _extract(self, tar_path: Path):
        with tarfile.open(tar_path, "r:bz2") as t:
            t.extractall(self.dir.parent)

    # ---------------- 合成 ----------------
    def _load(self):
        with self._lock:
            self._load_locked()

    def _load_locked(self):
        if self._tts is not None:
            return
        import sherpa_onnx
        d = str(self.dir)
        t0 = time.time()
        cfg = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                    model=f"{d}/model.int8.onnx", voices=f"{d}/voices.bin", tokens=f"{d}/tokens.txt",
                    data_dir=f"{d}/espeak-ng-data", dict_dir=f"{d}/dict",
                    lexicon=f"{d}/lexicon-us-en.txt,{d}/lexicon-zh.txt"),
                num_threads=self.threads),
            rule_fsts=f"{d}/date-zh.fst,{d}/number-zh.fst,{d}/phone-zh.fst",
            max_num_sentences=1)
        self._tts = sherpa_onnx.OfflineTts(cfg)
        self.status = "可用"
        log.info("本地语音模型已加载（%.1fs，%d 线程）", time.time() - t0, self.threads)

    def _gen(self, text: str, sid: int, speed: float) -> bytes:
        with self._lock:
            self._load()
            a = self._tts.generate(text, sid=int(sid), speed=float(speed))
        x = np.clip(np.asarray(a.samples, dtype=np.float32), -1, 1)
        pcm = (x * 32767).astype(np.int16).tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(a.sample_rate)
            w.writeframes(pcm)
        return buf.getvalue()

    async def synth(self, text: str, sid: int, speed: float = 1.0) -> bytes:
        await self.ensure_model()
        return await asyncio.to_thread(self._gen, text, sid, speed)
