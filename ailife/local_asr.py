"""本地免费语音识别：sherpa-onnx + SenseVoice（约 160MB，第一次用时自动下载）。

除了把你的语音转成文字，还能听出语气（开心 / 难过 / 生气…）和笑声、哭腔，
这些会一起告诉她。CPU 就能跑，一条十几秒的语音大约 0.5 秒。
"""
from __future__ import annotations

import asyncio
import logging
import os
import tarfile
import threading
from pathlib import Path

import httpx
import numpy as np

log = logging.getLogger("ailife.local_asr")

MODEL_NAME = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
TAR_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_NAME}.tar.bz2"
HF = "https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main"

EMOTIONS = {"HAPPY": "开心", "SAD": "难过", "ANGRY": "生气", "FEARFUL": "害怕",
            "DISGUSTED": "嫌弃", "SURPRISED": "惊讶", "NEUTRAL": None, "EMO_UNKNOWN": None}
EVENTS = {"Laughter": "笑了", "Cry": "带着哭腔", "Crying": "带着哭腔", "Cough": "咳嗽"}


def _tag(x: str | None) -> str:
    return (x or "").strip().strip("<|>").strip()


class LocalASR:
    def __init__(self, root: Path, proxy: str | None = None):
        self.dir = root / "data" / "asr" / MODEL_NAME
        self.proxy = proxy
        self._rec = None
        self._lock = threading.RLock()
        self._dl = asyncio.Lock()
        self.status = "未下载" if not self.ready_on_disk() else "未加载"

    def ready_on_disk(self) -> bool:
        return (self.dir / "model.int8.onnx").exists() and (self.dir / "tokens.txt").exists()

    async def _get(self, url: str, dst: Path, proxy):
        async with httpx.AsyncClient(proxy=proxy, timeout=60, follow_redirects=True) as cli:
            async with cli.stream("GET", url) as r:
                r.raise_for_status()
                with open(dst, "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 20):
                        f.write(chunk)

    async def ensure_model(self):
        if self.ready_on_disk():
            return
        async with self._dl:
            if self.ready_on_disk():
                return
            self.dir.parent.mkdir(parents=True, exist_ok=True)
            last = None
            for proxy in ([self.proxy, None] if self.proxy else [None]):
                try:          # 1) GitHub 打包好的压缩包
                    self.status = "下载中"
                    log.info("正在下载本地语音识别模型（约 160MB，只需一次）%s", "（走代理）" if proxy else "")
                    tmp = self.dir.parent / f"{MODEL_NAME}.tar.bz2.part"
                    await self._get(TAR_URL, tmp, proxy)
                    await asyncio.to_thread(self._extract, tmp)
                    tmp.unlink(missing_ok=True)
                    if self.ready_on_disk():
                        self.status = "未加载"
                        log.info("语音识别模型下载完成")
                        return
                except Exception as e:  # noqa: BLE001
                    last = e
                    log.warning("下载语音识别模型失败：%s", str(e)[:150])
            try:              # 2) 国内镜像，单独下两个文件
                self.dir.mkdir(parents=True, exist_ok=True)
                for name in ("tokens.txt", "model.int8.onnx"):
                    tmp = self.dir / (name + ".part")
                    await self._get(f"{HF}/{name}", tmp, None)
                    os.replace(tmp, self.dir / name)
                self.status = "未加载"
                log.info("语音识别模型下载完成（镜像）")
                return
            except Exception as e:  # noqa: BLE001
                last = e
            self.status = "下载失败"
            raise RuntimeError(f"语音识别模型下载失败：{last}")

    def _extract(self, tar_path: Path):
        with tarfile.open(tar_path, "r:bz2") as t:
            members = [m for m in t.getmembers() if m.name.endswith(("model.int8.onnx", "tokens.txt"))]
            t.extractall(self.dir.parent, members=members)

    def _load(self):
        with self._lock:
            self._load_locked()

    def _load_locked(self):
        if self._rec is not None:
            return
        import sherpa_onnx
        self._rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(self.dir / "model.int8.onnx"), tokens=str(self.dir / "tokens.txt"),
            use_itn=True, language="auto", num_threads=max(2, min(4, (os.cpu_count() or 4) // 2)))
        self.status = "可用"
        log.info("本地语音识别模型已加载")

    def _run(self, pcm16k: bytes) -> tuple[str, str | None, str | None]:
        x = np.frombuffer(pcm16k, dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            self._load()
            s = self._rec.create_stream()
            s.accept_waveform(16000, x)
            self._rec.decode_stream(s)
            r = s.result
        emo = EMOTIONS.get(_tag(getattr(r, "emotion", "")))
        ev = EVENTS.get(_tag(getattr(r, "event", "")))
        return (r.text or "").strip(), emo, ev

    async def transcribe(self, audio: bytes) -> tuple[str, str | None, str | None]:
        """返回 (文字, 语气, 声音事件)。audio 是任意格式（会用 ffmpeg 转成 16k 单声道）。"""
        from .voice import to_pcm
        await self.ensure_model()
        pcm = await to_pcm(audio, rate=16000)
        return await asyncio.to_thread(self._run, pcm)
