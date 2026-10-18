"""一起追番（Bangumi 番组计划）和一起听歌（网易云音乐）。都是公开接口，不用登录、不用 key。

追番：加一部番时去 Bangumi 查到它的条目，拿到总集数、每集的播出日期、标题和简介。
      更新那天她知道是第几集、讲了什么，你看完以后能和你聊剧情；你提到这部番时她也知道最近几集的内容。
听歌：电脑上正在放的歌，去网易云查到歌名、歌手、专辑，记进“你听过的歌”；
      她推荐歌时（[推歌:歌名|歌手]），查到网易云的链接一起发给你。
"""
from __future__ import annotations

import logging
import time
from datetime import date

import httpx

log = logging.getLogger("ailife.media")

BGM = "https://api.bgm.tv"
BGM_HEADERS = {"User-Agent": "ailife/1.0 (personal companion bot)"}
NE_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
              "Referer": "https://music.163.com/"}


def clean_desc(s: str) -> str:
    """去掉 Bangumi 简介后面的制作人员表。"""
    s = s.replace("\r\n", "\n").strip()
    for cut in ("\n\n脚本", "\n脚本", "脚本：", "\n\nスタッフ", "\n\n【"):
        i = s.find(cut)
        if i > 0:
            s = s[:i]
    return " ".join(s.split())


class Media:
    def __init__(self, cfg, db):
        self.cfg, self.db = cfg, db
        self.proxy = cfg.proxy or None
        self._ep_cache: dict[int, tuple[float, list]] = {}

    async def _req(self, method: str, url: str, headers: dict, **kw):
        last = None
        for proxy in (None, self.proxy) if self.proxy else (None,):
            try:
                async with httpx.AsyncClient(timeout=15, proxy=proxy, headers=headers, follow_redirects=True) as c:
                    r = await c.request(method, url, **kw)
                    r.raise_for_status()
                    return r.json()
            except Exception as e:  # noqa: BLE001
                last = e
        raise RuntimeError(f"{url} 失败：{last}")

    # ================= Bangumi =================
    async def bgm_search(self, name: str) -> dict | None:
        data = await self._req("POST", f"{BGM}/v0/search/subjects?limit=5", BGM_HEADERS,
                               json={"keyword": name, "filter": {"type": [2]}})
        items = data.get("data") or []
        if not items:
            return None
        # 名字最接近的、最近播出的优先
        def score(x):
            n = (x.get("name_cn") or x.get("name") or "")
            exact = 2 if n == name else 1 if name in n or n in name else 0
            return (exact, x.get("date") or "")
        x = max(items, key=score)
        return {"id": x["id"], "name_cn": x.get("name_cn") or x.get("name"), "date": x.get("date"),
                "eps": x.get("eps") or x.get("total_episodes")}

    async def bgm_episodes(self, subject_id: int) -> list[dict]:
        hit = self._ep_cache.get(subject_id)
        if hit and time.time() - hit[0] < 6 * 3600:
            return hit[1]
        eps, offset = [], 0
        while True:
            d = await self._req("GET", f"{BGM}/v0/episodes", BGM_HEADERS,
                                params={"subject_id": subject_id, "type": 0, "limit": 100, "offset": offset})
            eps += [{"ep": int(e.get("ep") or e.get("sort") or 0), "airdate": e.get("airdate") or "",
                     "title": e.get("name_cn") or e.get("name") or "", "desc": clean_desc(e.get("desc") or "")}
                    for e in d.get("data") or []]
            offset += 100
            if offset >= int(d.get("total") or 0):
                break
        self._ep_cache[subject_id] = (time.time(), eps)
        return eps

    async def enrich_show(self, row) -> dict | None:
        """给一部番补上 Bangumi 信息，并按播出日期校正“看到第几集”。"""
        try:
            sid, name_cn, total = row["subject_id"], row["name_cn"], row["total_eps"]
            if not sid:
                info = await self.bgm_search(row["name"])
                if not info:
                    return None
                sid, name_cn, total = info["id"], info.get("name_cn"), info.get("eps")
            eps = await self.bgm_episodes(sid)
            aired = [e for e in eps if e["airdate"] and e["airdate"] <= date.today().isoformat()]
            if sid != row["subject_id"] or (eps and not row["total_eps"]):
                self.db.set_show_meta(row["id"], sid, total or len(eps) or None, name_cn)
            if aired:
                latest = max(e["ep"] for e in aired)
                if latest > (row["episode"] or 0):
                    self.db.set_show_episode(row["id"], latest)
            return {"subject_id": sid, "eps": eps}
        except Exception as e:  # noqa: BLE001
            log.info("Bangumi 查询失败（%s）：%s", row["name"], str(e)[:120])
            return None

    async def episode(self, row, ep: int) -> dict | None:
        if not row["subject_id"]:
            return None
        try:
            eps = await self.bgm_episodes(row["subject_id"])
        except Exception:  # noqa: BLE001
            return None
        return next((e for e in eps if e["ep"] == ep), None)

    def recent_eps_cached(self, row, n: int = 2) -> list[dict]:
        """聊天提示词用：只读缓存，不发请求（缓存由每天整理 / 更新提醒时刷新）。"""
        hit = self._ep_cache.get(row["subject_id"] or -1)
        if not hit:
            return []
        today = date.today().isoformat()
        aired = [e for e in hit[1] if e["airdate"] and e["airdate"] <= today]
        return sorted(aired, key=lambda e: e["ep"])[-n:]

    # ================= 网易云 =================
    async def ne_search(self, title: str, artist: str = "") -> dict | None:
        q = f"{title} {artist}".strip()
        for url, method, kw in (
                ("https://music.163.com/api/cloudsearch/pc", "GET", {"params": {"s": q, "type": 1, "limit": 15}}),
                ("https://music.163.com/api/search/get", "GET", {"params": {"s": q, "type": 1, "limit": 15}})):
            try:
                d = await self._req(method, url, NE_HEADERS, **kw)
                songs = (d.get("result") or {}).get("songs") or []
            except Exception:  # noqa: BLE001
                continue
            if not songs:
                continue
            def norm(s):
                arts = s.get("ar") or s.get("artists") or []
                al = s.get("al") or s.get("album") or {}
                return {"id": s["id"], "title": s.get("name", ""), "artist": "/".join(a.get("name", "") for a in arts),
                        "album": al.get("name", ""), "url": f"https://music.163.com/song?id={s['id']}",
                        "_pop": float(s.get("pop") or 0), "_ok": (s.get("privilege") or {}).get("st", 0) >= 0}
            cands = [norm(s) for s in songs]

            def score(c):        # 歌手对得上最重要（避免翻唱版），其次歌名完全一致
                a = 3 if artist and artist in c["artist"] else 0
                t = 2 if c["title"] == title else 1 if title in c["title"] else 0
                bad = 0.5 if any(k in c["title"] for k in ("版", "Cover", "cover", "翻唱", "DJ", "伴奏")) else 0
                return a + t - bad + (0.5 if c["_ok"] else 0) + c["_pop"] / 400
            best = max(cands, key=score)
            return {k: v for k, v in best.items() if not k.startswith("_")}
        return None

    async def on_song(self, title: str, artist: str) -> dict | None:
        """电脑上换了一首歌：记下来，查到专辑等信息。"""
        info = None
        try:
            info = await self.ne_search(title, artist)
        except Exception:  # noqa: BLE001
            pass
        self.db.log_music(title, artist or (info or {}).get("artist", ""), (info or {}).get("album", ""),
                          (info or {}).get("id"), "pc")
        return info

    async def recommend_link(self, title: str, artist: str = "") -> dict | None:
        info = await self.ne_search(title, artist)
        self.db.log_music(title, artist or (info or {}).get("artist", ""), (info or {}).get("album", ""),
                          (info or {}).get("id"), "her")
        return info
