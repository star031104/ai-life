"""打电话：用“她自己的” Telegram 账号（Telethon + py-tgcalls）给你打 TG 语音电话，实时对话。

链路：你的声音(48k PCM 帧) → 能量 VAD 断句 → ASR → 大模型流式输出 → 按句 TTS → 10ms 帧推回通话
支持：她主动打给你 / 你打给她（自动接听）/ 说话时被你打断 / 她说再见后自己挂断 / 通话摘要写回聊天记忆
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from urllib.parse import urlparse

import numpy as np

from .brain import Brain, append_user
from .proactive import in_quiet
from .voice import ASR, TTS, pcm_to_wav, to_pcm

log = logging.getLogger("ailife.caller")

RATE = 48000
FRAME_BYTES = RATE * 2 // 100          # 10ms，单声道 s16le
SENT_END = re.compile(r"[。！？!?；;…~\n]")
HANGUP_TAG = re.compile(r"[\[【]\s*挂断\s*[\]】]")


def telethon_proxy(url: str | None):
    if not url:
        return None
    u = urlparse(url)
    scheme = {"socks5h": "socks5", "https": "http", "socks4a": "socks4"}.get(u.scheme, u.scheme)
    port = u.port or (1080 if scheme.startswith("socks") else 8080)
    if u.username:
        return (scheme, u.hostname, port, True, u.username, u.password or "")
    return (scheme, u.hostname, port)


class CallSession:
    def __init__(self, caller: "Caller", chat_id: int, reason: str, she_called: bool):
        self.caller = caller
        self.chat_id = chat_id
        self.reason = reason
        self.she_called = she_called
        c = caller.cfg.call
        self.silence_frames = max(20, int(c.silence_ms) // 10)
        self.vad_k = float(c.vad_threshold)
        self.out = bytearray()                 # 待播放的 PCM
        self.utterances: asyncio.Queue[bytes] = asyncio.Queue()
        self.transcript: list[tuple[str, str]] = []   # (说话人, 内容)
        self.ended = asyncio.Event()
        self.hangup_after_playback = False
        self.started = time.time()
        # VAD 状态
        self._noise = 300.0
        self._speech = bytearray()
        self._pre = bytearray()
        self._in_speech = False
        self._speech_frames = 0
        self._silence = 0
        self._gen_task: asyncio.Task | None = None
        self._speaking_text = ""

    # ---------------- 收音 + 断句 ----------------
    def feed(self, pcm: bytes):
        samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16)
        if samples.size == 0:
            return
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        thr = max(450.0, self._noise * self.vad_k)
        voiced = rms > thr
        if not self._in_speech:
            self._pre += pcm
            if len(self._pre) > FRAME_BYTES * 25:          # 250ms 预录
                del self._pre[: len(self._pre) - FRAME_BYTES * 25]
            if voiced:
                self._speech_frames += 1
                if self._speech_frames >= 3:              # 连续 30ms 有声才算开口
                    self._in_speech = True
                    self._speech = bytearray(self._pre)
                    self._silence = 0
            else:
                self._speech_frames = 0
                self._noise = 0.95 * self._noise + 0.05 * rms
            return
        self._speech += pcm
        if voiced:
            self._silence = 0
            self._speech_frames += 1
            # 她正在说话、你说了 300ms 以上 => 打断她
            if self._speech_frames == 30 and self.out:
                self.interrupt()
        else:
            self._silence += 1
            if self._silence >= self.silence_frames:
                seg = bytes(self._speech)
                self._in_speech = False
                self._speech = bytearray()
                self._pre = bytearray()
                self._speech_frames = 0
                if len(seg) > FRAME_BYTES * 35:           # 去掉太短的杂音
                    self.utterances.put_nowait(seg)

    def interrupt(self):
        if self.out:
            log.info("被打断")
            self.out.clear()
        if self._gen_task and not self._gen_task.done():
            self._gen_task.cancel()

    # ---------------- 推流 ----------------
    async def player(self):
        calls = self.caller.calls
        from pytgcalls.types import Device
        silence = bytes(FRAME_BYTES)
        next_t = time.perf_counter()
        while not self.ended.is_set():
            now = time.perf_counter()
            if now < next_t:
                await asyncio.sleep(max(0.0, next_t - now))
                continue
            # 补发落后的帧（Windows 下 sleep 精度约 15ms）
            n = min(10, int((now - next_t) / 0.01) + 1)
            for _ in range(n):
                if self.out:
                    chunk = bytes(self.out[:FRAME_BYTES])
                    del self.out[:FRAME_BYTES]
                    if len(chunk) < FRAME_BYTES:
                        chunk += bytes(FRAME_BYTES - len(chunk))
                else:
                    chunk = silence
                    if self.hangup_after_playback:
                        self.ended.set()
                        break
                try:
                    await calls.send_frame(self.chat_id, Device.MICROPHONE, chunk)
                except Exception:  # noqa: BLE001
                    self.ended.set()
                    break
                next_t += 0.01

    # ---------------- 对话 ----------------
    async def converse(self):
        if self.she_called:
            opener = f"（电话刚接通，是你打给他的，原因：{self.reason}。你先开口。）"
        else:
            opener = "（他给你打来电话，你刚接起来。你先开口。）"
        self._gen_task = asyncio.create_task(self._respond(opener))
        await asyncio.wait({self._gen_task})
        while not self.ended.is_set():
            get = asyncio.create_task(self.utterances.get())
            end = asyncio.create_task(self.ended.wait())
            done, _ = await asyncio.wait({get, end}, return_when=asyncio.FIRST_COMPLETED)
            if end in done:
                get.cancel()
                break
            end.cancel()
            seg = get.result()
            # 把紧接着的几段合并
            while not self.utterances.empty():
                seg += self.utterances.get_nowait()
            try:
                wav = pcm_to_wav(_down3(seg), 16000)
                text = await self.caller.asr.transcribe(wav, "call.wav")
            except Exception as e:  # noqa: BLE001
                log.warning("通话识别失败：%s", e)
                continue
            text = text.strip()
            if not text or len(re.sub(r"[\W_]", "", text)) == 0:
                continue
            log.info("他（电话）：%s", text)
            self.transcript.append(("他", text))
            self.interrupt()            # 停掉还没说完的话，回应他新说的
            self._gen_task = asyncio.create_task(self._respond(None))

    async def _respond(self, system_hint: str | None):
        brain = self.caller.brain
        hist = brain.history(12)
        sys = brain.system_prompt(" ".join(t for _, t in self.transcript[-4:]) + self.reason, mode="call")
        msgs = [{"role": "system", "content": sys + "\n想结束通话时，说完道别的话后加上 [挂断]。"}]
        msgs += hist
        call_lines = "\n".join(f"{who}：{t}" for who, t in self.transcript[-30:])
        cue = system_hint or "（继续电话里的对话，回应他刚才说的话。）"
        append_user(msgs, f"（电话里到目前为止：\n{call_lines or '刚接通'}）\n{cue}")
        said = ""
        buf = ""
        tts_q: asyncio.Queue[str | None] = asyncio.Queue()
        tts_task = asyncio.create_task(self._tts_worker(tts_q))
        try:
            async for delta in brain.llm.stream(msgs, max_tokens=300):
                buf += delta
                while True:
                    m = SENT_END.search(buf)
                    if not m and len(buf) > 40 and "，" in buf[20:]:
                        idx = buf.index("，", 20)
                    elif m:
                        idx = m.end() - 1
                    else:
                        break
                    sent, buf = buf[: idx + 1], buf[idx + 1:]
                    said += sent
                    await tts_q.put(sent)
            if buf.strip():
                said += buf
                await tts_q.put(buf)
            await tts_q.put(None)
            await tts_task
        except asyncio.CancelledError:
            tts_task.cancel()
            if said:
                self.transcript.append((brain.persona.name, said.strip() + "……（被打断）"))
            raise
        except Exception as e:  # noqa: BLE001
            tts_task.cancel()
            log.exception("通话中生成回复失败：%s", e)
            if not any(w != "他" for w, _ in self.transcript):
                self.ended.set()          # 一开口就失败（比如大模型连不上），直接挂断
            return
        if HANGUP_TAG.search(said):
            self.hangup_after_playback = True
        said = HANGUP_TAG.sub("", said).strip()
        if said:
            log.info("她（电话）：%s", said)
            self.transcript.append((brain.persona.name, said))

    async def _tts_worker(self, q: asyncio.Queue):
        while True:
            sent = await q.get()
            if sent is None:
                return
            sent = HANGUP_TAG.sub("", sent).strip()
            if not sent:
                continue
            try:
                audio = await self.caller.tts.synth(sent)
                pcm = await to_pcm(audio, RATE, 1)
                self.out += pcm + bytes(FRAME_BYTES * 12)     # 句间留 120ms
            except Exception as e:  # noqa: BLE001
                log.warning("TTS 失败：%s", e)


def _down3(pcm: bytes) -> bytes:
    """48k -> 16k（取三点平均）。"""
    s = np.frombuffer(pcm[: len(pcm) // 6 * 6], dtype=np.int16).astype(np.int32)
    return (s.reshape(-1, 3).mean(axis=1)).astype(np.int16).tobytes()


class Caller:
    def __init__(self, cfg, brain: Brain, tts: TTS, asr: ASR, bot):
        self.cfg, self.brain, self.tts, self.asr, self.bot = cfg, brain, tts, asr, bot
        self.client = None
        self.calls = None
        self.session: CallSession | None = None
        self._lock = asyncio.Lock()
        self._bg: set[asyncio.Task] = set()

    async def start(self):
        from telethon import TelegramClient
        from pytgcalls import PyTgCalls, filters
        from pytgcalls.types import ChatUpdate, Direction, StreamFrames

        c = self.cfg.call
        sess = str(self.cfg.root / c.session_name)
        self.client = TelegramClient(sess, int(c.api_id), c.api_hash,
                                     proxy=telethon_proxy(self.cfg.proxy))
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("她的 TG 账号还没登录：先运行  python run.py login")
        me = await self.client.get_me()
        log.info("通话账号已登录：%s", me.first_name)
        await self.client.get_dialogs()          # 缓存会话，才能找到你的账号
        self.calls = PyTgCalls(self.client)

        @self.calls.on_update(filters.stream_frame(Direction.INCOMING))
        async def _frames(_, update: StreamFrames):
            s = self.session
            if s and update.chat_id == s.chat_id and update.frames:
                s.feed(update.frames[0].frame)

        @self.calls.on_update(filters.chat_update(ChatUpdate.Status.INCOMING_CALL))
        async def _incoming(_, update: ChatUpdate):
            if update.chat_id != self.bot.owner_id:
                await self._decline(update.chat_id)
                return
            t = asyncio.create_task(self._answer(update.chat_id))
            self._bg.add(t)
            t.add_done_callback(self._bg.discard)

        @self.calls.on_update(filters.chat_update(ChatUpdate.Status.LEFT_CALL))
        async def _left(_, update: ChatUpdate):
            if self.session and update.chat_id == self.session.chat_id:
                self.session.ended.set()

        await self.calls.start()

    async def _stream_params(self):
        from pytgcalls.types import ExternalMedia, MediaStream, RecordStream
        from pytgcalls.types.raw import AudioParameters
        ap = AudioParameters(bitrate=RATE, channels=1)
        return (MediaStream(ExternalMedia.AUDIO, ap, video_flags=MediaStream.Flags.IGNORE),
                RecordStream(True, ap))

    async def _decline(self, chat_id: int):
        try:
            await self.calls.leave_call(chat_id)      # 拒接/清理，避免残留来电状态
        except Exception:  # noqa: BLE001
            pass

    async def _answer(self, chat_id: int):
        if self._lock.locked():
            await self._decline(chat_id)
            return
        async with self._lock:
            await asyncio.sleep(random.uniform(2, 4))        # 响两声再接
            play, rec = await self._stream_params()
            try:
                await self.calls.play(chat_id, play)
                await self.calls.record(chat_id, rec)
            except Exception as e:  # noqa: BLE001
                log.warning("接听失败：%s", e)
                await self._decline(chat_id)
                return
            await self._run(chat_id, "他打电话给你", she_called=False)

    async def call(self, reason: str, forced: bool = False):
        """她给你打电话。forced=True 表示是你要求的（不受次数和免打扰限制）。"""
        from pytgcalls.exceptions import CallBusy, CallDeclined, TimedOutAnswer
        from pytgcalls.types import CallConfig

        chat_id = self.bot.owner_id
        if not chat_id or self._lock.locked():
            return
        c = self.cfg.call
        today = self.brain.now().strftime("%Y-%m-%d")
        if not forced:
            if int(self.brain.db.get("calls_" + today, 0)) >= int(c.max_calls_per_day):
                log.info("今天打过的电话够多了，不打了")
                return
            if in_quiet(self.brain.now(), self.cfg.proactive.quiet_hours):
                return
        async with self._lock:
            self.brain.db.set("calls_" + today, int(self.brain.db.get("calls_" + today, 0)) + 1)
            play, rec = await self._stream_params()
            log.info("正在给你打电话（%s）", reason)
            try:
                await self.calls.play(chat_id, play, config=CallConfig(timeout=int(c.ring_timeout)))
                await self.calls.record(chat_id, rec)
            except (TimedOutAnswer, CallDeclined, CallBusy) as e:
                what = {"TimedOutAnswer": "响了很久他没接", "CallDeclined": "他挂掉了",
                        "CallBusy": "他在忙线"}.get(type(e).__name__, "没打通")
                log.info("电话没接通：%s", what)
                self.brain.db.add_message("event", f"你给他打了语音电话，{what}")
                p = await self.brain.proactive(f"你刚给他打电话，{what}")
                await self.bot.deliver(p, kind="proactive")
                return
            except Exception as e:  # noqa: BLE001
                log.exception("打电话失败：%s", e)
                await self._decline(chat_id)
                return
            await self._run(chat_id, reason, she_called=True)

    async def _run(self, chat_id: int, reason: str, she_called: bool):
        s = CallSession(self, chat_id, reason, she_called)
        self.session = s
        player = asyncio.create_task(s.player())
        talk = asyncio.create_task(s.converse())
        try:
            await asyncio.wait_for(s.ended.wait(), timeout=float(self.cfg.call.max_call_minutes) * 60)
        except asyncio.TimeoutError:
            log.info("通话超过上限，结束")
        s.ended.set()
        for t in (player, talk):
            t.cancel()
        if s._gen_task:
            s._gen_task.cancel()
        try:
            await self.calls.leave_call(chat_id)
        except Exception:  # noqa: BLE001
            pass
        self.session = None
        await self._wrap_up(s)

    async def _wrap_up(self, s: CallSession):
        minutes = max(1, round((time.time() - s.started) / 60))
        db = self.brain.db
        for who, text in s.transcript:
            role = "user" if who == "他" else "assistant"
            db.add_message(role, f"[电话里] {text}", "call")
        summary = ""
        if s.transcript:
            lines = "\n".join(f"{w}：{t}" for w, t in s.transcript)
            try:
                summary = await self.brain.llm.chat([{"role": "user", "content":
                    f"用一两句话概括这通电话聊了什么、气氛如何：\n{lines}"}], helper=True, max_tokens=150)
            except Exception:  # noqa: BLE001
                summary = ""
        who = "你给他打" if s.she_called else "他给你打"
        db.add_message("event", f"刚才{who}的语音电话结束了，通话约 {minutes} 分钟。{summary}")
        db.set("unanswered", 0)
        db.set("last_user_ts", time.time())
        log.info("通话结束（%d 分钟）", minutes)
        try:
            await self.brain.memory.maybe_extract(force=True)
        except Exception:  # noqa: BLE001
            pass
