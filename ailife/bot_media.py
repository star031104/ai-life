"""他发来的各种东西：动图、视频、圆形视频、文件、位置、骰子……以前这些她会“看不见”而完全不回。

- 动图 / 视频 / 圆形视频：用 ffmpeg 截几帧给会看图的模型看（声音听不了）。
- 图片文件（不压缩发送的原图）当图片看；小的文本文件读开头；别的文件告诉她文件名和大小。
- 位置：反查到城市，之后天气就按这里查。
- 都失败时也会告诉她“发了个什么、你看不了”，她至少会回一句。
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile

from telegram import Update

from .fftool import FFMPEG

log = logging.getLogger("ailife.bot")

MAX_DOWNLOAD = 20 * 1024 * 1024          # Bot API 只能下载 20MB 以内的文件
TEXT_EXT = (".txt", ".md", ".csv", ".log", ".json", ".py", ".yaml", ".yml", ".ini", ".srt")


async def _run(args: list[str], timeout: float, want_stderr: bool = False) -> bytes:
    """跑一次 ffmpeg：不占用控制台输入；超时就杀掉（不然临时文件删不掉、进程一直挂着）。"""
    p = await asyncio.create_subprocess_exec(
        FFMPEG, "-hide_banner", "-nostdin", *args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE if want_stderr else asyncio.subprocess.DEVNULL)
    try:
        _, err = await asyncio.wait_for(p.communicate(), timeout)
        return err or b""
    except asyncio.TimeoutError:
        try:
            p.kill()
        except ProcessLookupError:
            pass
        await p.wait()
        return b""


async def video_frames(data: bytes, n: int = 2, suffix: str = ".mp4") -> list[bytes]:
    """从视频 / 动图里平均截 n 帧（jpg）。"""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in" + suffix)
        with open(src, "wb") as f:
            f.write(data)
        dur = 0.0
        try:
            err = await _run(["-i", src], 15, want_stderr=True)
            import re
            m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err.decode(errors="ignore"))
            if m:
                dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        except Exception:  # noqa: BLE001
            pass
        times = [dur * (i + 1) / (n + 1) for i in range(n)] if dur > 0.5 else [0.0]
        out = []
        for i, t in enumerate(times):
            dst = os.path.join(td, f"f{i}.jpg")
            await _run(["-loglevel", "error", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1",
                        "-vf", "scale='min(640,iw)':-2", "-q:v", "4", "-y", dst], 20)
            if os.path.exists(dst) and os.path.getsize(dst) > 0:
                with open(dst, "rb") as f:
                    out.append(f.read())
        return out


def human_size(n: int | None) -> str:
    if not n:
        return "大小未知"
    return f"{n / 1024 / 1024:.1f}MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)}KB"


class MediaMixin:
    def _later(self, update: Update, kind: str, placeholder: str, work):
        """先记一条“正在看”，再在后台慢慢截帧 / 识别（期间照常收别的消息），看完改成真正的内容再回。"""
        mid = self._user_in(update, placeholder, kind)
        try:
            prefix = self._quote_ctx(update.message)
        except Exception:  # noqa: BLE001
            prefix = ""

        async def job():
            try:
                content = await work()
            except Exception as e:  # noqa: BLE001
                log.warning("处理他发的%s失败：%s", kind, e)
                content = placeholder.replace("正在看", "你没看清").replace("正在听", "你没听清")
            self.db.update_message(mid, prefix + content)
            await self.schedule_reply()
        self.spawn(job())
        return mid

    async def _download(self, ctx, file_id: str, size: int | None) -> bytes | None:
        if size and size > MAX_DOWNLOAD:
            return None
        try:
            f = await ctx.bot.get_file(file_id)
            return bytes(await f.download_as_bytearray())
        except Exception as e:  # noqa: BLE001
            log.warning("下载他发的文件失败：%s", e)
            return None

    async def _look(self, frames: list[bytes], hint: str) -> str:
        """让会看图的模型描述几帧画面。"""
        if not frames or not self.brain.llm.vision:
            return ""
        descs = []
        for fr in frames[:3]:
            try:
                d = await self.brain.llm.describe_image(base64.b64encode(fr).decode(), hint)
                if d:
                    descs.append(d.strip())
            except Exception as e:  # noqa: BLE001
                log.info("看帧失败：%s", e)
                break
        if len(descs) > 1:
            return "；然后".join(descs)
        return descs[0] if descs else ""

    async def on_animation(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        a = update.message.animation
        cap = update.message.caption or ""

        async def work():
            desc = ""
            data = await self._download(ctx, a.file_id, a.file_size)
            if data:
                try:
                    desc = await self._look(await video_frames(data, 1, ".mp4"), "这是一个表情动图（GIF），说说画面和它表达的情绪")
                except Exception as e:  # noqa: BLE001
                    log.info("动图截帧失败：%s", e)
            return (f"[发了一个动图：{desc}]" if desc else "[发了一个动图，你没看清是什么，只能从上下文猜他的情绪]") + \
                (f" {cap}" if cap else "")
        self._later(update, "animation", "[发了一个动图，你正在看]" + (f" {cap}" if cap else ""), work)

    async def on_video(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        msg = update.message
        v = msg.video_note or msg.video
        round_ = msg.video_note is not None
        cap = msg.caption or ""
        what = "一段圆形视频（他自己对着镜头拍的）" if round_ else "一段视频"
        dur = getattr(v, "duration", 0) or 0

        async def work():
            data = await self._download(ctx, v.file_id, v.file_size)
            if not data:
                return f"[发了{what}（{dur} 秒，{human_size(v.file_size)}），太大了你打不开]" + (f" {cap}" if cap else "")
            frames = []
            try:
                frames = await video_frames(data, 1 if round_ else 2, ".mp4")
            except Exception as e:  # noqa: BLE001
                log.info("视频截帧失败：%s", e)
            desc = await self._look(frames, "这是他发来的视频画面，描述画面里的人、事和场景" if not round_
                                   else "这是他自拍的圆形视频，描述他的样子、表情和周围环境")
            content = f"[发了{what}（{dur} 秒）" + (f"，画面：{desc}" if desc else "，你没看清内容") + "（声音你听不了）]"
            if round_:
                self.brain.together.observe(content)
            return content + (f" {cap}" if cap else "")
        self._later(update, "video", f"[发了{what}（{dur} 秒），你正在看]" + (f" {cap}" if cap else ""), work)

    async def on_document(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        d = update.message.document
        cap = update.message.caption or ""
        name = d.file_name or "文件"
        mime = (d.mime_type or "").lower()

        async def work():
            content = ""
            if mime.startswith("image/") and (d.file_size or 0) <= MAX_DOWNLOAD:
                data = await self._download(ctx, d.file_id, d.file_size)
                if data and self.brain.llm.vision:
                    try:
                        desc = await self.brain.llm.describe_image(base64.b64encode(data).decode(), cap)
                        content = f"[发了一张原图，图里是：{desc}]"
                    except Exception as e:  # noqa: BLE001
                        log.info("看原图失败：%s", e)
            elif (mime.startswith("text/") or name.lower().endswith(TEXT_EXT)) and (d.file_size or 0) <= 200 * 1024:
                data = await self._download(ctx, d.file_id, d.file_size)
                if data:
                    text = data.decode("utf-8", errors="ignore").strip()
                    content = f"[发了一个文件「{name}」，开头是：{text[:1200]}" + ("……]" if len(text) > 1200 else "]")
            elif mime.startswith(("video/", "audio/")):
                content = (f"[发了一个{'视频' if mime.startswith('video/') else '音频'}文件「{name}」"
                           f"（{human_size(d.file_size)}），你没法打开看]")
            if not content:
                content = f"[发了一个文件「{name}」（{human_size(d.file_size)}），你打不开，只能看到文件名]"
            return content + (f" {cap}" if cap else "")
        self._later(update, "file", f"[发了一个文件「{name}」，你正在看]" + (f" {cap}" if cap else ""), work)

    async def on_location(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        msg = update.message
        loc = msg.location or (msg.venue.location if msg.venue else None)
        venue = f"「{msg.venue.title}」" if msg.venue else ""
        live = " （实时共享位置）" if loc and getattr(loc, "live_period", None) else ""

        async def work():
            place = ""
            if loc:
                try:
                    place = await self.brain.weather.set_location(loc.latitude, loc.longitude)
                    self.brain.weather_info = None
                except Exception as e:  # noqa: BLE001
                    log.info("处理位置失败：%s", e)
            return f"[发来了他的位置{venue}：{place or '没认出是哪里'}{live}]"
        self._later(update, "location", f"[发来了他的位置{venue}{live}]", work)

    async def on_dice(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        dc = update.message.dice
        names = {"🎲": "骰子", "🎯": "飞镖", "🏀": "投篮", "⚽": "射门", "🎳": "保龄球", "🎰": "老虎机"}
        self._user_in(update, f"[扔了一个{names.get(dc.emoji, dc.emoji)}，结果是 {dc.value}"
                              f"{'（满分）' if (dc.emoji == '🎲' and dc.value == 6) else ''}]", "dice")
        await self.schedule_reply()

    async def on_other(self, update: Update, ctx):
        """上面都没接住的（联系人、投票、游戏……）：至少让她知道他发了东西。"""
        if not self._is_owner(update) or not update.message:
            return
        m = update.message
        kind = ("联系人名片" if m.contact else "投票" if m.poll else "小游戏" if m.game else
                "故事" if getattr(m, "story", None) else "一条消息")
        self._user_in(update, f"[发了一个{kind}，你这边显示不出来]", "other")
        await self.schedule_reply()
