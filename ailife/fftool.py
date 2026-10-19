"""ffmpeg 小工具：贴纸缩略图转 PNG、视频截帧（看图用）。语音功能已经去掉，这里不再转音频。"""
from __future__ import annotations

import asyncio
import shutil

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
