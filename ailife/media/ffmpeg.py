"""ffmpeg 小工具：贴纸缩略图转 PNG、视频截帧（看图用）。语音功能已经去掉，这里不再转音频。"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

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
