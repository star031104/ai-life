"""语音：TTS（edge / GPT-SoVITS / OpenAI 兼容）、ASR（OpenAI 兼容 / faster-whisper）、ffmpeg 转码。"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import shutil
import wave

import httpx

log = logging.getLogger("ailife.voice")

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


async def ffmpeg(args: list[str], data: bytes) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        FFMPEG, "-hide_banner", "-loglevel", "error", *args,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(data)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败：{err.decode(errors='ignore')[:300]}")
    return out


async def to_pcm(audio: bytes, rate: int = 48000, channels: int = 1) -> bytes:
    """任意音频 -> s16le PCM。"""
    return await ffmpeg(["-i", "pipe:0", "-f", "s16le", "-ac", str(channels), "-ar", str(rate), "pipe:1"], audio)


async def to_ogg_opus(audio: bytes) -> bytes:
    """任意音频 -> Telegram 语音条格式（ogg/opus）。"""
    return await ffmpeg(["-i", "pipe:0", "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "32k",
                         "-f", "ogg", "pipe:1"], audio)


def pcm_to_wav(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def clean_for_speech(text: str) -> str:
    text = re.sub(r"[\[【][^\]】]*[\]】]", "", text)
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    text = re.sub(r"[*_#`~>|]", "", text)
    # 去掉 emoji
    text = re.sub(r"[\U0001F000-\U0001FFFF☀-➿️]", "", text)
    return text.strip()


# 可在 Telegram 里用 /voices 试听、/voice 编号 切换的音色：(名字, 引擎, 音色, 语速, 音调)
# local = 本机 Kokoro（免费、不联网）；cosy = 硅基流动 CosyVoice2（收费，约 0.15 元/千字）；
# edge = 微软免费语音（目前常被微软拒绝）
VOICES = [
    ("本地免费·少女音 A", "local", "23", "1.05", ""),
    ("本地免费·少女音 B", "local", "11", "1.05", ""),
    ("本地免费·少女音 C", "local", "3", "1.05", ""),
    ("本地免费·少女音 D", "local", "27", "1.0", ""),
    ("本地免费·少女音 E", "local", "20", "1.0", ""),
    ("本地免费·少女音 F", "local", "14", "1.0", ""),
    ("微软免费·活泼少女（常失效）", "edge", "zh-CN-XiaoyiNeural", "+0%", "+0Hz"),
    # 男声（给男性角色用；Kokoro v1.1 里 58 号以后是男声）
    ("本地免费·男声 A", "local", "58", "1.0", ""),
    ("本地免费·男声 B", "local", "61", "1.0", ""),
    ("本地免费·男声 C", "local", "80", "1.0", ""),
    ("本地免费·男声 D", "local", "90", "1.0", ""),
]
MALE_SIDS = {"58", "61", "80", "90"}


def voice_gender(v: tuple) -> str:
    """VOICES 里一项是女声还是男声（给 /voices 按角色性别筛）。"""
    label = v[0]
    return "male" if "男声" in label else "female"
SOVITS_LABEL = "定制声音（GPT-SoVITS，带情绪）"
FALLBACK = ("local", "23", "1.05", "")        # 首选失败时的备用声音（免费）
SAMPLE_LINE = "你好呀，今天过得怎么样？记得按时吃饭哦。"       # 人设里 voice.sample 可以换成她自己的一句话


# 本地 Kokoro 没有情绪控制，用一点语速变化表达语气
STYLE_SPEED = {"开心": 1.06, "撒娇": 0.96, "难过": 0.9, "生气": 1.08}


def _key(c, cfg) -> str:
    return c.get("openai_api_key") or (cfg.get("keys") or {}).get("siliconflow", "")


class TTS:
    def __init__(self, cfg):
        self.c = cfg.tts
        self.key = _key(self.c, cfg)
        self.proxy = cfg.proxy or None
        self.override: tuple[str, str, str] | None = None     # (voice, rate, pitch)
        self.persona = None             # run.py 挂上：人设里的 voice.sid / speed / sample、性别
        self.on_degrade = None          # 选中的声音连续失败、自动换回本地声音时回调（bot 用来通知你）
        self._fails = 0
        self._edge_ok_route: str | None = "unset"
        from .local_tts import LocalTTS
        self.local = LocalTTS(cfg.root, self.proxy)
        # GPT-SoVITS：按语气准备的几段参考音频（默认/开心/撒娇/难过/生气），配置了就出现在 /voices 列表最后
        self.refs = {k: dict(v) for k, v in (self.c.get("gpt_sovits_refs") or {}).items()
                     if isinstance(v, dict) and v.get("audio")}
        if not self.refs and self.c.get("gpt_sovits_ref_audio") and "D:/voices/ref.wav" not in str(
                self.c.get("gpt_sovits_ref_audio")):
            self.refs = {"默认": {"audio": self.c.gpt_sovits_ref_audio, "text": self.c.gpt_sovits_ref_text}}
        if self.refs and not any(v[1] == "sovits" for v in VOICES):
            VOICES.append((SOVITS_LABEL, "sovits", "", "", ""))

    def set_voice(self, idx: int | None):
        self.override = VOICES[idx][1:] if idx is not None and 0 <= idx < len(VOICES) else None

    def local_voice(self) -> tuple[int, float]:
        """本地声音的音色和语速：人设 voice 优先；男性角色没指定时用男声。"""
        pv = dict(getattr(self.persona, "voice", None) or {})
        sid = pv.get("sid")
        if sid is None:
            sid = self.c.get("local_sid", 23)
            if getattr(self.persona, "gender", "female") == "male" and str(sid) not in MALE_SIDS:
                sid = 58
        try:
            return int(sid), float(pv.get("speed") or self.c.get("local_speed", 1.05))
        except (TypeError, ValueError):
            return 23, 1.05

    def sample_line(self) -> str:
        pv = dict(getattr(self.persona, "voice", None) or {})
        return str(pv.get("sample") or SAMPLE_LINE)

    async def synth(self, text: str, voice: tuple | None = None, style: str | None = None) -> bytes:
        """返回压缩音频（mp3/wav 等）。style＝语气（开心/撒娇/难过/生气/默认）。
        选中的声音失败时自动换备用声音，全失败才抛异常。"""
        text = clean_for_speech(text)
        if not text:
            raise ValueError("空文本")
        v = voice or self.override
        try:
            if v:
                out = await self._by_voice(text, v, style)
            else:
                engine = self.c.engine
                if engine == "local":
                    sid, speed = self.local_voice()
                    out = await self.local.synth(text, sid, speed * STYLE_SPEED.get(style, 1.0))
                elif engine == "gpt_sovits":
                    out = await self._gpt_sovits(text, style)
                elif engine == "openai":
                    out = await self._openai(text)
                else:
                    out = await self._edge(text)
            if voice is None:
                self._fails = 0
            return out
        except Exception as e:  # noqa: BLE001
            if voice is not None or (v or ()) [:1] == ("local",):   # 试听时、或本来就是本地声音时不再换
                raise
            log.warning("语音合成失败，改用备用声音：%s", str(e)[:150])
            self._fails = getattr(self, "_fails", 0) + 1
            if self._fails >= 3 and self.override is not None:      # 连续失败 3 次：换回本地声音，并告诉他
                label = next((x[0] for x in VOICES if tuple(x[1:]) == tuple(self.override)), "选中的声音")
                self.override = None
                self._fails = 0
                if self.on_degrade:
                    try:
                        self.on_degrade(label)
                    except Exception:  # noqa: BLE001
                        pass
            return await self._by_voice(text, FALLBACK, style)

    async def _by_voice(self, text: str, v: tuple, style: str | None = None) -> bytes:
        engine, name, rate, pitch = v
        if engine == "local":
            return await self.local.synth(text, int(name), float(rate or 1.0) * STYLE_SPEED.get(style, 1.0))
        if engine == "sovits":
            return await self._gpt_sovits(text, style)
        if engine == "cosy":
            return await self._openai(text, model="FunAudioLLM/CosyVoice2-0.5B",
                                      voice=f"FunAudioLLM/CosyVoice2-0.5B:{name}",
                                      base_url="https://api.siliconflow.cn/v1")
        return await self._edge(text, (name, rate, pitch))

    async def _edge(self, text: str, v: tuple[str, str, str] | None = None) -> bytes:
        """微软 edge 语音。国内直连常被拒（403），所以优先走代理，失败再直连。"""
        import edge_tts
        name, rate, pitch = v or (self.c.edge_voice, self.c.edge_rate, self.c.edge_pitch)
        routes = [self.proxy, None] if self.proxy else [None]
        if self._edge_ok_route in routes:          # 上次成功的方式放前面
            routes.remove(self._edge_ok_route)
            routes.insert(0, self._edge_ok_route)
        last = None
        for proxy in routes:
            try:
                com = edge_tts.Communicate(text, name, rate=rate, pitch=pitch, proxy=proxy,
                                           connect_timeout=10, receive_timeout=30)
                buf = bytearray()
                async for chunk in com.stream():
                    if chunk["type"] == "audio":
                        buf += chunk["data"]
                if not buf:
                    raise RuntimeError("edge-tts 没有返回音频")
                self._edge_ok_route = proxy
                return bytes(buf)
            except Exception as e:  # noqa: BLE001
                last = e
                log.info("edge-tts %s 失败：%s", "走代理" if proxy else "直连", str(e)[:120])
        raise RuntimeError(f"edge-tts 失败（代理和直连都试过）：{str(last)[:200]}")

    async def _gpt_sovits(self, text: str, style: str | None = None) -> bytes:
        """GPT-SoVITS api_v2：按语气挑参考音频（没配这个语气就用“默认”）。"""
        refs = self.refs or {"默认": {"audio": self.c.gpt_sovits_ref_audio, "text": self.c.gpt_sovits_ref_text}}
        ref = refs.get(style or "默认") or refs.get("默认") or next(iter(refs.values()))
        params = {
            "text": text, "text_lang": self.c.gpt_sovits_lang,
            "ref_audio_path": ref["audio"],
            "prompt_text": ref.get("text", ""), "prompt_lang": self.c.gpt_sovits_lang,
            "media_type": "wav", "streaming_mode": False,
        }
        async with httpx.AsyncClient(timeout=120) as cli:
            r = await cli.post(self.c.gpt_sovits_url, json=params)
            r.raise_for_status()
            return r.content

    async def _openai(self, text: str, model: str | None = None, voice: str | None = None,
                      base_url: str | None = None) -> bytes:
        async with httpx.AsyncClient(timeout=120) as cli:
            r = await cli.post(
                (base_url or self.c.openai_base_url).rstrip("/") + "/audio/speech",
                headers={"Authorization": f"Bearer {self.key}"},
                json={"model": model or self.c.openai_model, "voice": voice or self.c.openai_voice,
                      "input": text, "response_format": "mp3"},
            )
            r.raise_for_status()
            return r.content


class ASR:
    def __init__(self, cfg):
        self.c = cfg.asr
        self.key = _key(self.c, cfg)
        self._whisper = None
        self.local = None
        if self.c.get("engine", "local") == "local":
            from .local_asr import LocalASR
            self.local = LocalASR(cfg.root, cfg.proxy or None)

    async def transcribe_ex(self, audio: bytes, filename: str = "voice.ogg") -> tuple[str, str | None, str | None]:
        """返回 (文字, 语气, 声音事件)。本地识别失败时改用在线接口（只有文字）。"""
        if self.local and (self.local.ready_on_disk() or not self.key):
            try:
                return await self.local.transcribe(audio)
            except Exception as e:  # noqa: BLE001
                log.warning("本地语音识别失败，改用在线：%s", str(e)[:150])
                if not self.key:
                    raise
                return await self._online(audio, filename), None, None
        if self.local and self.key:             # 模型还没下好：这次先用在线的，别让他干等
            return await self._online(audio, filename), None, None
        return await self.transcribe(audio, filename), None, None

    async def transcribe(self, audio: bytes, filename: str = "audio.wav") -> str:
        if self.local and (self.local.ready_on_disk() or not self.key):
            try:
                return (await self.local.transcribe(audio))[0]
            except Exception as e:  # noqa: BLE001
                log.warning("本地语音识别失败，改用在线：%s", str(e)[:150])
        if self.c.engine == "faster_whisper":
            return await asyncio.to_thread(self._fw, audio)
        return await self._online(audio, filename)

    async def _online(self, audio: bytes, filename: str) -> str:
        async with httpx.AsyncClient(timeout=120) as cli:
            r = await cli.post(
                self.c.openai_base_url.rstrip("/") + "/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.key}"},
                data={"model": self.c.openai_model},
                files={"file": (filename, audio)},
            )
            r.raise_for_status()
            return (r.json().get("text") or "").strip()

    def _fw(self, audio: bytes) -> str:
        if self._whisper is None:
            from faster_whisper import WhisperModel
            dev = self.c.whisper_device
            if dev == "auto":
                try:
                    import ctranslate2
                    dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                except Exception:  # noqa: BLE001
                    dev = "cpu"
            self._whisper = WhisperModel(self.c.whisper_model, device=dev,
                                         compute_type="float16" if dev == "cuda" else "int8")
        segs, _ = self._whisper.transcribe(io.BytesIO(audio), language="zh", vad_filter=True)
        return "".join(s.text for s in segs).strip()
