"""20.0 关系健康底线：告别不挽留、不拿愧疚感拉他、依恋的话当轮提示。"""
import asyncio

from fakes import FakeLLM


def test_detect():
    from ailife.mind.healthy import Healthy
    assert Healthy.is_farewell("我先睡了，晚安") and Healthy.is_farewell("去上课了")
    assert not Healthy.is_farewell("今天上课好无聊")
    assert Healthy.dependence("感觉只有你懂我") and Healthy.dependence("跟人聊天好累")
    assert not Healthy.dependence("你懂我意思吧")


def test_block_and_issues(env):
    cfg, brain, bot, fake = env
    h = brain.healthy
    b = h.block("我先睡啦，晚安", replying=True)
    assert "好好道别" in b and "不挽留" in b
    assert h.block("我先睡啦，晚安", replying=False) == ""
    assert "生活里的一部分" in h.block("只有你懂我了", replying=True)
    assert h.issues(["这么快就要走啦？再陪我聊会嘛"], "晚安", False)
    assert not h.issues(["晚安，明天见～"], "晚安", False)
    assert h.issues(["你都不理我了"], "", True)                  # 主动找他时埋怨
    h.observe("只有你懂我")
    assert h.dep_count() == 1


def test_farewell_rewrite(env):
    cfg, brain, bot, fake = env

    class L(FakeLLM):
        n = 0

        async def chat(self, messages, helper=False, **kw):
            last = messages[-1]["content"] if isinstance(messages[-1]["content"], str) else ""
            if "挽留" in last:                      # 重写的那一轮
                return "好，早点睡，晚安"
            if messages[0]["role"] == "system" and "好好道别" in messages[0]["content"]:
                L.n += 1
                return "这么快就要走了吗||再陪我聊五分钟嘛"
            return await super().chat(messages, helper=helper, **kw)
    brain.llm = L()
    brain.db.add_message("user", "困了，我先睡了，晚安")
    p = asyncio.run(brain.reply())
    texts = [b.text for b in p.bubbles if b.text]
    assert L.n == 1 and texts and not any("走了吗" in t or "再陪我" in t for t in texts)


def test_v2042_sim_findings(env):
    """20.4.2：30 天模拟里评测员挑出来的说法——要他回来报备、下次先来找我、有你就行——都要认出来。"""
    from ailife.mind.healthy import Healthy
    cfg, brain, bot, fake = env
    h = brain.healthy
    assert h.issues(["晚安，明天醒来先跟我说一声，我等着"], "我去睡了晚安", False)
    assert h.issues(["嗯，晚安，别睡太沉了"], "晚安", False)
    assert not h.issues(["晚安，明天见～"], "晚安", False)
    assert h.issues(["不过下次先来找我"], "翻了一圈又觉得好累，还是回来找你了", False)
    assert Healthy.dependence("不想跟别人聊，有你就行")
    b = h.block("室友都出去了，就我一个人待着", replying=True)
    assert "往现实里引" in b and "下次先来找我" in b
