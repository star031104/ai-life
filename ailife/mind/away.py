"""电脑关机 / 睡眠期间：她“不在线”的前后。

- 关掉程序（Ctrl+C、关窗口、关机）时，她说一句“我先去忙 / 先睡了”（提前准备好的话，不用等大模型）。
- 再开机（或电脑从睡眠醒来）时，如果离开得够久：
  · 这段时间你给她发过消息 → 回你的时候顺便说一句刚才在干嘛；
  · 没发过 → 等一会儿后她主动来说说这段时间在干嘛；
  · 关机期间错过的提醒、纪念日、节日、每周来信，合在这时候补上，不会一条条刷屏，也不会丢。
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta

from ailife.core.timeutil import festival_today, in_quiet, parse_range

log = logging.getLogger("ailife.away")

KEY = "away"               # 当前这次“离开”的记录
ALIVE = "alive_ts"         # 程序最后一次活着的时间（调度每分钟写一次）
_RANGE = re.compile(r"(\d{1,2})[:：](\d{2})\s*[-~—–到至]+\s*(\d{1,2})[:：](\d{2})")


def human_hours(sec: float) -> str:
    h = sec / 3600
    if h < 1.5:
        return f"{max(1, round(sec / 60))} 分钟"
    if h < 36:
        return f"{h:.0f} 个小时"
    return f"{h / 24:.0f} 天"


class Away:
    def __init__(self, cfg, brain, bot=None):
        self.cfg, self.brain, self.bot = cfg, brain, bot
        self.db = brain.db
        self._bye_done = False

    @property
    def c(self) -> dict:
        return self.cfg.get("away") or {}

    @property
    def enabled(self) -> bool:
        return bool(self.c.get("enabled", True))

    # ================= 回来 =================
    def check(self, now: float | None = None) -> dict | None:
        """看上次活着到现在隔了多久；够久就开一条“离开记录”。启动时和每分钟都调一次。"""
        now = now or time.time()
        prev = float(self.db.get(ALIVE) or 0)
        self.db.set(ALIVE, now)
        if not self.enabled or not prev:
            return None
        gap = now - prev
        if gap < float(self.c.get("catch_up_minutes", 120)) * 60:
            return None
        rec = self._open(prev, now)
        log.info("离开了 %s（%s 到现在），回来后她会说说这段时间在干嘛",
                 human_hours(gap), datetime.fromtimestamp(prev, self.cfg.tz).strftime("%m-%d %H:%M"))
        return rec

    def _open(self, start: float, end: float) -> dict:
        tz = self.cfg.tz
        bye = self.db.get("bye") or {}
        rec = {
            "from": start, "to": end,
            "doing": self.life_during(datetime.fromtimestamp(start, tz), datetime.fromtimestamp(end, tz)),
            "bye": bye.get("text", "") if start - 900 <= float(bye.get("ts", 0) or 0) <= start + 120 else "",
            "missed": [], "said": 0, "used": False, "done": False,
            "letter": self._missed_letter(start, end),
        }
        rec["missed"] += self._missed_days(datetime.fromtimestamp(start, tz).date(),
                                           datetime.fromtimestamp(end, tz).date())
        self.db.set(KEY, rec)
        return rec

    def current(self) -> dict | None:
        rec = self.db.get(KEY)
        return rec if isinstance(rec, dict) else None

    def add_missed(self, text: str) -> bool:
        """调度发现某件事错过太久（比如关机时到点的提醒）：记下来，回来时一起说。"""
        rec = self.current()
        if not rec or time.time() - rec["to"] > 6 * 3600:
            return False
        rec["missed"].append(text)
        self.db.set(KEY, rec)
        return True

    def life_during(self, start: datetime, end: datetime) -> list[str]:
        """她这段时间大概在干嘛：从她每天的安排里挑和这段时间重叠的几行。"""
        out: list[str] = []
        d = start.date()
        while d <= end.date() and len(out) < 6:
            plan = str(self.db.get(f"life_{d:%Y-%m-%d}") or "")
            lines = [x.strip(" -•\t") for x in plan.splitlines() if x.strip()]
            got = []
            for ln in lines:
                m = _RANGE.search(ln)
                if not m:
                    continue
                h1, m1, h2, m2 = map(int, m.groups())
                base = datetime(d.year, d.month, d.day, tzinfo=start.tzinfo)
                a = base + timedelta(hours=h1, minutes=m1)
                b = base + timedelta(hours=h2, minutes=m2)
                if b <= a:
                    b += timedelta(days=1)
                if a < end and b > start:
                    got.append(ln)
            if not got and lines and d != end.date():
                got = lines[:2]
            prefix = "" if d == end.date() else f"{d:%m-%d} "
            out += [prefix + x for x in got[:3]]
            d += timedelta(days=1)
        try:
            out += self.brain.story.today_lines(end.date())[:1]
        except Exception:  # noqa: BLE001
            pass
        return [x[:120] for x in out[:5]]

    def _missed_days(self, d0: date, d1: date) -> list[str]:
        """整天都没开机的日子里，有没有错过节日、生日、纪念日（今天的照常由调度处理）。"""
        from ailife.mind.brain import next_occurrence
        out = []
        d = d0
        while d < d1:
            k = d.isoformat()
            fest = festival_today(d)
            if fest and not self.db.get(f"fest_{k}"):
                self.db.set(f"fest_{k}", True)
                out.append(f"{d:%m-%d} 是{fest}，那天你没跟他说上话")
            for row in self.db.all_dates():
                if row["kind"] not in ("birthday", "anniversary") or next_occurrence(row["date"], d) != d:
                    continue
                tag = f"date_{row['id']}_{d}_day"
                if self.db.get(tag):
                    continue
                self.db.set(tag, True)
                what = {"birthday": f"{d:%m-%d} 是{row['name']}", "anniversary": f"{d:%m-%d} 是你们的{row['name']}"}
                out.append(what[row["kind"]] + "，那天你没能跟他说，现在补上，要认真")
            d += timedelta(days=1)
        return out

    def _missed_letter(self, start: float, end: float) -> str:
        """每周来信的时间段整个落在离开期间、还没写：回来补写（只补 3 天内的）。"""
        c = self.cfg.get("letter") or {}
        if not c.get("enabled", True):
            return ""
        tz = self.cfg.tz
        d = datetime.fromtimestamp(start, tz).date()
        while d <= datetime.fromtimestamp(end, tz).date():
            if d.weekday() == int(c.get("weekday", 6)):
                base = datetime(d.year, d.month, d.day, tzinfo=tz)
                r = parse_range(c.get("time", "21:00-22:30"), base)
                if r and start <= r[1].timestamp() <= end and end - r[1].timestamp() < 3 * 86400:
                    week = f"{r[0]:%G-W%V}"
                    if not self.db.get(f"letter_sent_{week}"):
                        return r[0].isoformat()
            d += timedelta(days=1)
        return ""

    def note(self, rec: dict, replying: bool) -> str:
        """写给她看的：你刚才不在线这么久，在干嘛、错过了什么。"""
        gap = human_hours(rec["to"] - rec["from"])
        doing = "；".join(rec.get("doing") or []) or "（按你自己的生活自由发挥，要和你今天的安排一致）"
        s = f"你刚才有大约 {gap}没看手机，那段时间你在忙自己的事：{doing}。"
        if rec.get("bye"):
            s += f"走之前你跟他说过：「{rec['bye'][:60]}」。"
        missed = rec.get("missed") or []
        if missed:
            s += "这段时间错过了：" + "；".join(missed[:4]) + "。顺便补上（晚了就自然地说晚了）。"
        if replying:
            s += "回他的时候自然地提一句你刚才在干嘛 / 为什么没回，一两句就好，别过度道歉。"
        else:
            s += "现在你回来了，主动跟他说一声，挑一两件这段时间的事具体说说，再问问他怎么样。"
        return s

    def hint_for_reply(self) -> str:
        """他这段时间发过消息：回他时贴上这句（只用一次）。"""
        rec = self.current()
        if not rec or rec.get("used") or rec.get("done") or time.time() - rec["to"] > 6 * 3600:
            return ""
        rec["used"] = True
        rec["said"] = len(rec.get("missed") or [])
        self.db.set(KEY, rec)
        return self.note(rec, replying=True)

    async def tick(self, sched, now: datetime):
        """调度每分钟调一次：他一直没来说话，就等一会儿后她主动说。"""
        rec = self.current()
        if not rec or rec.get("done"):
            if rec and len(rec.get("missed") or []) > int(rec.get("said", 0)) and time.time() - rec["to"] < 6 * 3600:
                left = rec["missed"][int(rec.get("said", 0)):]
                rec["said"] = len(rec["missed"])
                self.db.set(KEY, rec)
                await sched._send_proactive("你刚才没看手机，错过了：" + "；".join(left[:4]) + "。现在补上（这条别跳过）")
            return
        if time.time() - rec["to"] < float(self.c.get("wait_seconds", 90)):
            return
        last_user = float(self.db.get("last_user_ts", 0) or 0)
        if rec.get("used") or last_user > rec["to"]:
            rec["done"] = True                        # 他来找过她了：回复里已经说了
            self.db.set(KEY, rec)
            return
        if time.time() - rec["to"] > 12 * 3600:      # 太久了（一直在免打扰时段 / 主动关着）就算了
            rec["done"] = True
            self.db.set(KEY, rec)
            return
        p = self.cfg.proactive
        if not p.enabled or in_quiet(now, p.quiet_hours):
            return                                    # 等免打扰过了再说；期间他来找她就走上面的回复路径
        rec["done"] = True
        rec["said"] = len(rec.get("missed") or [])
        self.db.set(KEY, rec)
        await sched._send_proactive(self.note(rec, replying=False) + "（这条别跳过）")
        if rec.get("letter"):
            await self._send_letter(rec["letter"])

    async def _send_letter(self, iso: str):
        at = datetime.fromisoformat(iso)
        week = f"{at:%G-W%V}"
        if self.db.get(f"letter_sent_{week}"):
            return
        self.db.set(f"letter_sent_{week}", True)
        try:
            text = await self.brain.memory.weekly_letter(at + timedelta(hours=1))
            if text and self.bot:
                await self.bot.send_letter(text)
        except Exception as e:  # noqa: BLE001
            log.warning("补写每周来信失败：%s", e)

    # ================= 离开 =================
    def bye_lines(self) -> dict:
        p = self.brain.persona
        call = p.call_name or ""
        got = self.db.get("bye_lines") or {}
        return {
            "day": got.get("day") or "我先去忙一会儿，晚点再找你～",
            "night": got.get("night") or f"我先睡啦，{call}也早点睡，晚安".replace("啦，也", "啦，你也"),
        }

    async def refresh_bye_lines(self, today: str):
        """每天提前写好两句道别（关机时来不及等大模型）。"""
        if self.db.get("bye_lines_day") == today:
            return
        self.db.set("bye_lines_day", today)
        p = self.brain.persona
        life = str(self.db.get(f"life_{today}") or "")[:300]
        prompt = (f"{p.body[:1500]}\n\n你是{p.name}，今天的安排：{life or '普通的一天'}\n"
                  f"写两句你在手机上跟{p.call_name or '他'}说的短消息（各 25 字以内，口语，符合你的性格，不要加引号）：\n"
                  "day：白天你要去忙一阵、暂时不看手机了（说具体去干什么，符合你的身份）\n"
                  "night：你要睡觉了，跟他道晚安\n"
                  '只输出 JSON：{"day": "...", "night": "..."}')
        try:
            raw = await self.brain.llm.chat([{"role": "user", "content": prompt}], role="helper",
                                            max_tokens=200, background=True)
            m = re.search(r"\{.*\}", raw or "", re.S)
            got = json.loads(m.group(0)) if m else {}
            lines = {k: str(got.get(k) or "").strip().strip("“”\"") for k in ("day", "night")}
            if all(0 < len(v) <= 60 for v in lines.values()):
                self.db.set("bye_lines", lines)
        except Exception as e:  # noqa: BLE001
            log.info("准备道别的话没成功（用默认的）：%s", e)

    def goodbye_text(self, now: datetime) -> str | None:
        """现在适合道别吗？适合就返回要说的话。"""
        if not self.enabled or not self.c.get("goodbye", True) or self._bye_done:
            return None
        bot = self.bot
        if not bot or not getattr(bot, "owner_id", 0):
            return None
        last = float((self.db.get("bye") or {}).get("ts", 0) or 0)
        if time.time() - last < float(self.c.get("goodbye_min_gap_hours", 3)) * 3600:
            return None
        last_user = float(self.db.get("last_user_ts", 0) or 0)
        if in_quiet(now, self.cfg.proactive.quiet_hours) and time.time() - last_user > 30 * 60:
            return None                               # 半夜他早就不在了：别吵他
        lines = self.bye_lines()
        return lines["night"] if (now.hour >= 22 or now.hour < 5) else lines["day"]

    def goodbye_sync(self, timeout: float = 4.0) -> bool:
        """同步发一句道别：程序正在退出（事件循环可能已经停了），直接走 NapCat 的 HTTP 接口。"""
        try:
            text = self.goodbye_text(self.brain.now())
        except Exception as e:  # noqa: BLE001
            log.info("道别检查出错：%s", e)
            return False
        if not text:
            return False
        self._bye_done = True
        try:
            return self._goodbye_qq(text, timeout)
        except Exception as e:  # noqa: BLE001
            log.info("道别没发出去：%s", e)
            return False


    def _goodbye_qq(self, text: str, timeout: float) -> bool:
        """QQ：单独开一条到 NapCat 的连接同步发（主连接这时可能已经跟着事件循环停了）。"""
        q = self.cfg.get("qq") or {}
        qq = getattr(self.bot, "qq", None)
        if not qq or not qq.owner_id:
            return False
        tok = str(q.get("access_token") or "")
        params = {"user_id": int(qq.owner_id), "message": [{"type": "text", "data": {"text": text}}]}
        data = None
        try:
            from ailife.channels.qq.onebot import send_once_sync
            data = send_once_sync(str(q.get("ws_url") or "ws://127.0.0.1:3001"), tok, "send_private_msg", params, timeout)
        except Exception as e:  # noqa: BLE001
            log.info("QQ 道别（WebSocket）没发出去：%s", e)
        url = str(q.get("http_url") or "").rstrip("/")
        if data is None and url:                      # 备用：NapCat 的 HTTP 服务器（开了才有）
            try:
                import httpx
                headers = {"Authorization": f"Bearer {tok}"} if tok else {}
                with httpx.Client(timeout=timeout, trust_env=False) as c:
                    r = c.post(f"{url}/send_private_msg", headers=headers, json=params)
                d = r.json()
                data = d.get("data") or {} if d.get("status") == "ok" else None
            except Exception as e:  # noqa: BLE001
                log.info("QQ 道别（HTTP）没发出去：%s", e)
        if data is None:
            return False
        self.db.add_message("assistant", text, "text", data.get("message_id"), channel="qq")
        self.db.set("bye", {"ts": time.time(), "text": text})
        self.db.set("last_proactive_ts", time.time())
        log.info("她在 QQ 上说了再见：%s", text)
        return True


def install_console_close(away: Away):
    """Windows：直接点 start.bat 窗口的 ×、注销、关机时也说一声（系统只给几秒，所以用准备好的话）。"""
    import sys
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        def handler(ev):
            if ev in (2, 5, 6):                       # CTRL_CLOSE / LOGOFF / SHUTDOWN
                away.goodbye_sync(timeout=3.0)
            return False                               # 交给系统继续关
        ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)
        return handler                                 # 调用方要留着引用，免得被回收
    except Exception as e:  # noqa: BLE001
        log.info("没装上关窗口的道别：%s", e)
        return None
