"""Telegram 命令（/status、/habit、/diag……）和按钮回调。"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .voice import to_ogg_opus
from .bot_common import HELP_TEXT, keep, stats_line

log = logging.getLogger("ailife.bot")


class CommandsMixin:
    # ---------------- 命令 ----------------
    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if not self.owner_id:
            self.db.set("owner_id", uid)
            log.info("主人已绑定：%s", uid)
        if not self._is_owner(update):
            return
        await update.message.reply_text(f"已连接。你的 TG ID 是 {uid}。\n\n" + HELP_TEXT)
        if not self.db.last_message():
            greet = getattr(self.brain.persona, "greeting", "")
            if greet:                                   # 人设里写好的开场白
                m = await self.app.bot.send_message(uid, greet)
                self.db.add_message("event", "他第一次打开和你的聊天窗口")
                self.db.add_message("assistant", greet, "text", m.message_id)
            else:
                self.db.add_message("event", "他第一次打开和你的聊天窗口")
                await self.schedule_reply(0.5)

    async def cmd_memory(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        mems = self.db.all_memories()
        if not mems:
            await update.message.reply_text("（她还没记住什么）")
            return
        lines = [f"{m['id']}. {keep(m['content'])}（{m['importance']}）" for m in mems[-60:]]
        await update.message.reply_text("\n".join(lines)[:4000] + "\n\n删除某条：/forget 编号")

    async def cmd_forget(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        for a in ctx.args or []:
            if a.isdigit():
                self.db.delete_memory(int(a))
        await update.message.reply_text("好，已经删掉了。")

    async def cmd_diary(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        ds = self.db.recent_diary(3)
        await update.message.reply_text(
            "\n\n".join(f"【{d['day']}】\n{keep(d['content'])}" for d in ds) or "（还没有日记）")

    async def cmd_status(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        rs = self.db.pending_reminders()
        await update.message.reply_text(
            f"心情：{self.brain.mood}（{self.brain.emotion.describe()}）\n好感：💗 满（永远）\n"
            f"记忆：{len(self.db.all_memories())} 条 · 贴纸库：{self.db.sticker_count()} 张\n"
            f"未回复的主动消息：{self.db.get('unanswered', 0)}\n"
            f"待提醒：{len(rs)} 条\n"
            f"今天的安排：\n{keep(self.db.get(f'life_{self.brain.now():%Y-%m-%d}', '（未生成）'))}"
        )

    async def cmd_call(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        if not self.caller:
            await update.message.reply_text("打电话功能没开（config.yaml 里 call.enabled）。")
            return
        self.spawn(self.caller.call("他让你现在给他打电话", forced=True))

    async def cmd_help(self, update: Update, ctx):
        if self._is_owner(update):
            await update.message.reply_text(HELP_TEXT)

    async def cmd_voices(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        from .voice import VOICES, voice_gender
        cur = self.db.get("tts_voice")
        g = getattr(self.brain.persona, "gender", "female")
        await update.message.reply_text("试听几种声音（生成需要几秒）。喜欢哪个就发 /voice 编号")
        line = self.tts.sample_line()
        for i, (label, *v) in enumerate(VOICES):
            if g in ("female", "male") and voice_gender((label, *v)) != g and v[0] != "sovits":
                continue                            # 只给和角色性别对得上的声音
            try:
                audio = await to_ogg_opus(await self.tts.synth(line, tuple(v)))
                mark = "（当前）" if cur == i else ""
                await self.app.bot.send_voice(self.owner_id, audio, caption=f"{i + 1}. {label}{mark}")
            except Exception as e:  # noqa: BLE001
                await update.message.reply_text(f"{i + 1}. {label} 生成失败：{e}")

    async def cmd_voice(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        from .voice import VOICES
        a = (ctx.args or [""])[0]
        if a.isdigit() and 1 <= int(a) <= len(VOICES):
            self.db.set("tts_voice", int(a) - 1)
            self.tts.set_voice(int(a) - 1)
            await update.message.reply_text(f"好，以后用「{VOICES[int(a) - 1][0]}」的声音。")
        else:
            await update.message.reply_text(f"用法：/voice 1~{len(VOICES)}（先发 /voices 试听）")

    async def cmd_city(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        city = " ".join(ctx.args or []).strip()
        if not city:
            await update.message.reply_text(f"当前城市：{self.brain.weather.city or '未设置'}\n用法：/city 上海")
            return
        self.db.set("city", city)
        w = await self.brain.weather.fetch(force=True)
        if w:
            await update.message.reply_text(f"好，她现在知道你在{w['city']}了。\n{w['text']}")
        else:
            await update.message.reply_text("天气没查到，城市名换个写法试试（比如“上海”“Shanghai”）。")

    async def cmd_schedule(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        text = update.message.text.partition(" ")[2].strip()
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
            await update.message.reply_text(
                f"当前作息：{cur}{extra}\n\n用法：/schedule 工作日 9-18 上班，周二周四晚上有课，一般 1 点睡、10 点起\n清空：/schedule 无")
            return
        m = re.fullmatch(r"(?:删|del)\s*(\d+)", text)
        if m:
            self.brain.rhythm.del_routine(int(m.group(1)) - 1)
            await update.message.reply_text("删掉了。")
            return
        self.db.set("schedule", "" if text == "无" else text)
        await update.message.reply_text("记住了，她会按你的作息来找你。")

    async def cmd_dates(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        from .output import normalize_date
        args = ctx.args or []
        if len(args) >= 2 and args[0] == "删" and args[1].isdigit():
            self.db.delete_date(int(args[1]))
            await update.message.reply_text("删掉了。")
            return
        if len(args) >= 2:
            nd = normalize_date(args[0])
            if not nd:
                await update.message.reply_text("日期格式：11-20（每年）或 2026-12-20（一次性）")
                return
            name = " ".join(args[1:])
            kind = "birthday" if "生日" in name else "anniversary" if len(nd) == 5 else "event"
            self.db.add_date(nd, name, kind, 3 if kind != "event" else 1)
            await update.message.reply_text(f"记下了：{nd} {name}")
            return
        ds = self.db.all_dates()
        lines = [f"{d['id']}. {d['date']} {d['name']}" for d in ds] or ["（还没有）"]
        await update.message.reply_text("\n".join(lines) + "\n\n添加：/dates 11-20 在一起纪念日\n删除：/dates 删 编号")

    async def cmd_shows(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        from .output import parse_show_time
        text = update.message.text.partition(" ")[2].strip()
        if text.startswith("删"):
            n = text[1:].strip()
            if n.isdigit():
                self.db.delete_show(int(n))
                await update.message.reply_text("不追了。")
            return
        if text:
            parts = text.rsplit(" ", 2)
            st = parse_show_time(" ".join(parts[-2:])) if len(parts) >= 3 else None
            if not st:
                await update.message.reply_text("用法：/shows 葬送的芙莉莲 周五 23:00")
                return
            sid = self.db.add_show(parts[0].strip("《》"), *st)
            self.brain._enrich_later(sid)
            await update.message.reply_text(f"好，每周{'一二三四五六日'[st[0]]} {st[1]} 更新后她会来找你聊。"
                                            "（会去 Bangumi 查集数和每集内容）")
            return
        ss = self.db.active_shows()
        lines = [f"{x['id']}. 《{x['name']}》周{'一二三四五六日'[x['weekday']]} {x['time']}，出到第 {x['episode']} 集"
                 + (f"/共 {x['total_eps']} 集" if x["total_eps"] else "")
                 for x in ss] or ["（还没有）"]
        await update.message.reply_text("\n".join(lines) + "\n\n添加：/shows 番名 周五 23:00\n删除：/shows 删 编号")

    async def cmd_game(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭕ 井字棋", callback_data="g:new:ttt"),
             InlineKeyboardButton("✊ 石头剪刀布", callback_data="g:new:rps")],
            [InlineKeyboardButton("🔢 猜数字", callback_data="g:new:guess"),
             InlineKeyboardButton("💬 她来挑（聊天游戏）", callback_data="g:new:chat")]])
        await update.message.reply_text("玩什么？", reply_markup=kb)

    async def _safe_edit(self, q, text, rows=None):
        try:
            await q.edit_message_text(text, reply_markup=self._kb(rows))
        except Exception as e:  # noqa: BLE001  （内容没变时 Telegram 会报错，忽略）
            log.debug("编辑游戏消息失败：%s", e)

    @staticmethod
    def _kb(rows):
        if not rows:
            return None
        return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in r] for r in rows])

    async def on_callback(self, update: Update, ctx):
        q = update.callback_query
        if not q or not self.owner_id or q.from_user.id != self.owner_id:
            return
        try:
            await q.answer()
        except Exception:  # noqa: BLE001
            pass
        parts = q.data.split(":")
        kind = parts[1] if len(parts) > 1 else ""
        arg = parts[-1]
        st = self.games.state
        if kind in ("ttt", "rps") and (len(parts) < 4 or not st or st.get("gid") != parts[2]):
            await self._safe_edit(q, "这局已经结束啦，发 /game 再开一局。")
            return
        names = {"ttt": "井字棋", "rps": "石头剪刀布", "guess": "猜数字"}
        if kind == "new":
            if arg == "chat":
                self.db.add_message("event", "他想和你玩个小游戏，你来挑一个（成语接龙、猜谜、真心话、二选一……），说清规则就开始")
                await self.schedule_reply(0.5)
                return
            text, rows = self.games.start(arg)
            await self._safe_edit(q, text, rows)
            self.db.add_message("event", f"你们开始玩{names.get(arg, arg)}了（输赢由程序判定，你按结果真实地反应）")
            await self.schedule_reply(1.0)
            return
        if kind == "ttt":
            text, rows, ended = self.games.ttt_move(int(arg))
            if ended:
                text += "\n" + ended.split("\n", 1)[-1]          # 结束时把最终棋盘贴出来
            await self._safe_edit(q, text, rows)
        elif kind == "rps":
            text, rows, ended = self.games.rps_play(arg)
            await self._safe_edit(q, text, rows)
        else:
            return
        if ended:
            self.db.add_message("event", ended)
            await self.schedule_reply(1.0)

    async def cmd_photo(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        self.db.add_message("event", f"他想看你现在的照片（今天你还能发 {self.brain.photos.remaining_today()} 张）")
        await self.schedule_reply(0.5)

    async def cmd_home(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        if not self.miniapp_url:
            await update.message.reply_text("手机小屋还没开好（看程序窗口里 Mini App 那行的提示）。电脑上可以打开 "
                                            f"http://127.0.0.1:{int((self.cfg.get('web') or {}).get('port', 8765))}")
            return
        from telegram import WebAppInfo
        rows = [[InlineKeyboardButton(f"🏠 打开{self.brain.persona.name}的小屋", web_app=WebAppInfo(self.miniapp_url))]]
        dash = getattr(self, "dash", None)
        if dash is not None and hasattr(dash, "issue_token"):
            tok = dash.issue_token(12)
            sep = "&" if "?" in self.miniapp_url else "?"
            rows.append([InlineKeyboardButton("🌐 用浏览器打开（12 小时内有效）", url=f"{self.miniapp_url}{sep}t={tok}")])
        kb = InlineKeyboardMarkup(rows)
        port = int((self.cfg.get("web") or {}).get("port", 8765))
        await update.message.reply_text(
            f"点这里进小屋～\n在这台电脑上也可以直接打开 http://127.0.0.1:{port}（不用登录，和手机上看到的是同一份）",
            reply_markup=kb)

    async def cmd_sense(self, update: Update, ctx):
        """/sense 看感知状态；/sense 暂停 [小时] 暂时不让她感觉到你在电脑上做什么；/sense 继续"""
        if not self._is_owner(update):
            return
        args = ctx.args or []
        if args and self.pc:
            if args[0] in ("暂停", "pause", "off", "关"):
                try:
                    hours = float(args[1]) if len(args) > 1 else 2.0
                except ValueError:
                    hours = 2.0
                self.pc.pause(max(0.1, min(hours, 24 * 7)))
                await update.message.reply_text(f"好，接下来 {hours:g} 小时不看你在电脑上做什么（/sense 继续 可以提前恢复）。")
                return
            if args[0] in ("继续", "resume", "on", "开"):
                self.pc.pause(None)
                await update.message.reply_text("电脑感知恢复了。")
                return
        pc = self.db.get("sense_pc") or {}
        ph = self.db.get("sense_phone") or {}
        if self.pc and self.pc.enabled and self.pc.paused():
            left = (float(self.db.get("sense_pause_until") or 0) - time.time()) / 3600
            head = f"电脑感知：暂停中（还有 {left:.1f} 小时，/sense 继续 恢复）"
        else:
            head = f"电脑感知：{'开' if self.pc and self.pc.enabled else '关'}" + (
                f"（{ {'app': '只认程序和网站名', 'content': '加上视频标题 / 项目名 / 文档名', 'full': '完整窗口标题'}.get(self.pc.detail, '')}）"
                if self.pc and self.pc.enabled else "") + (
                f"\n现在：{pc.get('desc')}" if pc and time.time() - pc.get("ts", 0) < 300 else "")
        lines = [head]
        if self.pc:
            today = self.pc.today_summary()
            if today:
                lines.append(f"今天：{today}")
            lines.append("不想被看到：/sense 暂停 2（小时）；某个程序永远不说：config.yaml 的 sense.pc.ignore")
        if self.phone and self.phone.enabled:
            lines.append(f"手机信号频道（ntfy）：{self.phone.url}\n"
                         "在快捷指令/MacroDroid 里设置：闹钟关掉、到家、开始充电时，用“获取 URL 内容”向这个地址 POST 一句话，"
                         "比如“起床”“到家”“充电”。")
            if ph:
                lines.append(f"最近一次：{ph.get('event')}")
        await update.message.reply_text("\n\n".join(lines))

    async def cmd_letter(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        ls = self.db.letters(1)
        await update.message.reply_text(f"💌 {ls[0]['week']}\n\n{keep(ls[0]['content'])}" if ls else "（还没有信，周日晚上她会写）")

    async def cmd_note(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        text = update.message.text.partition(" ")[2].strip()
        if not text:
            await update.message.reply_text("用法：/note 想对她说的悄悄话（会贴在小屋的留言板上）")
            return
        self.db.add_note("user", text[:500])
        await update.message.reply_text("贴到留言板上了～")
        self.spawn(self.on_note(text))

    # ---------------- 一起打卡 ----------------
    def _habit_kb(self):
        from .together import habit_day
        day = habit_day(self.brain.now())
        rows = [[InlineKeyboardButton(("✅ " if self.db.checked(h["id"], day) else "⬜ ") + h["name"],
                                      callback_data=f"h:{h['id']}")] for h in self.db.habits()]
        return InlineKeyboardMarkup(rows) if rows else None

    async def cmd_habit(self, update: Update, ctx):
        """/habit  看打卡；/habit add 早睡 23:30；/habit del 编号"""
        if not self._is_owner(update):
            return
        args = ctx.args or []
        if args and args[0] in ("add", "加", "新"):
            if len(args) < 2:
                await update.message.reply_text("用法：/habit add 早睡 23:30（时间可以不写；到点没打卡她会来催）")
                return
            from .together import norm_hhmm
            looks_time = len(args) >= 3 and re.fullmatch(r"\d{1,2}[:：]\d{1,2}", args[-1])
            t = norm_hhmm(args[-1]) if looks_time else None
            if looks_time and not t:
                await update.message.reply_text("时间不对哦，写成 23:30 这样（0:00～23:59）")
                return
            name = " ".join(args[1:-1] if t else args[1:])[:20]
            self.db.add_habit(name, t)
            self.db.add_message("event", f"他和你约好了一个打卡目标：{name}" + (f"（每天 {t}）" if t else "") + "，你负责监督他")
            await update.message.reply_text(f"好，「{name}」加上了。" + (f"每天 {t} 还没打卡的话她会来催。" if t else ""),
                                            reply_markup=self._habit_kb())
            await self.schedule_reply(1.0)
            return
        if args and args[0] in ("del", "删") and len(args) > 1 and args[1].isdigit():
            self.db.del_habit(int(args[1]))
            await update.message.reply_text("删掉了。")
            return
        hs = self.db.habits()
        if not hs:
            await update.message.reply_text("还没有打卡目标。比如：/habit add 早睡 23:30　/habit add 背单词 21:00")
            return
        from .together import habit_day
        day = habit_day(self.brain.now())
        lines = [f"{h['id']}. {h['name']}" + (f"（{h['remind_time']}）" if h["remind_time"] else "")
                 + f"　连续 {self.db.streak(h['id'], day)} 天" for h in hs]
        await update.message.reply_text("\n".join(lines) + "\n\n点下面的按钮打卡；/habit del 编号 删除",
                                        reply_markup=self._habit_kb())

    async def on_habit_button(self, update: Update, ctx):
        q = update.callback_query
        if not q or not self.owner_id or q.from_user.id != self.owner_id:
            return
        try:
            hid = int(q.data.split(":")[1])
        except (IndexError, ValueError):
            return
        h = next((x for x in self.db.habits() if x["id"] == hid), None)
        if not h:
            await q.answer("这个目标已经删掉了")
            return
        new, streak = self.brain.together.do_checkin(hid)
        await q.answer(f"打卡成功！连续 {streak} 天" if new else "今天已经打过卡啦")
        try:
            await q.edit_message_reply_markup(reply_markup=self._habit_kb())
        except Exception:  # noqa: BLE001
            pass
        if new:
            self.db.add_message("event", f"他完成了打卡：{h['name']}（已经连续 {streak} 天）")
            await self.schedule_reply(1.5)

    async def cmd_recap(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        rs = self.db.recaps(1)
        now = self.brain.now()
        if rs and (ctx.args or [""])[0] not in ("本月", "这个月", "now"):
            r = rs[0]
            await update.message.reply_text(f"📒 {r['month']}\n\n{keep(r['content'])}\n\n{stats_line(r['stats'])}")
            return
        await update.message.reply_text("她在翻这个月的聊天记录和日记，稍等…")
        msg = update.message

        async def job():                    # 后台写，不耽误这期间聊天
            res = await self.brain.together.monthly_recap(now.year, now.month, save=False)
            await msg.reply_text(f"📒 这个月到现在\n\n{keep(res[0])}\n\n{stats_line(res[1])}" if res else "这个月还没什么可写的。")
        self.spawn(job())

    def _voice_degraded(self, label: str):
        self.db.set("tts_voice", None)
        if self.owner_id:
            self.spawn(self.app.bot.send_message(
                self.owner_id, f"⚠️ 「{label}」连续 3 次合成失败，已自动换回本地声音。想换别的发 /voices 试听。"))

    async def cmd_diag(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        await update.message.reply_text("正在检查各个部件，大约半分钟（这期间照常聊天）…")
        from .health import diagnose
        msg = update.message

        async def job():
            try:
                text = await diagnose(self.cfg, self.brain, self, self.dash)
            except Exception as e:  # noqa: BLE001
                text = f"自检本身出错了：{e}"
            await msg.reply_text(text[:4000], disable_web_page_preview=True)
        self.spawn(job())

    async def cmd_autostart(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        from .health import autostart, autostart_on
        arg = (ctx.args or [""])[0]
        if arg in ("on", "开"):
            msg = autostart(self.cfg.root, True)
        elif arg in ("off", "关"):
            msg = autostart(self.cfg.root, False)
        else:
            msg = f"开机自启：{'已开启' if autostart_on() else '没开'}。/autostart 开  或  /autostart 关"
        await update.message.reply_text(msg)

    async def cmd_watched(self, update: Update, ctx):
        """/watched 番名 [集数]：告诉她你看完了，她会带着这一集的内容和你聊。"""
        if not self._is_owner(update):
            return
        args = (update.message.text.partition(" ")[2].strip()).rsplit(" ", 1)
        ss = self.db.active_shows()
        if not args[0] or not ss:
            await update.message.reply_text("用法：/watched 番名 [集数]（番要先在 /shows 里）")
            return
        name = args[0].strip("《》")
        row = next((s for s in ss if name in s["name"] or s["name"] in name
                    or (s["name_cn"] and name in s["name_cn"])), None)
        if not row:
            await update.message.reply_text("没找到这部番，先用 /shows 加上。")
            return
        ep = int(args[1]) if len(args) > 1 and args[1].isdigit() else row["episode"]
        info = await self.brain.media.episode(row, ep) if ep else None
        about = f"这集叫「{info['title']}」，内容：{info['desc'][:200]}" if info and (info["title"] or info["desc"]) else ""
        self._user_in(update, f"[我看完了《{row['name']}》第 {ep} 集]", "text")
        self.db.add_message("event", f"他看完了《{row['name']}》第 {ep} 集。你也看过了。{about}（和他聊聊剧情、角色、你的感受）")
        await self.schedule_reply(0.5)

    async def cmd_song(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        want = update.message.text.partition(" ")[2].strip()
        self._user_in(update, f"[想让你推荐一首歌{('：' + want) if want else ''}]", "text")
        self.db.add_message("event", "他想让你给他推荐一首歌。结合他的口味、此刻的心情和时间，挑一首真实存在的歌，"
                                     "用 [推歌:歌名|歌手] 发给他，再说说为什么想推这首。")
        await self.schedule_reply(0.5)

    async def cmd_profile(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        p = self.db.latest_profile()
        force = update.message.text.strip().endswith("更新")
        if force or (not p and len(self.db.all_memories("user")) >= 3):
            await update.message.reply_text("她在整理，稍等…")
            await self.brain.mind.update_profile()
            p = self.db.latest_profile()
        await update.message.reply_text(f"她心里的你：\n\n{p['content']}" if p else "她对你了解得还不够多，多聊聊吧。")

    async def cmd_story(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        today = self.brain.now().date()
        lines = [keep(f"「{a['title']}」{'（已结束）' if a['status'] == 'done' else ''}\n" +
                      "\n".join(f"  {b['day'][5:]} {b['text']}" for b in a["beats"]))
                 for a in self.brain.story.for_web(today)[:3]]
        await update.message.reply_text("\n\n".join(lines) if lines else "她最近没什么特别的事（每天凌晨会安排）。")

    async def cmd_mind(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        ts = self.db.thoughts(3)
        await update.message.reply_text("\n\n".join(
            f"{datetime.fromtimestamp(t['ts'], self.cfg.tz):%m-%d %H:%M}  {keep(t['thought'])}" for t in ts)
            if ts else "她还没有一个人想过事情（清醒时每隔几小时会想一次）。")
