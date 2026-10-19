"""命令：在 QQ 私聊里发 /status、/habit、/diag……（也可以用中文：/日记 /心情 /打卡）。"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from ailife.chat.command import Command
from ailife.chat.common import HELP_TEXT, keep, stats_line

log = logging.getLogger("ailife.bot")


class CommandsMixin:
    # ---------------- 命令 ----------------
    async def cmd_memory(self, c: Command):
        mems = self.db.all_memories()
        if not mems:
            await c.reply("（她还没记住什么）")
            return
        lines = [f"{m['id']}. {keep(m['content'])}（{m['importance']}）" for m in mems[-60:]]
        await c.reply("\n".join(lines)[:4000] + "\n\n删除某条：/forget 编号")

    async def cmd_forget(self, c: Command):
        for a in c.args or []:
            if a.isdigit():
                self.db.delete_memory(int(a))
        await c.reply("好，已经删掉了。")

    async def cmd_diary(self, c: Command):
        ds = self.db.recent_diary(3)
        await c.reply(
            "\n\n".join(f"【{d['day']}】\n{keep(d['content'])}" for d in ds) or "（还没有日记）")

    async def cmd_status(self, c: Command):
        rs = self.db.pending_reminders()
        await c.reply(
            f"心情：{self.brain.mood}（{self.brain.emotion.describe()}）\n好感：💗 满（永远）\n"
            f"记忆：{len(self.db.all_memories())} 条 · 贴纸库：{self.db.sticker_count()} 张\n"
            f"未回复的主动消息：{self.db.get('unanswered', 0)}\n"
            f"待提醒：{len(rs)} 条\n"
            f"今天的安排：\n{keep(self.db.get(f'life_{self.brain.now():%Y-%m-%d}', '（未生成）'))}"
        )

    async def cmd_help(self, c: Command):
        await c.reply(HELP_TEXT)

    async def cmd_city(self, c: Command):
        city = " ".join(c.args or []).strip()
        if not city:
            await c.reply(f"当前城市：{self.brain.weather.city or '未设置'}\n用法：/city 上海")
            return
        self.db.set("city", city)
        w = await self.brain.weather.fetch(force=True)
        if w:
            await c.reply(f"好，她现在知道你在{w['city']}了。\n{w['text']}")
        else:
            await c.reply("天气没查到，城市名换个写法试试（比如“上海”“Shanghai”）。")

    async def cmd_schedule(self, c: Command):
        text = c.text.partition(" ")[2].strip()
        if not text:
            cur = self.db.get("schedule") or "未设置"
            rh = self.brain.rhythm
            rs = rh.routines()
            learned = rh.learned_times()
            extra = ""
            if rs:
                extra += "\n\n她从聊天里记下的固定安排：\n" + "\n".join(f"{i + 1}. {r['when']} {r['what']}" for i, r in enumerate(rs))
                extra += "\n（删掉一条：/schedule 删 编号）"
            if learned:
                extra += "\n\n从手机信号学到的：" + "，".join(f"{v} {k}" for k, v in learned.items())
            await c.reply(
                f"当前作息：{cur}{extra}\n\n用法：/schedule 工作日 9-18 上班，周二周四晚上有课，一般 1 点睡、10 点起\n清空：/schedule 无")
            return
        m = re.fullmatch(r"(?:删|del)\s*(\d+)", text)
        if m:
            self.brain.rhythm.del_routine(int(m.group(1)) - 1)
            await c.reply("删掉了。")
            return
        self.db.set("schedule", "" if text == "无" else text)
        await c.reply("记住了，她会按你的作息来找你。")

    async def cmd_dates(self, c: Command):
        from ailife.mind.output import normalize_date
        args = c.args or []
        if len(args) >= 2 and args[0] == "删" and args[1].isdigit():
            self.db.delete_date(int(args[1]))
            await c.reply("删掉了。")
            return
        if len(args) >= 2:
            nd = normalize_date(args[0])
            if not nd:
                await c.reply("日期格式：11-20（每年）或 2026-12-20（一次性）")
                return
            name = " ".join(args[1:])
            kind = "birthday" if "生日" in name else "anniversary" if len(nd) == 5 else "event"
            self.db.add_date(nd, name, kind, 3 if kind != "event" else 1)
            await c.reply(f"记下了：{nd} {name}")
            return
        ds = self.db.all_dates()
        lines = [f"{d['id']}. {d['date']} {d['name']}" for d in ds] or ["（还没有）"]
        await c.reply("\n".join(lines) + "\n\n添加：/dates 11-20 在一起纪念日\n删除：/dates 删 编号")

    async def cmd_shows(self, c: Command):
        from ailife.mind.output import parse_show_time
        text = c.text.partition(" ")[2].strip()
        if text.startswith("删"):
            n = text[1:].strip()
            if n.isdigit():
                self.db.delete_show(int(n))
                await c.reply("不追了。")
            return
        if text:
            parts = text.rsplit(" ", 2)
            st = parse_show_time(" ".join(parts[-2:])) if len(parts) >= 3 else None
            if not st:
                await c.reply("用法：/shows 葬送的芙莉莲 周五 23:00")
                return
            sid = self.db.add_show(parts[0].strip("《》"), *st)
            self.brain._enrich_later(sid)
            await c.reply(f"好，每周{'一二三四五六日'[st[0]]} {st[1]} 更新后她会来找你聊。"
                                            "（会去 Bangumi 查集数和每集内容）")
            return
        ss = self.db.active_shows()
        lines = [f"{x['id']}. 《{x['name']}》周{'一二三四五六日'[x['weekday']]} {x['time']}，出到第 {x['episode']} 集"
                 + (f"/共 {x['total_eps']} 集" if x["total_eps"] else "")
                 for x in ss] or ["（还没有）"]
        await c.reply("\n".join(lines) + "\n\n添加：/shows 番名 周五 23:00\n删除：/shows 删 编号")

    async def cmd_photo(self, c: Command):
        self.db.add_message("event", f"他想看你现在的照片（今天你还能发 {self.brain.photos.remaining_today()} 张）")
        await self.schedule_reply(0.5)

    async def cmd_letter(self, c: Command):
        ls = self.db.letters(1)
        await c.reply(f"💌 {ls[0]['week']}\n\n{keep(ls[0]['content'])}" if ls else "（还没有信，周日晚上她会写）")

    async def cmd_note(self, c: Command):
        text = c.text.partition(" ")[2].strip()
        if not text:
            await c.reply("用法：/note 想对她说的悄悄话（会贴在小屋的留言板上）")
            return
        self.db.add_note("user", text[:500])
        await c.reply("贴到留言板上了～")
        self.spawn(self.on_note(text))

    # ---------------- 一起打卡 ----------------
    async def cmd_habit(self, c: Command):
        """/habit  看打卡；/habit add 早睡 23:30；/habit del 编号"""
        args = c.args or []
        if args and args[0] in ("add", "加", "新"):
            if len(args) < 2:
                await c.reply("用法：/habit add 早睡 23:30（时间可以不写；到点没打卡她会来催）")
                return
            from ailife.mind.together import norm_hhmm
            looks_time = len(args) >= 3 and re.fullmatch(r"\d{1,2}[:：]\d{1,2}", args[-1])
            t = norm_hhmm(args[-1]) if looks_time else None
            if looks_time and not t:
                await c.reply("时间不对哦，写成 23:30 这样（0:00～23:59）")
                return
            name = " ".join(args[1:-1] if t else args[1:])[:20]
            self.db.add_habit(name, t)
            self.db.add_message("event", f"他和你约好了一个打卡目标：{name}" + (f"（每天 {t}）" if t else "") + "，你负责监督他")
            await c.reply(f"好，「{name}」加上了。" + (f"每天 {t} 还没打卡的话她会来催。" if t else ""))
            await self.schedule_reply(1.0)
            return
        if args and args[0] in ("del", "删") and len(args) > 1 and args[1].isdigit():
            self.db.del_habit(int(args[1]))
            await c.reply("删掉了。")
            return
        if args and (args[0].isdigit() or (args[0] in ("打卡", "done", "完成") and len(args) > 1 and args[1].isdigit())):
            await self._checkin(int(args[0] if args[0].isdigit() else args[1]), c)
            return
        hs = self.db.habits()
        if not hs:
            await c.reply("还没有打卡目标。比如：/habit add 早睡 23:30　/habit add 背单词 21:00")
            return
        from ailife.mind.together import habit_day
        day = habit_day(self.brain.now())
        lines = [("✅ " if self.db.checked(h["id"], day) else "⬜ ") + f"{h['id']}. {h['name']}"
                 + (f"（{h['remind_time']}）" if h["remind_time"] else "")
                 + f"　连续 {self.db.streak(h['id'], day)} 天" for h in hs]
        await c.reply("\n".join(lines) + "\n\n打卡：/habit 编号（或 /打卡 编号）；删除：/habit del 编号")

    async def _checkin(self, hid: int, c: Command):
        h = next((x for x in self.db.habits() if x["id"] == hid), None)
        if not h:
            await c.reply("没有这个编号的打卡目标（发 /habit 看看）。")
            return
        new, streak = self.brain.together.do_checkin(hid)
        await c.reply(f"✅ {h['name']} 打卡成功！连续 {streak} 天" if new else f"「{h['name']}」今天已经打过卡啦")
        if new:
            self.db.add_message("event", f"他完成了打卡：{h['name']}（已经连续 {streak} 天）")
            await self.schedule_reply(1.5)

    async def cmd_home(self, c: Command):
        port = int((self.cfg.get("web") or {}).get("port", 8765))
        await c.reply(f"小屋在 http://127.0.0.1:{port}\n"
                      "她跑在服务器上时：在你电脑上双击“打开小屋”（scripts 里的 open_home 脚本），浏览器会自动打开。")

    async def cmd_sense(self, c: Command):
        """/sense：手机信号（起床 / 到家 / 充电）的设置。"""
        ph = self.db.get("sense_phone") or {}
        if not (self.phone and self.phone.enabled):
            await c.reply("手机信号没开（config.yaml 的 sense.phone.enabled）。")
            return
        lines = [f"手机信号频道（ntfy）：{self.phone.url}",
                 "在快捷指令 / MacroDroid 里设置：闹钟关掉、到家、开始充电时，用“获取 URL 内容”向这个地址 POST 一句话，"
                 "比如“起床”“到家”“充电”。"]
        if ph:
            lines.append(f"最近一次：{ph.get('event')}")
        await c.reply("\n\n".join(lines))

    async def cmd_recap(self, c: Command):
        rs = self.db.recaps(1)
        now = self.brain.now()
        if rs and (c.args or [""])[0] not in ("本月", "这个月", "now"):
            r = rs[0]
            await c.reply(f"📒 {r['month']}\n\n{keep(r['content'])}\n\n{stats_line(r['stats'])}")
            return
        await c.reply("她在翻这个月的聊天记录和日记，稍等…")

        async def job():                    # 后台写，不耽误这期间聊天
            res = await self.brain.together.monthly_recap(now.year, now.month, save=False)
            await c.reply(f"📒 这个月到现在\n\n{keep(res[0])}\n\n{stats_line(res[1])}" if res else "这个月还没什么可写的。")
        self.spawn(job())

    async def cmd_diag(self, c: Command):
        await c.reply("正在检查各个部件，大约半分钟（这期间照常聊天）…")
        from ailife.core.health import diagnose

        async def job():
            try:
                text = await diagnose(self.cfg, self.brain, self, self.dash)
            except Exception as e:  # noqa: BLE001
                text = f"自检本身出错了：{e}"
            await c.reply(text[:4000])
        self.spawn(job())

    async def cmd_watched(self, c: Command):
        """/watched 番名 [集数]：告诉她你看完了，她会带着这一集的内容和你聊。"""
        args = (c.text.partition(" ")[2].strip()).rsplit(" ", 1)
        ss = self.db.active_shows()
        if not args[0] or not ss:
            await c.reply("用法：/watched 番名 [集数]（番要先在 /shows 里）")
            return
        name = args[0].strip("《》")
        row = next((s for s in ss if name in s["name"] or s["name"] in name
                    or (s["name_cn"] and name in s["name_cn"])), None)
        if not row:
            await c.reply("没找到这部番，先用 /shows 加上。")
            return
        ep = int(args[1]) if len(args) > 1 and args[1].isdigit() else row["episode"]
        info = await self.brain.media.episode(row, ep) if ep else None
        about = f"这集叫「{info['title']}」，内容：{info['desc'][:200]}" if info and (info["title"] or info["desc"]) else ""
        self.user_said(c, f"[我看完了《{row['name']}》第 {ep} 集]", "text")
        self.db.add_message("event", f"他看完了《{row['name']}》第 {ep} 集。你也看过了。{about}（和他聊聊剧情、角色、你的感受）")
        await self.schedule_reply(0.5)

    async def cmd_song(self, c: Command):
        want = c.text.partition(" ")[2].strip()
        self.user_said(c, f"[想让你推荐一首歌{('：' + want) if want else ''}]", "text")
        self.db.add_message("event", "他想让你给他推荐一首歌。结合他的口味、此刻的心情和时间，挑一首真实存在的歌，"
                                     "用 [推歌:歌名|歌手] 发给他，再说说为什么想推这首。")
        await self.schedule_reply(0.5)

    async def cmd_profile(self, c: Command):
        p = self.db.latest_profile()
        force = c.text.strip().endswith("更新")
        if force or (not p and len(self.db.all_memories("user")) >= 3):
            await c.reply("她在整理，稍等…")
            await self.brain.mind.update_profile()
            p = self.db.latest_profile()
        await c.reply(f"她心里的你：\n\n{p['content']}" if p else "她对你了解得还不够多，多聊聊吧。")

    async def cmd_story(self, c: Command):
        today = self.brain.now().date()
        lines = [keep(f"「{a['title']}」{'（已结束）' if a['status'] == 'done' else ''}\n" +
                      "\n".join(f"  {b['day'][5:]} {b['text']}" for b in a["beats"]))
                 for a in self.brain.story.for_web(today)[:3]]
        await c.reply("\n\n".join(lines) if lines else "她最近没什么特别的事（每天凌晨会安排）。")

    async def cmd_mind(self, c: Command):
        ts = self.db.thoughts(3)
        await c.reply("\n\n".join(
            f"{datetime.fromtimestamp(t['ts'], self.cfg.tz):%m-%d %H:%M}  {keep(t['thought'])}" for t in ts)
            if ts else "她还没有一个人想过事情（清醒时每隔几小时会想一次）。")
