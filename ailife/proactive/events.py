"""有固定时间的事：纪念日、节日、每周来信、追番更新、天气预警、打卡催促、每月纪念册、时间胶囊、她的目标。"""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta

from ailife.core.timeutil import festival_today, in_quiet, parse_range

log = logging.getLogger("ailife.proactive")


class EventsMixin:
    # ---------------- 重要日子 ----------------
    async def _special_dates(self, now: datetime):
        from ailife.mind.brain import next_occurrence
        today = now.date()
        key_day = today.isoformat()
        # 等早安发过（或中午以后）再说，免得一大早扎堆
        if not self.db.get(f"morning_done_{key_day}") and now.hour < 12:
            return
        for d in self.db.all_dates():
            name, kind = d["name"], d["kind"]
            nxt = next_occurrence(d["date"], today)
            reason = None
            if nxt == today:
                tag = f"date_{d['id']}_{nxt}_day"
                reason = {"birthday": f"今天是{name}！好好给他过",
                          "anniversary": f"今天是你们的{name}，用你的方式表达一下、也可以约他做点什么"}.get(
                    kind, f"今天是他的{name}，给他打气加油")
            elif nxt and (nxt - today).days == int(d["remind_before"]):
                tag = f"date_{d['id']}_{nxt}_before"
                left = (nxt - today).days
                reason = (f"再过 {left} 天就是{name}了，你偷偷在准备，可以不经意地提一下" if kind != "event"
                          else f"他的{name}还有 {left} 天，提醒他好好准备、别熬太晚")
            elif kind == "event" and len(d["date"]) == 10:
                from datetime import date
                if (today - date.fromisoformat(d["date"])).days == 1:
                    tag = f"date_{d['id']}_after"
                    reason = f"昨天是他的{name}，问问他结果怎么样（别给压力）"
            if reason and not self.db.get(tag):
                self.db.set(tag, True)
                await self._send_proactive(reason)
                return              # 一次只说一件

    # ---------------- 追番 ----------------
    async def _shows(self, now: datetime):
        for sh in self.db.active_shows():
            if sh["weekday"] != now.weekday():
                continue
            h, m = map(int, sh["time"].split(":"))
            air = now.replace(hour=h, minute=m, second=0, microsecond=0)
            mins = (now - air).total_seconds() / 60
            tag = f"show_{sh['id']}_{now:%Y-%m-%d}"
            if 20 <= mins <= 360 and not self.db.get(tag):
                self.db.set(tag, True)
                media = self.brain.media
                info_all = await media.enrich_show(sh)      # 按 Bangumi 的播出日期校正集数
                row = self.db.conn.execute("SELECT * FROM shows WHERE id=?", (sh["id"],)).fetchone()
                nkey = f"show_notified_{sh['id']}"
                notified = int(self.db.get(nkey, 0) or 0)
                if info_all and info_all.get("eps"):
                    recent = {now.date().isoformat(), (now.date() - timedelta(days=1)).isoformat()}
                    fresh = [e["ep"] for e in info_all["eps"] if e["airdate"] in recent and e["ep"] > notified]
                    if not fresh:                          # Bangumi 说今天没有新的一集（停播 / 总集篇）
                        continue
                    ep = max(fresh)
                else:                                      # 查不到 Bangumi：按每周一集算
                    self.db.bump_show(sh["id"])
                    ep = sh["episode"] + 1
                self.db.set(nkey, ep)
                if row["total_eps"] and ep > row["total_eps"]:
                    self.db.delete_show(sh["id"])
                    continue
                info = await media.episode(row, ep)
                about = ""
                if info and (info.get("title") or info.get("desc")):
                    about = f"（这集叫「{info['title']}」，内容：{info['desc'][:150]}。你也看过了，可以聊剧情，但他没看之前别剧透）"
                last = " 这是最后一集！" if row["total_eps"] and ep == row["total_eps"] else ""
                if time.time() - (self.db.get("last_user_ts", 0) or 0) < 30 * 60:
                    reason = f"《{sh['name']}》第 {ep} 集刚更新，他正好在线，兴奋地约他一起看{last}{about}"
                else:
                    reason = f"《{sh['name']}》第 {ep} 集更新了，你想找他一起看/讨论{last}{about}"
                await self._send_proactive(reason)
                return

    # ---------------- 11.2 随手拍：让他看看她在干嘛 ----------------
    async def _life_photo(self, now: datetime):
        ph = self.brain.photos
        per_week = int((self.cfg.get("photo") or {}).get("proactive_per_week", 3))
        if per_week <= 0 or not ph.enabled or not ph.gen.ready or ph.remaining_today() <= 0 or not (11 <= now.hour <= 21):
            return
        wk, day = f"life_photo_{now:%G-W%V}", f"life_photo_day_{now:%Y-%m-%d}"
        n = int(self.db.get(wk, 0) or 0)
        if n >= per_week or self.db.get(day) or int(self.db.get("unanswered", 0) or 0) >= 2:
            return
        if time.time() - float(self.db.get("last_proactive_ts", 0) or 0) < 90 * 60:
            return
        if time.time() - float(self.db.get("last_user_ts", 0) or 0) < 10 * 60:    # 正聊着：别插一张照片进来
            return
        if random.random() >= (per_week / 7) / (11 * 60):                    # 每分钟看一次：一周大约 per_week 次
            return
        self.db.set(day, True)
        ok = await self._send_proactive("你想让他看看你此刻在干嘛：随手拍一张照片发给他（写 [照片:画面描述]，"
                                        "画面要具体、和你今天的安排对得上），再配一两句话，像平时分享生活那样")
        if ok:
            self.db.set(wk, n + 1)

    # ---------------- 天气 ----------------
    async def _weather_alert(self, now: datetime):
        if not self.brain.weather.city or not (7 <= now.hour <= 21):
            return
        if time.time() - (self.db.get("weather_check_ts", 0) or 0) < 3600:
            return
        self.db.set("weather_check_ts", time.time())
        w = await self.brain.weather.fetch(force=True)
        self.brain.weather_info = w
        tag = f"rain_alert_{now:%Y-%m-%d}"
        if w and w.get("rain_soon") and not self.db.get(tag):
            self.db.set(tag, True)
            await self._send_proactive(f"你看到他那边（{w['city']}）接下来几个小时要下雨了，提醒他出门带伞")

    # ---------------- 节日 / 节气 ----------------
    async def _festival(self, now: datetime):
        fest = festival_today(now.date())
        if not fest:
            return
        tag = f"fest_{now:%Y-%m-%d}"
        if self.db.get(tag) or (not self.db.get(f"morning_done_{now:%Y-%m-%d}") and now.hour < 12):
            return
        self.db.set(tag, True)
        await self._send_proactive(f"今天是{fest}，用你的方式跟他说一声（可以约他做点应景的事）")

    # ---------------- 每周来信 ----------------
    async def _weekly_letter(self, now: datetime):
        c = self.cfg.get("letter") or {}
        if not c.get("enabled", True) or now.weekday() != int(c.get("weekday", 6)):
            return
        r = parse_range(c.get("time", "21:00-22:30"), now.replace(hour=0, minute=0))
        if not r or not (r[0] <= now <= r[1]):
            return
        week = f"{now:%G-W%V}"
        if self.db.get(f"letter_sent_{week}"):
            return
        self.db.set(f"letter_sent_{week}", True)
        text = await self.brain.memory.weekly_letter(now)
        if text:
            await self.bot.send_letter(text)

    # ---------------- 一起打卡：到点没打卡就来催 ----------------
    async def _habits(self, now: datetime):
        from ailife.mind.together import habit_day
        day = habit_day(now)
        for h in self.db.habits():
            t = h["remind_time"]
            if not t:
                continue
            try:
                hh, mm = map(int, t.split(":"))
                at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            except ValueError:
                continue
            mins = (now - at).total_seconds() / 60
            tag = f"habit_nag_{h['id']}_{day}"
            if 0 <= mins <= 30 and not self.db.checked(h["id"], day) and not self.db.get(tag):
                self.db.set(tag, True)
                st = self.db.streak(h["id"], day)
                more = f"他已经连续 {st} 天了，别让他断" if st else "他昨天没坚持住，今天要盯紧点"
                await self._send_proactive(f"到点了，监督他完成打卡目标「{h['name']}」（{more}）。"
                                           "用你的方式催他，完成了让他跟你说")
                return

    # ---------------- 每月纪念册 ----------------
    async def _monthly_recap(self, now: datetime):
        # 1 号那天电脑没开、或者模型限流失败，之后 7 天里都会补；失败了隔 3 小时再试
        if now.day > 7 or now.hour < 10 or in_quiet(now, self.cfg.proactive.quiet_hours):
            return
        y, m = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        key = f"{y}-{m:02d}"
        if self.db.get_recap(key) or time.time() - float(self.db.get(f"recap_try_{key}") or 0) < 3 * 3600:
            return
        self.db.set(f"recap_try_{key}", time.time())
        res = await self.brain.together.monthly_recap(y, m)
        if res and self.bot.owner_id:
            from ailife.chat.common import stats_line
            text = f"📒 我们的{m}月\n\n{res[0]}\n\n{stats_line(res[1])}"
            msg, ch = await self.bot.owner_text(text)
            if msg:
                self.db.add_message("assistant", f"[{m}月纪念册] {res[0]}", "letter", msg.message_id, channel=ch)
            else:
                log.warning("纪念册没发出去")

    # ---------------- 时间胶囊：到日子拆开 ----------------
    async def _capsules(self, now: datetime):
        if now.hour < 9 or in_quiet(now, self.cfg.proactive.quiet_hours):
            return
        due = self.db.due_capsules(now.date().isoformat())
        if not due or not self.bot.owner_id:
            return
        tz = self.cfg.tz

        class _Fail(Exception):
            pass

        async def send(text):
            m, ch = await self.bot.owner_text(text)
            if not m:
                raise _Fail("没发出去")
            return m, ch
        for c in due[:4]:
            written = datetime.fromtimestamp(c["created"], tz)
            days = (now.date() - written.date()).days
            try:
                if c["by"] == "her":
                    text = f"💌 {written:%Y-%m-%d} 她写给今天的你（{days} 天前埋下的时间胶囊）\n\n{c['content']}"
                    m, ch = await send(text)
                    self.db.add_message("assistant", f"[时间胶囊] {c['content']}", "letter", m.message_id, channel=ch)
                    self.db.open_capsule(c["id"])
                    continue
                text = f"💌 时间胶囊到日子了：这是你 {written:%Y-%m-%d}（{days} 天前）写下的\n\n{c['content']}"
                await send(text)
                self.db.add_message("event", f"他 {days} 天前（{written:%Y-%m-%d}）埋的时间胶囊今天打开了，里面写着：{c['content']}")
                self.db.open_capsule(c["id"])
                await self._send_proactive(f"你们的时间胶囊今天打开了，是他 {days} 天前写的：「{c['content'][:300]}」。"
                                           "认真读完，说说你的感受：那时候的你们、现在的你们，有什么变了、什么没变")
            except Exception as e:  # noqa: BLE001
                log.warning("打开时间胶囊失败：%s", e)
                return

    # ---------------- 每周一：真实聊天的质量周报（给你看的，不是她说的话） ----------------
    async def _weekly_report(self, now: datetime):
        c = dict(self.cfg.get("quality") or {})
        if not c.get("weekly_report", True) or now.weekday() != 0 or now.hour < 10 or not self.bot.owner_id:
            return
        key = f"quality_report_{now:%G-W%V}"
        if self.db.get(key):
            return
        self.db.set(key, True)
        from ailife.mind.report import weekly_report
        import asyncio
        text, cur, _ = await asyncio.to_thread(weekly_report, self.brain, True, True)
        if not cur.get("你发的消息") and not cur.get("主动找你"):
            return
        from ailife.chat.common import ui
        m, _ = await self.bot.owner_text(ui(text))
        if not m:
            log.warning("周报没发出去")
