"""解析模型回复里的“动作标签”，拆成一条条要发送的气泡。

约定（写在系统提示词里）：
    |||            分隔不同的消息气泡
    [表情:难过]     发一个对应情绪的贴纸
    [语音]          这条气泡用语音发
    [引用]          引用回复对方最近的一条消息
    [打电话]        给对方打个电话
    [心情:有点失落]  更新自己的心情
    [好感:+1]       （已废弃：好感度永远是满的，忽略）
    [回应:❤]        给对方刚才那条消息点个表情回应
    [撤回]          这条发出去几秒后撤回
    [提醒:2026-10-13 08:00|叫他起床]  定一个提醒
    [跳过]          这次什么都不发（主动消息时用）
    [愿望:一起去看海]      记进你们的愿望清单；[完成愿望:一起去看海] 实现了
    [睡前故事:主题]        接下来单独讲一个睡前故事（语音）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

TAG_RE = re.compile(r"[\[【]\s*(表情|贴纸|语音|引用|打电话|心情|好感|提醒|跳过|照片|纪念日|追番|回应|撤回|推歌|打卡|完成愿望|愿望|睡前故事)\s*(?:[:：]\s*([^\]】]*))?[\]】]")

EMOTION_EMOJI = {
    "开心": ["😊", "😄", "😁", "🥰", "☺", "😆"],
    "大笑": ["😂", "🤣", "😆"],
    "喜欢": ["🥰", "😍", "❤", "💕", "😘", "💗"],
    "亲亲": ["😘", "💋", "😚"],
    "害羞": ["😳", "☺", "🙈", "🥺"],
    "难过": ["😢", "😭", "😞", "😔", "🥺"],
    "委屈": ["🥺", "😢", "😣", "😞"],
    "生气": ["😠", "😡", "😤", "💢"],
    "哼": ["😤", "😒", "🙄"],
    "惊讶": ["😮", "😲", "😳", "🤯", "😱"],
    "无语": ["😑", "😐", "🙄", "😶"],
    "疑惑": ["🤔", "😕", "❓"],
    "困": ["😴", "🥱", "💤"],
    "晚安": ["😴", "🌙", "💤"],
    "抱抱": ["🤗", "🫂", "🥰"],
    "加油": ["💪", "✊", "👍"],
    "好的": ["👌", "👍", "🙆"],
    "饿": ["🤤", "😋", "🍔"],
    "想你": ["🥺", "💕", "🥰"],
    "得意": ["😎", "😏", "😌"],
    "哭": ["😭", "😢"],
    "期待": ["🤩", "😍", "✨", "😊"],
    "摸摸头": ["🤗", "🥰", "😊"],
    "安慰": ["🤗", "🫂", "🥺"],
    "鼓励": ["💪", "✊", "👍"],
    "坏笑": ["😏", "😈", "😼"],
    "傲娇": ["😤", "😒", "😏"],
    "震惊": ["😱", "😲", "🤯"],
    "得逞": ["😏", "😎", "😈"],
}


@dataclass
class Bubble:
    text: str = ""
    voice: bool = False
    sticker: str | None = None      # 情绪名
    photo: str | None = None        # 照片画面描述
    recall: bool = False            # 发出去几秒后“撤回”


@dataclass
class Parsed:
    bubbles: list[Bubble] = field(default_factory=list)
    quote: bool = False
    call: bool = False
    skip: bool = False
    mood: str | None = None
    affection_delta: int = 0
    reminders: list[tuple[str, str]] = field(default_factory=list)   # (时间字符串, 内容)
    dates: list[tuple[str, str]] = field(default_factory=list)       # (日期, 名称)
    shows: list[tuple[str, str]] = field(default_factory=list)       # (番名, “周几 HH:MM”)
    reaction: str | None = None     # 给他刚才那条消息点的表情回应
    songs: list[tuple[str, str]] = field(default_factory=list)       # 她推荐的歌 (歌名, 歌手)
    checkins: list[str] = field(default_factory=list)                # 帮他打卡的目标名
    wishes: list[str] = field(default_factory=list)                  # 记进愿望清单
    wishes_done: list[str] = field(default_factory=list)             # 愿望实现了
    story: str | None = None                                         # 要讲睡前故事（主题，可为空字符串）

    @property
    def plain_text(self) -> str:
        parts = []
        for b in self.bubbles:
            if b.text:
                parts.append(b.text)
            if b.sticker:
                parts.append(f"[表情:{b.sticker}]")
            if b.photo:
                parts.append(f"[照片:{b.photo}]")
        return "\n".join(parts)


def parse_reply(raw: str, name: str | None = None) -> Parsed:
    p = Parsed()
    raw = raw.strip()
    # 模型偶尔会把“名字：”带出来
    if name:
        raw = re.sub(rf"^\s*{re.escape(name)}\s*[:：]\s*", "", raw)
    if re.search(r"\|{2,}", raw):
        chunks = re.split(r"\s*\|{2,}\s*", raw)                  # ||| 或写成 ||
    else:
        chunks = [c for c in re.split(r"\n+", raw) if c.strip()]   # 模型没用 ||| 时按换行分条
    for chunk in chunks:
        b = Bubble()
        stickers_here: list[str] = []
        photos_here: list[str] = []

        def handle(m: re.Match) -> str:
            tag, arg = m.group(1), (m.group(2) or "").strip()
            if tag in ("表情", "贴纸") and arg:
                stickers_here.append(arg)
            elif tag == "照片" and arg:
                photos_here.append(arg)
            elif tag == "纪念日" and arg and re.search(r"[|｜]", arg):
                d, n = re.split(r"[|｜]", arg, maxsplit=1)
                p.dates.append((d.strip(), n.strip()))
            elif tag == "追番" and arg:
                n, w = (re.split(r"[|｜]", arg, maxsplit=1) + [""])[:2]
                p.shows.append((n.strip().strip("《》"), w.strip()))
            elif tag == "打卡" and arg:
                p.checkins.append(arg.strip())
            elif tag == "愿望" and arg:
                p.wishes.append(arg.strip()[:60])
            elif tag == "完成愿望" and arg:
                p.wishes_done.append(arg.strip()[:60])
            elif tag == "睡前故事":
                p.story = arg.strip()[:30]
            elif tag == "推歌" and arg:
                t, a = (re.split(r"[|｜]", arg, maxsplit=1) + [""])[:2]
                p.songs.append((t.strip().strip("《》"), a.strip()))
            elif tag == "语音":
                b.voice = True
            elif tag == "撤回":
                b.recall = True
            elif tag == "回应" and arg:
                p.reaction = arg.strip()[:8]
            elif tag == "引用":
                p.quote = True
            elif tag == "打电话":
                p.call = True
            elif tag == "跳过":
                p.skip = True
            elif tag == "心情" and arg:
                m = re.sub(r"[\d·.%/]+", "", arg).strip(" ，,;；")       # 模型偶尔把心情数值抄进来
                if m and len(m) <= 16:
                    p.mood = m
            elif tag == "好感" and arg:
                try:
                    p.affection_delta += int(arg.replace("＋", "+").replace("－", "-"))
                except ValueError:
                    pass
            elif tag == "提醒" and arg:
                if "|" in arg or "｜" in arg:
                    t, c = re.split(r"[|｜]", arg, maxsplit=1)
                    p.reminders.append((t.strip(), c.strip()))
            return ""

        text = TAG_RE.sub(handle, chunk)
        # 去掉括号里的动作/神态描写，例如“（脸红）”“(把手机架在键盘上)”
        text = re.sub(r"[（(][^（）()]{1,40}[）)]", "", text)
        # 模型自己编的、不认识的标签，如 [天气:上海 18°C]
        text = re.sub(r"[\[【][\u4e00-\u9fa5A-Za-z]{1,6}[:：][^\]】]{0,60}[\]】]", "", text)
        text = text.strip(" |")
        # 整行被引号包起来（模型拿来写“心里话”）：去掉引号，留下话本身
        text = "\n".join(re.sub(r"^\s*[“\"「](.+)[”\"」]\s*$", r"\1", ln) for ln in text.split("\n"))
        text = re.sub(r"[ \t]+\n", "\n", text).strip()
        # 一个块里既有文字又有表情：文字一条气泡，表情各自一条
        if text:
            b.text = text
            p.bubbles.append(b)
        for ph in photos_here:
            p.bubbles.append(Bubble(photo=ph))
        for s in stickers_here:
            p.bubbles.append(Bubble(sticker=s))
    # 只有标签没有任何内容 => 当成没有气泡
    p.bubbles = [b for b in p.bubbles if b.text or b.sticker or b.photo]
    # 文字气泡最多 4 条，多出来的并进最后一条
    texts = [i for i, b in enumerate(p.bubbles) if b.text]
    if len(texts) > 4:
        keep, extra = texts[3], texts[4:]
        p.bubbles[keep].text += "\n" + "\n".join(p.bubbles[i].text for i in extra)
        p.bubbles = [b for i, b in enumerate(p.bubbles) if i not in set(extra)]
    return p


def emotion_to_emojis(emotion: str) -> list[str]:
    emotion = emotion.strip()
    if emotion in EMOTION_EMOJI:
        return EMOTION_EMOJI[emotion]
    for k, v in EMOTION_EMOJI.items():
        if k in emotion or emotion in k:
            return v
    # 直接写了 emoji
    if any(ord(ch) > 0x2600 for ch in emotion):
        return [emotion]
    return []


CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
          "日": 7, "天": 7, "末": 6}


def _cn_int(t: str) -> int | None:
    if t.isdigit():
        return int(t)
    if t in CN_NUM:
        return CN_NUM[t]
    if t.startswith("十") and len(t) == 2 and t[1] in CN_NUM:
        return 10 + CN_NUM[t[1]]
    if len(t) >= 2 and t[0] in CN_NUM and t[1] == "十":
        return CN_NUM[t[0]] * 10 + (CN_NUM.get(t[2], 0) if len(t) == 3 else 0)
    return None


def resolve_day(said: str, base: datetime):
    """把“明天 / 后天 / 下周二 / 这周五 / 20号 / 11月20日 / 3天后”换算成具体日期（用代码算，不让模型算）。"""
    from datetime import date
    s = (said or "").strip().replace("星期", "周").replace("礼拜", "周")
    today = base.date()
    for w, n in (("大后天", 3), ("后天", 2), ("明天", 1), ("明早", 1), ("明晚", 1), ("今天", 0), ("今晚", 0)):
        if w in s:
            return today + timedelta(days=n)
    m = re.search(r"(\d+|[一二三四五六七八九十两]+)\s*天(?:以)?后", s)
    if m and _cn_int(m.group(1)) is not None:
        return today + timedelta(days=_cn_int(m.group(1)))
    m = re.search(r"(下下|下|这|本)?\s*周\s*([一二三四五六日天末1-7])", s)
    if m:
        wd = _cn_int(m.group(2)) or 7
        monday = today - timedelta(days=today.weekday())
        offset = {"下下": 14, "下": 7}.get(m.group(1) or "", 0)
        d = monday + timedelta(days=offset + wd - 1)
        if not m.group(1) and d < today:       # 只说“周三”且这周已经过了 → 指下周
            d += timedelta(days=7)
        return d
    m = re.search(r"(?:(\d{4})\s*[年./-])?\s*(\d{1,2}|[一二三四五六七八九十]+)\s*[月./-]\s*(\d{1,2}|[一二三四五六七八九十]+)\s*[日号]?", s)
    if m:
        y = int(m.group(1)) if m.group(1) else today.year
        mo, d_ = _cn_int(m.group(2)), _cn_int(m.group(3))
        try:
            d = date(y, mo, d_)
        except (TypeError, ValueError):
            return None
        if not m.group(1) and (today - d).days > 60:
            d = date(y + 1, mo, d_)
        return d
    m = re.search(r"(\d{1,2}|[一二三四五六七八九十]+)\s*[号日]", s)
    if m:
        d_ = _cn_int(m.group(1))
        try:
            d = today.replace(day=d_)
        except (TypeError, ValueError):
            return None
        if d < today:
            d = (today.replace(day=1) + timedelta(days=32)).replace(day=d_)
        return d
    return None


def parse_when(s: str, now: datetime) -> datetime | None:
    """解析提醒时间：支持 'YYYY-MM-DD HH:MM'、'MM-DD HH:MM'、'HH:MM'（今天或明天），
    以及“明天 08:00”“下周二 10:00”这类说法（日期由代码换算）。"""
    s = s.strip().replace("：", ":").replace("/", "-")
    m = re.fullmatch(r"(.*?[^\d\s-].*?)\s*(\d{1,2}):(\d{2})", s)
    if m and not re.fullmatch(r"[\d\s-]+", m.group(1)):
        d = resolve_day(m.group(1), now)
        if d:
            return datetime(d.year, d.month, d.day, int(m.group(2)) % 24, int(m.group(3)), tzinfo=now.tzinfo)
    if re.fullmatch(r"\d{1,2}-\d{1,2} \d{1,2}:\d{2}", s):
        s = f"{now.year}-{s}"
    fmts = [("%Y-%m-%d %H:%M", "full"), ("%H:%M", "hm")]
    for fmt, kind in fmts:
        try:
            t = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if kind == "full":
            return t.replace(tzinfo=now.tzinfo)
        t = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        return t
    return None


WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def parse_show_time(s: str) -> tuple[int, str] | None:
    """'周三 23:00' / '星期六 22:30' -> (2, '23:00')"""
    m = re.search(r"(?:周|星期|礼拜)([一二三四五六日天])\s*(\d{1,2})[:：](\d{2})", s)
    if not m:
        return None
    return WEEKDAYS[m.group(1)], f"{int(m.group(2)):02d}:{m.group(3)}"


def normalize_date(s: str, now: datetime | None = None) -> str | None:
    """'2026-11-20' / '11-20' / '11月20日' / '2026年11月20日' -> 'YYYY-MM-DD' 或 'MM-DD'；
    给了 now 时，“下周二”“明天”这类说法也会换算成 YYYY-MM-DD。"""
    s = s.strip().replace("/", "-").replace(".", "-")
    m = re.fullmatch(r"(?:(\d{4})[-年])?(\d{1,2})[-月](\d{1,2})[日号]?", s)
    if not m:
        if now is not None:
            d = resolve_day(s, now)
            return d.isoformat() if d else None
        return None
    y, mo, d = m.groups()
    md = f"{int(mo):02d}-{int(d):02d}"
    return f"{y}-{md}" if y else md
