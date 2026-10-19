"""主动行为调度：早安晚安、想你了随机找你、到点提醒、写日记、安排她自己的一天、
节日、每周来信、手机信号、数据库备份。

什么时候找你：泊松概率 × 她的想念值 × 你在这个钟点的回复习惯（从历史里学）。
找你聊什么：从“话题池”里按权重抽——没聊完的话题、她今天的小插曲、你电脑上刚发生的事、天气……
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta

from ailife.proactive.events import EventsMixin
from ailife.core.timeutil import (GENERIC_REASONS, festival_today, fire_probability, hour_factor,  # noqa: F401
                            in_quiet, parse_range)

log = logging.getLogger("ailife.proactive")


class Scheduler(EventsMixin):
    def __init__(self, cfg, brain, bot):
        self.cfg, self.brain, self.bot = cfg, brain, bot
        self.db = brain.db
        self._task: asyncio.Task | None = None
        self.on_wake: list = []                      # 电脑从睡眠醒来时要做的事（重连隧道等）

    def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        await asyncio.sleep(5)
        gap = 0.0
        while True:
            if gap > 10 * 60:                   # 电脑睡眠 / 休眠后醒来
                log.info("电脑好像刚从睡眠中醒来（停了 %.0f 分钟），重新同步一下", gap / 60)
                self.brain.weather_info = None
                self.db.set("pro_last_check", time.time())          # 醒来别马上连发主动消息
                for cb in list(self.on_wake):
                    try:
                        await cb(gap)
                    except Exception as e:  # noqa: BLE001
                        log.warning("醒来后的恢复出错：%s", e)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("调度出错：%s", e)
            # 提醒需要准时，所以每分钟醒一次；随机主动按 check_every_minutes 节奏判断
            # 只量 sleep 本身：tick 跑得慢（半夜整理记忆）不算“睡眠”
            t0 = time.time()
            await asyncio.sleep(60)
            gap = time.time() - t0

    # ------------------------------------------------------------
    async def tick(self, now: datetime | None = None):
        now = now or self.brain.now()
        self.brain.away.check()                         # 记一下“还活着”；隔了很久（关机 / 睡眠）就准备回来后补上
        if self.bot.owner_id:
            await self._reminders(now)                  # 提醒最要紧：先发，不等半夜整理那些慢活
        if hasattr(self.bot, "spawn") and not getattr(self, "sync_housekeeping", False):
            t = getattr(self, "_house_task", None)
            if t is None or t.done():                   # 整理记忆、写日记要调好几次大模型：放后台做，不卡住调度
                self._house_task = self.bot.spawn(self._daily_housekeeping(now))
        else:
            await self._daily_housekeeping(now)
        await self._first_run_extras(now)
        if not self.bot.owner_id:
            return
        self._expression_feedback()
        try:
            await self.brain.away.tick(self, now)
        except Exception as e:  # noqa: BLE001
            log.warning("回来后补消息出错：%s", e)
        # 打卡提醒是他自己定的时间（可能在深夜），纪念册是一个月一次的事：都不受主动开关 / 安静时段影响
        for fn in (self._habits, self._monthly_recap, self._capsules, self._weekly_report):
            try:
                await fn(now)
            except Exception as e:  # noqa: BLE001
                log.warning("%s 出错：%s", fn.__name__, e)
        p = self.cfg.proactive
        if not p.enabled:
            return
        if in_quiet(now, p.quiet_hours):
            return
        await self._greetings(now)
        for fn in (self._special_dates, self._festival, self._weekly_letter, self._shows, self._weather_alert):
            try:
                await fn(now)
            except Exception as e:  # noqa: BLE001
                log.warning("%s 出错：%s", fn.__name__, e)
        try:
            await self._heartbeat(now)
        except Exception as e:  # noqa: BLE001
            log.warning("内心独白出错：%s", e)
        step = float(p.check_every_minutes)
        last_check = self.db.get("pro_last_check", 0)
        if time.time() - last_check < step * 60 - 5:
            return
        self.db.set("pro_last_check", time.time())
        await self._random_reach_out(now, step)

    async def _daily_housekeeping(self, now: datetime):
        today = now.strftime("%Y-%m-%d")
        if self.db.get("house_day") == today:
            return
        self.db.set("house_day", today)
        try:
            dst = self.db.backup(self.cfg.root / "data" / "backup", keep=7)
            log.info("数据库已备份：%s", dst.name)
            from ailife.core.health import extra_backup_dir
            extra = extra_backup_dir(self.cfg)
            if extra:                            # 再复制一份到别的地方（默认 OneDrive），硬盘坏了也不丢
                try:
                    d2 = self.db.backup(extra, keep=7)
                    self.db.set("backup_extra_last", f"{d2.parent} {today}")
                    log.info("异地备份：%s", d2)
                except Exception as e:  # noqa: BLE001
                    log.warning("异地备份失败：%s", e)
        except Exception as e:  # noqa: BLE001
            log.warning("备份失败：%s", e)
        try:
            await self.brain.memory.maybe_extract(force=True)     # 先整理，日记和日程才能用上
        except Exception as e:  # noqa: BLE001
            log.warning("整理记忆失败：%s", e)
        yesterday = now - timedelta(days=1)
        story = self.brain.story
        await self.brain.memory.write_diary(yesterday, self.brain.emotion.describe(),
                                            "；".join(story.today_lines(yesterday.date())))
        fest = festival_today(now.date())
        try:
            await story.daily(now.date(), fest)
        except Exception as e:  # noqa: BLE001
            log.warning("故事线整理失败：%s", e)
        try:
            await self.brain.herworld.daily(now.date())          # 身边的人、长期目标
        except Exception as e:  # noqa: BLE001
            log.warning("整理她的世界失败：%s", e)
        lines = story.today_lines(now.date())
        rh = self.brain.rhythm.plan_hint() if getattr(self.brain, "rhythm", None) else ""
        hw = self.brain.herworld
        for g in self.db.goals("active"):
            _, _, step = hw.progress(g, now.date())
            if step:
                lines.append(f"目标「{g['title']}」今天：{step['text']}")
        extra = rh + (f"今天是{fest}。" if fest else "") + (
            "你自己最近在经历的事（今天的安排里要体现今天的进展）：" + "；".join(lines) if lines else "")
        await self.brain.memory.plan_life(now, extra)
        if self.brain.mind.profile_due():
            await self.brain.mind.update_profile()
        for sh in self.db.active_shows():              # 刷新番剧信息（集数、每集简介）
            await self.brain.media.enrich_show(sh)
        self.db.expire_threads(14)
        # 为今天随机抽早安/晚安的时间点
        for key in ("morning", "night"):
            r = parse_range(self.cfg.proactive.get(key), now)
            if r:
                t = r[0] + (r[1] - r[0]) * random.random()
                self.db.set(f"{key}_at_{today}", t.timestamp())
        self.db.set("calls_" + today, 0)
        await self.brain.away.refresh_bye_lines(today)      # 提前写好今天的道别（关机时来不及等大模型）

    async def _reminders(self, now: datetime):
        for r in self.db.due_reminders(time.time()):
            self.db.finish_reminder(r["id"])
            late = time.time() - r["due_ts"]
            if late > 6 * 3600:          # 电脑关机错过太久：回来时和“这段时间在干嘛”一起说
                when = datetime.fromtimestamp(r["due_ts"], self.cfg.tz)
                self.brain.away.add_missed(f"本来 {when:%m-%d %H:%M} 要提醒他：{r['content']}")
                continue
            reason = f"到点提醒他：{r['content']}"
            async with self.bot._lock:
                p = await self.brain.proactive(reason)
                if p.skip or not p.bubbles:
                    continue
                await self.bot.deliver(p, kind="proactive")

    async def _greetings(self, now: datetime):
        today = now.strftime("%Y-%m-%d")
        for key, reason in (("morning", "早上了，跟他说早安"),
                            ("night", "很晚了，跟他道晚安、催他早点睡")):
            at = self.db.get(f"{key}_at_{today}")
            r = parse_range(self.cfg.proactive.get(key), now.replace(hour=0, minute=0))
            if at and r and not (r[0].timestamp() <= at <= r[1].timestamp()):
                at = r[0].timestamp() + (r[1] - r[0]).total_seconds() * random.random()   # 改过时间段，按新的重排
                self.db.set(f"{key}_at_{today}", at)
            elif not r:
                continue
            done = self.db.get(f"{key}_done_{today}")
            if not at or done or time.time() < at:
                continue
            self.db.set(f"{key}_done_{today}", True)
            # 晚安：如果他刚刚还在聊，就自然融入聊天，不单独发
            last_user = self.db.get("last_user_ts", 0)
            if key == "night" and time.time() - last_user < 20 * 60:
                continue
            if int(self.db.get("unanswered", 0)) >= self.max_unanswered() + 1:
                continue
            if key == "morning":
                reason = await self.morning_reason("早上了，跟他说早安")
            await self._send_proactive(reason)

    async def _random_reach_out(self, now: datetime, step: float):
        p = self.cfg.proactive
        last_user = self.db.get("last_user_ts", 0) or 0
        last_pro = self.db.get("last_proactive_ts", 0) or 0
        if time.time() - last_user < float(p.active_window_minutes) * 60:
            return
        if time.time() - last_pro < float(p.min_gap_minutes) * 60:
            return
        unanswered = int(self.db.get("unanswered", 0))
        if unanswered >= self.max_unanswered():
            return
        if self.today_count(now) >= int(p.get("max_per_day", 8)):
            return
        hours = (time.time() - last_user) / 3600 if last_user else 24
        mood_hint = {
            0: "",
            1: "（你上一条主动消息他还没回）",
            2: "（你已经连着找了他两次都没回，按你的性格闹点小情绪，比如小小抱怨“已读不回是吧”，但别凶）",
        }.get(unanswered, "（你找了他好几次都没回，收起脾气，开始有点担心他是不是太忙或出了什么事，简短地问一句就好）")
        try:
            hf = hour_factor(self.db.proactive_history(60), self.db.user_message_times(60), now.hour, self.cfg.tz)
        except Exception:  # noqa: BLE001
            hf = 1.0
        factor = self.brain.emotion.longing_factor() * hf * self.responsiveness()[0]
        if self.brain.mind.enabled:
            factor *= 0.5                       # 大部分主动找他交给“内心独白”，随机的少一点
        if getattr(self.brain, "rhythm", None) and self.brain.rhythm.busy_now(now):
            factor *= 0.3                       # 按他的固定安排，这会儿在上课/上班
        prob = fire_probability(p, hours, step, factor) * (0.5 ** unanswered)
        if random.random() >= prob:
            return
        if hours > 48:
            reason, on_use = f"他已经 {hours / 24:.0f} 天没来找你了，你很想他，有点担心", None
        elif hours > 12 and random.random() < 0.5:
            reason, on_use = "有一阵子没聊了，你想他了", None
        else:
            reason, on_use = self.pick_topic(now)
        th = [t for t in self.db.thoughts(1) if time.time() - t["ts"] < 3 * 3600]
        if th:
            reason += f"（你刚才心里在想：{th[-1]['thought'][:80]}）"
        sent = await self._send_proactive(reason + mood_hint)
        if sent and on_use:
            on_use()

    # ---------------- 按他的回复情况自适应 ----------------
    def responsiveness(self) -> tuple[float, float, int]:
        """最近两周她主动找他、他 6 小时内回了的比例。返回 (频率系数, 回复率, 样本数)。
        回得越少她越克制；样本少时接近 1，不乱跳。"""
        rows = [r for r in self.db.proactive_history(14) if time.time() - r["ts"] > 6 * 3600]
        n = len(rows)
        r = sum(1 for x in rows if x["replied_ts"])
        rate = (r + 1) / (n + 2)
        factor = max(0.3, min(1.2, rate * 1.6))
        return factor, rate, n

    def max_unanswered(self) -> int:
        base = int(self.cfg.proactive.max_unanswered)
        _, rate, n = self.responsiveness()
        if n >= 5 and rate < 0.3:
            return min(base, 2)                 # 他最近很少回：最多连找 2 次就停
        return base

    def today_count(self, now: datetime) -> int:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return sum(1 for r in self.db.proactive_history(2) if r["ts"] >= start)

    # ---------------- 话题池 ----------------
    def topic_pool(self, now: datetime) -> list[tuple[float, str, object]]:
        pool: list[tuple[float, str, object]] = []
        for t in self.db.open_threads(5):
            if not t["last_used"] or time.time() - t["last_used"] > 24 * 3600:
                pool.append((3.0, f"接着聊你们之前没聊完的事：{t['topic']}",
                             (lambda tid=t["id"]: self.db.use_thread(tid))))
        lkey = f"life_shared_{now:%Y-%m-%d}"
        if self.db.get(f"life_{now:%Y-%m-%d}") and not self.db.get(lkey):     # 今天的小插曲只分享一次
            pool.append((2.0, "分享你今天安排里正在发生或刚发生的一个小插曲（挑一件，之前没提过的）",
                         (lambda: self.db.set(lkey, True))))
        w = self.brain.weather_info
        if w and w.get("text"):
            pool.append((1.0, f"聊聊他那边的天气（{w['text'][:40]}）", None))
        if self.db.unseen_notes():
            pool.append((5.0, "他在你们小屋的留言板上给你写了话，你看到了，回应他", None))
        wt = self.brain.moments.wish_topic()
        if wt:
            pool.append((0.8, wt[1], (lambda k=wt[0]: self.db.set(k, time.time()))))
        for key, topic in self.brain.herworld.milestone_topics(now.date()):
            pool.append((3.0, topic, (lambda k=key: self.db.set(k, True))))
        if self.brain.together.care_state()["care"]:
            pool.append((4.0, "他这几天状态不太好，轻轻地问问他今天怎么样（别提“你最近是不是不开心”这种直白的话）", None))
        for r in GENERIC_REASONS:
            pool.append((0.6, r, None))
        return pool

    def pick_topic(self, now: datetime) -> tuple[str, object]:
        pool = self.topic_pool(now)
        total = sum(w for w, _, _ in pool)
        x = random.random() * total
        for w, reason, on_use in pool:
            x -= w
            if x <= 0:
                return reason, on_use
        return pool[-1][1], pool[-1][2]

    async def _send_proactive(self, reason: str, note_reply: bool = False) -> bool:
        async with self.bot._lock:          # 不和正在进行的回复抢着发
            p = await self.brain.proactive(reason)
            if p.skip or not p.bubbles:
                log.info("主动消息：她决定先不打扰（%s）", reason)
                return False
            sent = await self.bot.deliver(p, kind="proactive")
        if sent:
            if int(self.db.get("unanswered", 0)) > 0:
                self.brain.emotion.on_ignored()      # 上一条还没回，又忍不住找他
            self.db.set("last_proactive_ts", time.time())
            self.db.set("unanswered", int(self.db.get("unanswered", 0)) + 1)
            self.db.log_proactive(reason)
            if self.db.unseen_notes():
                self.db.mark_notes_seen()
            if note_reply:
                txt = "\n".join(b.text for b in p.bubbles if b.text)
                if txt:
                    self.db.add_note("her", txt)
        return bool(sent)

    # ---------------- 学来的说法：看他的反应调权重 ----------------
    def _expression_feedback(self):
        pend = self.db.get("expr_pending")
        if not pend or time.time() - pend.get("ts", 0) < 3600:
            return
        self.db.set("expr_pending", None)          # 一小时没回：这次用得不太成功
        for e in self.db.expressions("approved"):
            if e["id"] in pend.get("ids", []):
                w = max(0.0, e["weight"] - 0.05)
                self.db.set_expression(e["id"], weight=w,
                                       **({"status": "rejected"} if w < 0.3 and e["uses"] >= 3 else {}))

    # ---------------- 手机传来的状态 ----------------
    async def on_phone(self, ev: str):
        now = self.brain.now()
        if getattr(self.brain, "rhythm", None):
            self.brain.rhythm.note_phone_event(ev)          # 学他平时几点到公司、下班、到家
        today = now.strftime("%Y-%m-%d")
        if not self.bot.owner_id or not self.cfg.proactive.enabled:
            return
        if ev == "刚起床":
            if not self.db.get(f"morning_done_{today}") and 5 <= now.hour < 14:
                self.db.set(f"morning_done_{today}", True)
                await self._send_proactive(await self.morning_reason("他刚起床（手机闹钟刚关），跟他说早安"))
            return
        if in_quiet(now, self.cfg.proactive.quiet_hours):
            return
        if ev.startswith("准备睡了"):
            if not self.db.get(f"night_done_{today}") and (now.hour >= 21 or now.hour < 3):
                self.db.set(f"night_done_{today}", True)
                await self._send_proactive("他准备睡了（手机开始充电），跟他道晚安")
            return
        if ev == "手机快没电了":
            bat = self.db.get("phone_battery") or {}
            pct = f"（只剩 {bat['pct']}%）" if bat.get("pct") else ""
            if int(self.db.get("unanswered", 0)) < 2:
                await self._send_proactive(f"他手机快没电了{pct}，用你的方式提醒他去充电（短一点）")
            return
        if ev in ("刚到家", "下班了", "到公司/学校了", "刚出门"):
            last_pro = self.db.get("last_proactive_ts", 0) or 0
            if time.time() - last_pro > 30 * 60 and int(self.db.get("unanswered", 0)) < 2 and random.random() < 0.7:
                await self._send_proactive(f"你感觉到他{ev}，自然地关心一句")

    async def _first_run_extras(self, now: datetime):
        """刚升级、还没有故事线 / 对他的了解时，不用等到半夜，启动后就补上（一天只试一次）。"""
        key = f"extras_try_{now:%Y-%m-%d}"
        if self.db.get(key):
            return
        self.db.set(key, True)

        async def run():
            try:
                hw = self.brain.herworld
                if hw.enabled:                          # 先定好她身边的人，故事线才能用上这些名字
                    await hw.ensure_cast()
                    if not self.db.goals(None, 1):
                        await hw.new_goal(now.date())
                if self.brain.story.enabled and not self.db.arcs(None, 1):
                    await self.brain.story.daily(now.date(), festival_today(now.date()))
                if self.brain.mind.profile_due():
                    await self.brain.mind.update_profile()
                for sh in self.db.active_shows():
                    await self.brain.media.enrich_show(sh)
            except Exception as e:  # noqa: BLE001
                log.warning("补全故事线/了解失败：%s", e)
        if hasattr(self.bot, "spawn"):
            self.bot.spawn(run())

    # ---------------- 内心独白（心跳） ----------------
    async def _heartbeat(self, now: datetime):
        mind = self.brain.mind
        if not mind.due():
            return
        if time.time() - (self.db.get("last_user_ts", 0) or 0) < 20 * 60:
            return                              # 正在聊天，不用“一个人想”
        th = await mind.think()
        if not th or not th.get("contact"):
            return
        p = self.cfg.proactive
        if time.time() - (self.db.get("last_proactive_ts", 0) or 0) < float(p.min_gap_minutes) * 60:
            return
        if int(self.db.get("unanswered", 0)) >= self.max_unanswered():
            return
        if self.today_count(now) >= int(p.get("max_per_day", 8)):
            return
        reason = (f"你刚才心里在想：{th['thought'][:150]}。你决定主动找他（{th.get('why', '')[:60]}）"
                  f"，想说的大意：{str(th.get('say') or '')[:80]}")
        if await self._send_proactive(reason):
            self.db.mark_thought_sent(th["id"])

    # ---------------- 晨间小报 ----------------
    async def morning_reason(self, base: str) -> str:
        try:
            brief = await self.brain.together.morning_brief()
        except Exception as e:  # noqa: BLE001
            log.info("晨间小报没生成：%s", e)
            brief = ""
        if not brief:
            return base
        return (f"{base}。顺便像随口一提那样告诉他今天要注意的一两件事（挑最重要的，别像念报告，"
                f"可以拆成两条发）：{brief}")


