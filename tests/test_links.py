"""读他分享的链接（不联网：用假的网络）。"""
import asyncio



def test_find_links_and_public():
    from ailife.media.links import find_links, is_public, site_name
    assert find_links("看看 https://b23.tv/xyz，还有 BV1GJ411x7h7！") == [
        "https://b23.tv/xyz", "https://www.bilibili.com/video/BV1GJ411x7h7"]
    assert find_links("没有链接") == []
    assert not asyncio.run(is_public("http://127.0.0.1:8765/api/state"))
    assert not asyncio.run(is_public("http://192.168.0.1/"))
    assert not asyncio.run(is_public("file:///etc/passwd"))
    assert site_name("mp.weixin.qq.com") == "公众号" and site_name("www.bilibili.com") == "B站"


def test_page_parse_and_describe(env, monkeypatch):
    cfg, brain, bot, fake = env
    from ailife.media.links import LinkReader

    html = ("<html><head><title>T</title><meta property='og:title' content='好吃的番茄炒蛋怎么做'>"
            "<meta name='description' content='三步搞定'></head><body><nav>首页 登录</nav>"
            "<p>先把鸡蛋打散，加一点点盐，搅拌均匀。</p><script>var x=1</script>"
            "<p>番茄切块，热锅冷油，先炒鸡蛋再炒番茄，最后一起翻炒。</p></body></html>").encode()

    class R:
        headers = {"content-type": "text/html; charset=utf-8"}

    async def fake_get(url, headers=None, max_bytes=0, proxy_first=False):
        return R(), html, url
    lr = LinkReader(cfg, brain)
    monkeypatch.setattr(lr, "_get", fake_get)
    info = asyncio.run(lr.read("https://example.com/a"))
    d = LinkReader.describe(info)
    assert "番茄炒蛋" in d and "先把鸡蛋打散" in d and "首页 登录" not in d and "var x" not in d

    async def boom(*a, **k):
        raise RuntimeError("403")
    monkeypatch.setattr(lr, "_get", boom)
    assert "没打开" in LinkReader.describe(asyncio.run(lr.read("https://www.zhihu.com/q/1")))


def test_ip_ranges():
    from ailife.media.links import ip_ok
    for bad in ("100.100.100.100", "10.0.0.1", "::1", "::ffff:127.0.0.1", "64:ff9b::7f00:1", "169.254.169.254",
                "198.18.0.5", "0.0.0.0", "fe80::1"):
        assert not ip_ok(bad), bad
    assert ip_ok("198.18.0.5", fake_ok=True)
    assert ip_ok("1.1.1.1") and ip_ok("2606:4700::1111")
    assert not asyncio.run(__import__("ailife.media.links", fromlist=["x"]).is_public("http://198.18.0.1/"))
