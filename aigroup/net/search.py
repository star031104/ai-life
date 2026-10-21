"""上网查一下：她不知道的东西（这是什么、谁、哪本书讲了什么、最近的新闻比分……）先搜一搜再说。

按顺序试，哪个先给出“对得上”的结果就用哪个：
- Tavily（config.yaml 的 net.tavily_key 填了才用；每月 1000 次免费；13.0 起填了 Key 就先用它，net.tavily_first: false 改回兜底）
- 百度、360、搜狗（都不用 Key；像浏览器一样先打开首页拿 cookie 再搜，国内服务器直连就能用）
只拿标题和摘要，最多几条；不点开结果页（点开交给 links.py，只读群友发的链接）。
结果里一个查询词都没出现（被风控时会给一堆不相干的）就当没搜到，换下一个。
"""
from __future__ import annotations

import html
import logging
import re
import time

import httpx

log = logging.getLogger("aigroup.search")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
TAG = re.compile(r"<[^>]+>")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG.sub("", s or ""))).strip()


ENGINES = (  # 名字, 首页, 搜索地址, 参数名
    ("百度", "https://www.baidu.com/", "https://www.baidu.com/s", "wd"),
    ("360", "https://www.so.com/", "https://www.so.com/s", "q"),
    ("搜狗", "https://www.sogou.com/", "https://www.sogou.com/web", "query"),
)
DROP = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)


def parse_generic(page: str, n: int = 5) -> list[dict]:
    """通用的结果页解析：每个 <h3> 是一条结果的标题，它后面到下一个 <h3> 之间的文字当摘要（百度、360、搜狗都是这样排的）。"""
    page = DROP.sub(" ", page or "")
    parts = re.split(r"<h3[\s>]", page)[1:]
    out = []
    for part in parts:
        m = re.match(r"[^>]*>(.*?)</h3>(.*)", part, re.S) or re.match(r"(.*?)</h3>(.*)", part, re.S)
        if not m:
            continue
        title = _clean(m.group(1))
        href = re.search(r'href="([^"]+)"', part)
        snippet = _clean(m.group(2))[:400]
        snippet = re.sub(r"^(\d{4}年\d{1,2}月\d{1,2}日|\d+\s*(天|小时|分钟)前)\s*[·\-]?\s*", "", snippet)
        snippet = re.split(r'["\']?[}\]]|\{"|变清晰', snippet)[0].strip()    # 页面里混进来的 JSON、图片栏
        if (len(title) < 4 or re.match(r"(相关搜索|大家还在搜|其他人还|为您推荐|猜你想搜)", title)
                or re.search(r"百度图片|百度视频|- 视频$|_图片$", title) or len(snippet) < 8):
            continue
        out.append({"title": title[:80], "url": html.unescape(href.group(1)) if href else "", "snippet": snippet[:220]})
        if len(out) >= n:
            break
    return out


def relevant(query: str, results: list[dict]) -> bool:
    """结果里至少出现一个查询词（2 个字以上的片段）才算搜到了。"""
    words = [w for w in re.split(r"[\s,，、]+", query) if len(w) >= 2]
    if not words:
        words = [query[i:i + 2] for i in range(0, max(1, len(query) - 1), 2)]
    text = " ".join(r["title"] + r["snippet"] for r in results)
    return any(w in text for w in words)


def pub_date(v) -> str:
    """Tavily 的 published_date（RFC 2822 或 ISO）→ YYYY-MM-DD；认不出返回空。"""
    s = str(v or "").strip()
    if not s:
        return ""
    from datetime import datetime
    from email.utils import parsedate_to_datetime
    for f in (lambda: parsedate_to_datetime(s), lambda: datetime.fromisoformat(s.replace("Z", "+00:00"))):
        try:
            return f().strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            continue
    m = re.search(r"(20\d\d)-(\d\d)-(\d\d)", s)
    return m.group(0) if m else ""


def text_dates(text: str, today) -> list[str]:
    """从标题 / 摘要里认出写着的日期（2026年10月19日、10月19日、2026-10-19、10/19），
    没写年份的按今天往前推（12 月的新闻在 1 月看到算去年）。返回 YYYY-MM-DD 列表。"""
    out = []
    for m in re.finditer(r"(?:(20\d\d)\s*[年\-/.]\s*)?(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*[日号]?", text or ""):
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        if not y and not re.search(r"[月]", m.group(0)):       # 光是“3.5”“10/19”这种不带“月”的，容易是比分、版本号
            continue
        yy = int(y) if y else today.year - (1 if (mo, d) > (today.month, today.day) else 0)
        out.append(f"{yy:04d}-{mo:02d}-{d:02d}")
    return out


class WebSearch:
    def __init__(self, cfg):
        c = dict(cfg.get("net") or {})
        self.enabled = bool(c.get("search", True))
        self.tavily_key = str(c.get("tavily_key") or "").strip()
        self.tavily_first = bool(c.get("tavily_first", True))      # 13.0 配了 Key 就先用 Tavily（结果干净，服务器上不会被风控）
        self.proxy = cfg.get("proxy") or None
        self.timeout = float(c.get("search_timeout", 12))
        self._cache: dict[str, list[dict]] = {}
        self._clients: dict[str, httpx.AsyncClient] = {}
        self.kv = None                    # 19.0 GroupChat 挂上数据库：记 Tavily 这个月用了几次、免费额度用完没
        self.last_engine = ""

    async def _client(self, name: str, home: str) -> httpx.AsyncClient:
        c = self._clients.get(name)
        if c is None or c.is_closed:
            c = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, proxy=None,
                                  headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
                                           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
            try:
                await c.get(home)                         # 先打开首页拿 cookie，像个正常浏览器
            except Exception:  # noqa: BLE001
                pass
            self._clients[name] = c
        return c

    async def _engine(self, name: str, home: str, url: str, key: str, q: str, n: int) -> list[dict]:
        c = await self._client(name, home)
        r = await c.get(url, params={key: q})
        r.raise_for_status()
        if re.search(r"安全验证|验证码|captcha", r.text[:20000], re.I) and "<h3" not in r.text:
            self._clients.pop(name, None)
            raise RuntimeError("要验证")
        return parse_generic(r.text, n)

    async def search(self, query: str, n: int = 5, fresh_days: int = 0) -> list[dict]:
        """fresh_days > 0：只要最近这几天的新闻（13.2 分享用）。Tavily 走新闻模式、带发布日期；
        别的搜索引擎给不出日期，结果照旧返回，由调用方看日期再决定。"""
        q = re.sub(r"\s+", " ", query or "").strip()[:60]
        if not self.enabled or len(q) < 2:
            return []
        ck = f"{q}|{fresh_days}" if fresh_days else q
        if ck in self._cache:
            return self._cache[ck]
        res: list[dict] = []
        tries = [(e[0], (lambda e=e: self._engine(*e, q, n))) for e in ENGINES]
        if self.tavily_key and not self.tavily_out():
            tav = ("Tavily", lambda: self._tavily(q, n, fresh_days))
            tries = [tav] + tries if (self.tavily_first or fresh_days) else tries + [tav]
        for name, fn in tries:
            try:
                got = await fn()
            except Exception as e:  # noqa: BLE001
                log.info("%s搜索失败（%s）：%s", name, q, e)
                continue
            if got and relevant(q, got):
                res = got
                self.last_engine = name
                log.info("搜了「%s」（%s）：%d 条", q, name, len(res))
                break
            log.info("%s搜「%s」没搜到对得上的", name, q)
        self._cache[ck] = res
        if len(self._cache) > 200:
            self._cache.pop(next(iter(self._cache)))
        return res

    # ---------------- 19.0 Tavily 免费额度：用完了这个月就只用免费的引擎 ----------------
    def _month(self) -> str:
        return time.strftime("%Y-%m")

    def tavily_out(self) -> bool:
        return bool(self.kv is not None and self.kv.get("tavily_out") == self._month())

    def tavily_used(self) -> int:
        return int((self.kv.get(f"tavily_n_{self._month()}") if self.kv is not None else 0) or 0)

    async def _tavily(self, q: str, n: int, fresh_days: int = 0, snippet: int = 220, site: str = "") -> list[dict]:
        body = {"query": q, "max_results": n, "search_depth": "basic"}
        if site:
            body["include_domains"] = [site]
        if fresh_days:
            body.update(topic="news", days=int(fresh_days))
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {self.tavily_key}"},
                             json=body)
            if r.status_code in (432, 433) or (r.status_code in (401, 403) and re.search(
                    r"limit|quota|credit|exceed|plan", r.text or "", re.I)):
                if self.kv is not None:
                    self.kv.set("tavily_out", self._month())
                log.warning("Tavily 这个月的免费额度用完了（%s），这个月改用免费的搜索", r.status_code)
                raise RuntimeError("Tavily 额度用完")
            r.raise_for_status()
            d = r.json()
        if self.kv is not None:
            k = f"tavily_n_{self._month()}"
            self.kv.set(k, int(self.kv.get(k) or 0) + 1)
        return [{"title": _clean(x.get("title", ""))[:80], "url": x.get("url", ""),
                 "snippet": _clean(x.get("content", ""))[:snippet], "date": pub_date(x.get("published_date"))}
                for x in (d.get("results") or [])[:n]]

    async def extract(self, url: str) -> dict:
        """19.3 让 Tavily 的服务器去读一个网页（国内打不开的、被拦的）：{title, text}（Markdown）。5 个网址算 1 个额度，这里一次一个。"""
        if not self.tavily_key or self.tavily_out():
            return {}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://api.tavily.com/extract", headers={"Authorization": f"Bearer {self.tavily_key}"},
                             json={"urls": [url], "format": "markdown", "extract_depth": "basic"})
            if r.status_code in (432, 433):
                if self.kv is not None:
                    self.kv.set("tavily_out", self._month())
                raise RuntimeError("Tavily 额度用完")
            r.raise_for_status()
            d = r.json()
        if self.kv is not None:
            k = f"tavily_n_{self._month()}"
            self.kv.set(k, int(self.kv.get(k) or 0) + 1)
        res = (d.get("results") or [{}])[0] if d.get("results") else {}
        return {"title": str(res.get("title") or ""), "text": str(res.get("raw_content") or "")}

    async def agent_search(self, query: str, n: int = 6, days: int = 0, site: str = "") -> tuple[list[dict], str]:
        """给“查资料”用：Tavily 优先（带日期、摘要长一点），免费额度用完 / 出错就换百度、360（没有日期）。
        返回（结果, 用的哪个引擎）。"""
        q = re.sub(r"\s+", " ", query or "").strip()[:80]
        site = re.sub(r"^https?://|/.*$", "", (site or "").strip().lower())[:60]
        if not self.enabled or len(q) < 2:
            return [], ""
        ck = f"agent|{q}|{days}|{site}"
        hit = self._cache.get(ck)
        if hit:
            return hit, "缓存"
        tries = []
        if self.tavily_key and not self.tavily_out():
            tries.append(("Tavily", lambda: self._tavily(q, n, int(days or 0), snippet=320, site=site)))
        qq = f"site:{site} {q}" if site else q                   # 19.3 只搜某个网站（百度 / 360 认 site:）
        tries += [(e[0], (lambda e=e: self._engine(*e, qq, n))) for e in ENGINES]
        for name, fn in tries:
            try:
                got = await fn()
            except Exception as e:  # noqa: BLE001
                log.info("%s搜索失败（%s）：%s", name, q, e)
                continue
            if got:
                self._cache[ck] = got
                if len(self._cache) > 200:
                    self._cache.pop(next(iter(self._cache)))
                log.info("查资料：搜了「%s」（%s，%d 条）", q, name, len(got))
                return got, name
        return [], ""

    @staticmethod
    def block(query: str, results: list[dict], k: int = 4) -> str:
        if not results:
            return ""
        lines = [f"- {r['title']}：{r['snippet']}" for r in results[:k] if r.get("snippet") or r.get("title")]
        return (f"# 你刚在手机上搜了一下「{query}」，看到这些（自然地用，像你自己知道 / 刚看到的；别说“根据搜索结果”，"
                f"别贴链接，别像百科一样整段念；搜到的对不上就别硬说）\n" + "\n".join(lines))
