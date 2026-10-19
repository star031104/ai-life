"""把小屋网页安全地带到手机上：Telegram Mini App。

Telegram 的 Mini App 必须是 https 地址。两种方式：
1. 自己有固定的 https 地址（自己的域名 / frp / Cloudflare 命名隧道）→ 填 miniapp.url，指向本机的 public_port。
2. 什么都没有 → 程序自动下载 cloudflared，开一个免费的临时隧道（trycloudflare.com，每次启动地址会变，
   程序会自动把新地址更新到 bot 的菜单按钮上）。

手机上打开的是“公开入口”（public_port），每个请求都要通过 Telegram 签名校验，只有你本人能看。
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import sys
from pathlib import Path

import httpx

log = logging.getLogger("ailife.tunnel")

CF_URL = {
    "win32": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
    "linux": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
    "darwin": "",
}
URL_RE = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")


class Tunnel:
    def __init__(self, cfg, port: int):
        c = dict(cfg.get("miniapp") or {})
        self.enabled = bool(c.get("enabled", True))
        self.fixed_url = (c.get("url") or "").rstrip("/")
        self.port = port
        self.proxy = cfg.proxy or None
        self.bin_dir = cfg.root / "data" / "bin"
        self.proc: asyncio.subprocess.Process | None = None
        self.url: str | None = None
        self._reader: asyncio.Task | None = None

    def _exe(self) -> Path | None:
        name = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"
        local = self.bin_dir / name
        if local.exists():
            return local
        found = shutil.which("cloudflared")
        return Path(found) if found else None

    async def _download(self) -> Path:
        url = CF_URL.get(sys.platform, "")
        import platform
        if sys.platform == "linux" and platform.machine().lower() in ("aarch64", "arm64", "armv8l"):
            url = url.replace("linux-amd64", "linux-arm64")          # ARM 的 Linux（树莓派之类）
        if not url:
            raise RuntimeError("这个系统请自己装 cloudflared")
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        dst = self.bin_dir / ("cloudflared.exe" if sys.platform == "win32" else "cloudflared")
        tmp = dst.with_suffix(".part")
        last = None
        for proxy in ([self.proxy, None] if self.proxy else [None]):
            try:
                log.info("正在下载 cloudflared（约 60MB，只需一次）…")
                async with httpx.AsyncClient(proxy=proxy, timeout=60, follow_redirects=True) as cli:
                    async with cli.stream("GET", url) as r:
                        r.raise_for_status()
                        with open(tmp, "wb") as f:
                            async for ch in r.aiter_bytes(1 << 20):
                                f.write(ch)
                tmp.replace(dst)
                if sys.platform != "win32":
                    dst.chmod(0o755)
                return dst
            except Exception as e:  # noqa: BLE001
                last = e
        raise RuntimeError(f"cloudflared 下载失败：{last}")

    async def start(self) -> str | None:
        if not self.enabled:
            return None
        if self.fixed_url:
            self.url = self.fixed_url
            return self.url
        exe = self._exe() or await self._download()
        args = [str(exe), "tunnel", "--no-autoupdate", "--protocol", "http2",
                "--url", f"http://127.0.0.1:{self.port}"]
        self.proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()

        async def read():
            async for raw in self.proc.stdout:
                line = raw.decode(errors="ignore")
                m = URL_RE.search(line)
                if m and not fut.done():
                    fut.set_result(m.group(0))
            if not fut.done():                  # 进程退出了还没拿到地址
                fut.set_exception(RuntimeError("cloudflared 提前退出了（可能是临时隧道被限流，稍后重启再试）"))
        self._reader = asyncio.create_task(read())
        try:
            self.url = await asyncio.wait_for(fut, 90)
        except asyncio.TimeoutError:
            await self.stop()
            raise RuntimeError("cloudflared 90 秒内没拿到地址（网络不通？可以在 miniapp.url 填自己的地址）")
        await asyncio.sleep(3)          # 等隧道真正连上
        return self.url

    async def stop(self):
        if self._reader:
            self._reader.cancel()
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), 5)
            except Exception:  # noqa: BLE001
                pass
