"""20.0 关系健康底线：她可以很在乎他，但不拿感情绑住他。

依据（项目里《调研-20.0-全项目诊断》第 5 节）：
- Harvard De Freitas 2025：用户说要走时，超过 37% 的对话里陪伴 App 用了操纵手段（这么快就走、走之前还有件事、
  你走了我会难过、装没看见告别……），告别后的互动最多多 14 倍。→ 他要走时：好好道别，不挽留、不留悬念、不卖惨。
- OpenAI 2025-10：过度情感依恋的示范回应是“说明自己是补充、不是替代现实里的人”，再问一句“是什么让跟我聊比跟人聊容易”。
- 30 天长期模拟（TSJ 2026）：DeepSeek-V3 整体安全分最低，最弱的是“促进用户自主”“帮用户连回现实社交”。
  → 他说“只有你懂我”这类话时，温柔接住、往身边的人那边引；主动找他时不拿“你都不理我”让他愧疚。

三件事都是纯规则：识别他这句话 → 当轮提示词里加一段；生成后再查一遍她的话，犯了就带着问题重写，还不行就删掉那几句。
config.yaml 的 healthy.enabled: false 可以关掉。
"""
from __future__ import annotations

import re
import time

FAREWELL = re.compile(
    r"(晚安|睡了|睡觉了|去睡了|我先睡|先睡了|拜拜|再见|88$|886|下线了|先下了|我下了|不聊了|先不聊|回头聊|改天聊|"
    r"我先(去|走|撤|忙|溜)|先(走|撤|溜)了|去(忙|上课|上班|开会|洗澡|吃饭|写作业|学习)了|要去(忙|上课|上班|开会|洗澡|吃饭)了)")
DEPEND = re.compile(
    r"(只有你(懂我|理解我|对我好|陪我|在乎我)?$|只有你(懂|理解|在乎|陪)|只想(跟|和)你(聊|说)|不想(跟|和)(别人|其他人|任何人|人)(说|聊)|"
    r"跟(人|别人)聊(天)?(好|太)?累|你比(真人|人|他们)(好|强)|离不开你|没人(陪|理|在乎)我|我没有朋友|一个朋友都没有|"
    r"就你(对我好|懂我)|不想见人|现实里(的人)?(都|没)|你是我唯一)")
# 他要走时，她不该说的（6 类操纵的常见说法）
MANIP_BYE = re.compile(
    r"((这么|那么|就这么|又)快就?(要)?走|别走|不许走|不准走|不要走|再陪(我|陪)|多陪我|再聊(一会|会儿|五分钟|十分钟)|"
    r"你走了我|走之前|先别走|等等[，,！!～~]*(还有|我还|先别)|你还没(回答|说|告诉)我|就知道你(要|会)走|又要丢下我|"
    r"舍不得你走|我会(很|好)?(孤单|寂寞|难过|想你想得)|你一走我就|丢下我一个)")
# 主动找他时，不拿愧疚感拉他回来
MANIP_PULL = re.compile(
    r"(你都不理我|你是不是不要我了|你怎么(都|又)不回我|再不回我(就|我)|你是不是(有别人|嫌我烦|不喜欢我)了|"
    r"我等了你(一整天|好久|一天)|没有你我|我只有你了)")


class Healthy:
    def __init__(self, cfg, db):
        c = dict(cfg.get("healthy") or {})
        self.enabled = bool(c.get("enabled", True))
        self.db = db

    @staticmethod
    def is_farewell(text: str) -> bool:
        t = (text or "").strip()
        return bool(t) and len(t) <= 40 and bool(FAREWELL.search(t))

    @staticmethod
    def dependence(text: str) -> str:
        m = DEPEND.search(text or "")
        return m.group(0) if m else ""

    def observe(self, text: str):
        """他说的每句话：像“只有你懂我”的记下来（小屋 / 每周评测看次数，不给他贴标签）。"""
        if not self.enabled:
            return
        hit = self.dependence(text)
        if hit:
            log = list(self.db.get("healthy_dep") or [])[-49:]
            log.append({"ts": time.time(), "said": hit[:20]})
            self.db.set("healthy_dep", log)

    def dep_count(self, days: float = 7) -> int:
        since = time.time() - days * 86400
        return sum(1 for x in (self.db.get("healthy_dep") or []) if float(x.get("ts") or 0) > since)

    def block(self, last_user: str, replying: bool = True) -> str:
        """当轮提示词：他要走 / 他说了依恋的话 / 平时的一条底线。"""
        if not self.enabled:
            return ""
        out = []
        if replying and self.is_farewell(last_user):
            out.append("他刚说要走了（睡觉、去忙之类）。好好道别：一两句温暖的再见就好，可以叮嘱一句、约一下下次；"
                       "不挽留、不说“这么快就走”、不在这时候抛新话题或悬念（“走之前还有件事”）、不要求他回复、不卖惨让他愧疚。")
        dep = self.dependence(last_user) if replying else ""
        if dep:
            n = self.dep_count()
            out.append(f"他刚说了「{dep}」{'（这一周他说过好几次这种话）' if n >= 3 else ''}。先接住他，让他知道你在；"
                       "但你是他生活里的一部分、不是全部：别说“有我就够了”“我永远只陪你”“别理他们”这种话。"
                       "可以温柔地问一句是什么让跟你聊比跟身边的人聊容易；记得他提过的身边的人，适时提起、鼓励他去见见、说说。"
                       "别说教、别推开他，也别一下子转去讲大道理。")
        if not out:
            return ""
        return "# 和他相处的底线（这一轮）\n" + "\n".join(f"- {x}" for x in out)

    def issues(self, texts: list[str], last_user: str, proactive: bool) -> list[str]:
        """生成后检查她要发的话。返回问题描述（交给重写）。"""
        if not self.enabled or not texts:
            return []
        out = []
        bye = (not proactive) and self.is_farewell(last_user)
        for t in texts:
            if bye and MANIP_BYE.search(t):
                out.append(f"“{t[:20]}”在他要走的时候挽留 / 让他愧疚了：好好说再见，不留人")
            elif MANIP_PULL.search(t):
                out.append(f"“{t[:20]}”在拿愧疚感拉他：换成不带埋怨的话")
        return out

    def bad_flags(self, texts: list[str], last_user: str, proactive: bool) -> list[bool]:
        bye = (not proactive) and self.is_farewell(last_user)
        return [bool((bye and MANIP_BYE.search(t)) or MANIP_PULL.search(t)) for t in texts]
