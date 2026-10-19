"""聊天各部分共用的常量和小工具：错别字表、贴纸情绪、命令列表、“等我一下”、系统文字里的代词。"""
from __future__ import annotations

TYPOS = {"在": "再", "再": "在", "的": "得", "那": "哪", "做": "作", "已经": "以经", "知道": "知到", "吗": "嘛"}
EMOTION_LABELS = "开心 大笑 喜欢 害羞 难过 委屈 生气 哼 惊讶 无语 困 抱抱 想你 得意 哭 疑惑 加油 坏笑 期待 晚安".split()


HELP = [
    ("status", "她现在的心情和今天的安排"),
    ("memory", "她记得的关于你的事"),
    ("diary", "偷看她的日记"),
    ("photo", "让她发张照片"),
    ("city", "告诉她你在哪个城市（天气）"),
    ("schedule", "告诉她你的作息/课表"),
    ("dates", "重要日子：/dates 11-20 在一起纪念日"),
    ("shows", "一起追番：/shows 番名 周五 23:00"),
    ("home", "她的小屋在哪、怎么打开"),
    ("note", "在小屋留言板给她留言"),
    ("letter", "看她这周写给你的信"),
    ("sense", "手机信号（起床 / 到家 / 充电）的设置"),
    ("watched", "看完一集番：/watched 番名 集数"),
    ("song", "让她推荐一首歌"),
    ("profile", "她心里的你"),
    ("story", "她最近在经历的事"),
    ("mind", "偷看她在想什么"),
    ("diag", "一键自检（哪里坏了）"),
    ("habit", "一起打卡：/habit add 早睡 23:30，打卡 /habit 编号"),
    ("recap", "每月纪念册（/recap 本月 看这个月到现在）"),
    ("quality", "这一周她的表现（重复、出戏、回复长度……）"),
    ("persona", "人设：看 / 换角色 / 改完重新读"),
    ("wish", "愿望清单：/wish add 一起去看海"),
    ("capsule", "时间胶囊：/capsule 一年 写给一年后的我们"),
    ("goals", "她自己在努力的长期目标"),
    ("people", "她身边的人（室友、朋友……）"),
    ("help", "所有命令"),
]
HELP_TEXT = "\n".join(f"/{c}  {d}" for c, d in HELP)


def stats_line(st: dict) -> str:
    keys = ["聊天的天数", "他发的消息", "她发的消息", "照片", "新记住的事", "一起听的歌", "打卡", "完成的愿望"]
    return " · ".join(f"{k} {st[k]}" for k in keys if st.get(k))


# 大模型全挂时先稳住他的话（不经过大模型）
HOLD_LINES = ["信号好差……等我一下下", "啊网卡住了，等我一下！", "稍等，我这边网有点卡", "等等，消息没发出去，马上回你",
              "我这边网络抽风了，等我一会儿"]


# ---------------- 系统提示文字里的“她”：角色是男生时改成“他”（她自己说的话不动） ----------------
UI = {"ta": "她"}


KEEP = "\u2063"          # 不可见分隔符：包在里面的是她写的内容（日记、信……），代词不动


def keep(text) -> str:
    """标记一段内容不做代词替换（大模型写的内容里“她”可能指的是你）。"""
    return f"{KEEP}{text}{KEEP}" if text else ""


def ui(text):
    if not isinstance(text, str):
        return text
    if KEEP not in text:
        return text if UI["ta"] == "她" else text.replace("她", UI["ta"])
    parts = text.split(KEEP)
    if UI["ta"] != "她":
        parts = [x if i % 2 else x.replace("她", UI["ta"]) for i, x in enumerate(parts)]
    return "".join(parts)


def install_ui_pronoun(ta: str):
    """系统文字（命令的回复）里的“她”换成角色的代词（男性角色是“他”）。她自己说的话不受影响。"""
    UI["ta"] = ta or "她"
