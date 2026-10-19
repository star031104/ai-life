"""上网查一下（从 ai-life-group 搬过来的）：他问到她不知道的东西（这是什么、谁、哪本书讲了什么、最近的新闻比分……）先搜一搜再说。

按顺序试，哪个先给出“对得上”的结果就用哪个：
- 百度、360、搜狗（都不用 Key；像浏览器一样先打开首页拿 cookie 再搜，国内服务器直连就能用）
- Tavily（config.yaml 的 net.tavily_key 填了才用；每月 1000 次免费，前面几个都不行时兜底）
只拿标题和摘要，最多几条；不点开结果页（点开交给 links.py，只读群友发的链接）。
结果里一个查询词都没出现（被风控时会给一堆不相干的）就当没搜到，换下一个。
"""
from __future__ import annotations

import html
import logging
import re

import httpx

log = logging.getLogger("ailife.search")

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


class WebSearch:
    def __init__(self, cfg):
        c = dict(cfg.get("net") or {})
        self.enabled = bool(c.get("search", True))
        self.tavily_key = str(c.get("tavily_key") or "").strip()
        self.proxy = cfg.get("proxy") or None
        self.timeout = float(c.get("search_timeout", 12))
        self._cache: dict[str, list[dict]] = {}
        self._clients: dict[str, httpx.AsyncClient] = {}

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

    async def search(self, query: str, n: int = 5) -> list[dict]:
        q = re.sub(r"\s+", " ", query or "").strip()[:60]
        if not self.enabled or len(q) < 2:
            return []
        if q in self._cache:
            return self._cache[q]
        res: list[dict] = []
        tries = [(e[0], (lambda e=e: self._engine(*e, q, n))) for e in ENGINES] + \
            ([("Tavily", lambda: self._tavily(q, n))] if self.tavily_key else [])
        for name, fn in tries:
            try:
                got = await fn()
            except Exception as e:  # noqa: BLE001
                log.info("%s搜索失败（%s）：%s", name, q, e)
                continue
            if got and relevant(q, got):
                res = got
                log.info("搜了「%s」（%s）：%d 条", q, name, len(res))
                break
            log.info("%s搜「%s」没搜到对得上的", name, q)
        self._cache[q] = res
        if len(self._cache) > 200:
            self._cache.pop(next(iter(self._cache)))
        return res

    async def _tavily(self, q: str, n: int) -> list[dict]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {self.tavily_key}"},
                             json={"query": q, "max_results": n, "search_depth": "basic"})
            r.raise_for_status()
            d = r.json()
        return [{"title": _clean(x.get("title", ""))[:80], "url": x.get("url", ""),
                 "snippet": _clean(x.get("content", ""))[:220]} for x in (d.get("results") or [])[:n]]

    @staticmethod
    def block(query: str, results: list[dict], k: int = 4) -> str:
        if not results:
            return ""
        lines = [f"- {r['title']}：{r['snippet']}" for r in results[:k] if r.get("snippet") or r.get("title")]
        return (f"[你刚在手机上搜了一下「{query}」，看到这些（自然地用，像你自己知道 / 刚看到的；别说“根据搜索结果”，"
                f"别贴链接，别像百科一样整段念；搜到的对不上就别硬说）：\n" + "\n".join(lines) + "]")


ASK_RE = re.compile(r"(是什么|是啥|啥是|什么是|是谁|谁是|什么意思|啥意思|讲了什么|讲的什么|讲什么|讲啥|说的是什么|"
                    r"好看吗|好玩吗|值得|怎么样|咋样|多少钱|几号|哪天|什么时候|啥时候|上线|发售|更新了|最新|新闻|比分|"
                    r"赛程|结果|攻略|推荐|介绍一下|科普|总结一下|查一下|查查|搜一下|搜搜|百度一下|知道.{1,12}吗|听说过)")

DECIDE_PROMPT = """你帮一个正在和恋人聊天的女生判断：他下面这几句里，有没有她需要先上网查一下才能接得住的事
（作品、游戏、比赛、新闻、人物、地点、概念、书讲了什么、某样东西多少钱 / 什么时候出……）。
问她自己的事、问你们俩的事、撒娇闲聊、表情都不用查。
只输出 JSON：{"need": true 或 false, "query": "要搜的关键词（6~20 个字，像在搜索框里打的）", "topic": "话题（2~10 个字）"}

他说的：
"""
