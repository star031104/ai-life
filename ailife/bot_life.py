"""第六批的命令：/wish 愿望清单、/capsule 时间胶囊、/goals 她的目标、/people 她身边的人。"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

from .bot_common import keep

log = logging.getLogger("ailife.bot")


class LifeCommandsMixin:
    # ---------------- 愿望清单 ----------------
    def _wish_kb(self):
        rows = [[InlineKeyboardButton("⬜ " + w["text"][:30], callback_data=f"w:{w['id']}")]
                for w in self.db.wishes(include_done=False)[:10]]
        return InlineKeyboardMarkup(rows) if rows else None

    async def cmd_wish(self, update: Update, ctx):
        """/wish  看清单；/wish add 一起去看海；/wish done 编号 [感想]；/wish del 编号"""
        if not self._is_owner(update):
            return
        args = ctx.args or []
        msg = update.message
        if args and args[0] in ("add", "加", "新") and len(args) > 1:
            text = " ".join(args[1:])[:60]
            self.db.add_wish(text, "user")
            self.db.add_message("event", f"他把“{text}”写进了你们的愿望清单")
            await msg.reply_text(f"写进愿望清单了：{text}", reply_markup=self._wish_kb())
            await self.schedule_reply(1.0)
            return
        if args and args[0] in ("done", "完成", "实现") and len(args) > 1 and args[1].isdigit():
            await self._finish_wish(int(args[1]), " ".join(args[2:]), msg.reply_text)
            return
        if args and args[0] in ("del", "删") and len(args) > 1 and args[1].isdigit():
            self.db.del_wish(int(args[1]))
            await msg.reply_text("删掉了。")
            return
        ws = self.db.wishes()
        if not ws:
            await msg.reply_text("愿望清单还是空的。比如：/wish add 一起去看海\n聊天时说“以后想和你一起…”她也会记下来。")
            return
        open_ = [w for w in ws if not w["done_ts"]]
        done = [w for w in ws if w["done_ts"]]
        lines = [f"{w['id']}. {w['text']}" + ("（她提的）" if w["by"] == "her" else "") for w in open_]
        if done:
            lines += ["", "已经一起做到的："] + [
                f"✓ {w['text']}（{datetime.fromtimestamp(w['done_ts'], self.cfg.tz):%Y-%m-%d}）" for w in done[:10]]
        await msg.reply_text("\n".join(lines) + "\n\n做到了就点下面的按钮；/wish done 编号 感想 可以顺便写一句",
                             reply_markup=self._wish_kb())

    async def _finish_wish(self, wid: int, note: str, reply):
        w = next((x for x in self.db.wishes() if x["id"] == wid), None)
        if not w or w["done_ts"]:
            await reply("没找到这个愿望（或者已经完成了）。")
            return
        self.db.done_wish(wid, note)
        self.db.add_message("event", f"你们一起实现了愿望清单里的“{w['text']}”" + (f"，他说：{note}" if note else ""))
        await reply(f"🎉 愿望达成：{w['text']}")
        await self.schedule_reply(1.0)

    async def on_wish_button(self, update: Update, ctx):
        q = update.callback_query
        if not q or not self.owner_id or q.from_user.id != self.owner_id:
            return
        try:
            wid = int(q.data.split(":")[1])
        except (IndexError, ValueError):
            await q.answer()
            return
        await q.answer("达成！")
        await self._finish_wish(wid, "", lambda t: q.message.reply_text(t))
        try:
            await q.edit_message_reply_markup(self._wish_kb())
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 时间胶囊 ----------------
    async def cmd_capsule(self, update: Update, ctx):
        """/capsule 日期 内容   日期可以写 2027-01-01、1月1日、30天、3个月、半年、一年"""
        if not self._is_owner(update):
            return
        msg = update.message
        args = ctx.args or []
        if args and args[0] in ("del", "删") and len(args) > 1 and args[1].isdigit():
            c = next((x for x in self.db.capsules() if x["id"] == int(args[1]) and x["by"] == "user"), None)
            if c and not c["opened_ts"]:
                self.db.del_capsule(c["id"])
                await msg.reply_text("这个时间胶囊挖出来扔掉了。")
            else:
                await msg.reply_text("没找到（只能删你自己写的、还没打开的）。")
            return
        if len(args) >= 2:
            from .moments import parse_open_day
            today = self.brain.now().date()
            day = parse_open_day(args[0], today)
            raw = (msg.text or "").split(None, 1)[1] if len((msg.text or "").split(None, 1)) > 1 else ""
            content = raw.split(None, 1)[1].strip() if len(raw.split(None, 1)) > 1 else " ".join(args[1:])  # 保留换行
            if not day or day <= today:
                await msg.reply_text("打开日期要是将来的某天：比如 2027-01-01、3个月、半年、一年")
                return
            if (day - today).days > 3660:
                await msg.reply_text("十年以上有点太远啦，换个近一点的日子吧。")
                return
            await msg.reply_text("正在埋……")
            cid, mine = await self.brain.moments.create_capsule(content, day)
            await msg.reply_text(f"🔒 时间胶囊埋好了，{day:%Y-%m-%d} 那天会打开（还有 {(day - today).days} 天）。"
                                 + ("\n她听说以后，也偷偷写了一封给那天的你，到时候一起拆。" if mine else ""))
            await self.schedule_reply(2.0)
            return
        caps = self.db.capsules()
        if not caps:
            await msg.reply_text("还没有时间胶囊。写给未来的你们：\n/capsule 一年 一年后的我们，还在一起吗？\n"
                                 "/capsule 2027-01-01 新年快乐")
            return
        today = self.brain.now().date().isoformat()
        lines = []
        for c in caps:
            who = "你" if c["by"] == "user" else "她"
            if c["opened_ts"]:
                lines.append(f"💌 {c['open_day']} 已打开（{who}写的）：{c['content'][:40]}")
            else:
                left = (datetime.fromisoformat(c["open_day"]).date() - datetime.fromisoformat(today).date()).days
                lines.append(f"🔒 {c['id']}. {c['open_day']} 打开（{who}写的，还有 {left} 天）")
        await msg.reply_text("\n".join(lines) + "\n\n/capsule del 编号 可以删掉你自己写的、还没打开的")

    # ---------------- 她的目标 / 身边的人 ----------------
    async def cmd_goals(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        today = self.brain.now().date()
        hw = self.brain.herworld
        gs = self.db.goals(None, 6)
        if not gs:
            await update.message.reply_text("她还没定下自己的长期目标（每天整理时会想好的）。")
            return
        out = []
        for g in gs:
            prog, past, _ = hw.progress(g, today)
            if g["status"] == "done":
                out.append(keep(f"🏁 {g['title']}：{g.get('outcome') or ''}"))
                continue
            bar = "▓" * (prog // 10) + "░" * (10 - prog // 10)
            out.append(keep(f"🎯 {g['title']}  {bar} {prog}%\n   为什么：{g['why'] or '—'}\n   最近："
                            + ("；".join(s["text"] for s in past[-2:]) or "刚开始")))
        await update.message.reply_text("她自己在努力的事（只显示到今天为止）\n\n" + "\n\n".join(out))

    async def cmd_people(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        ps = self.db.cast_all()
        if not ps:
            await update.message.reply_text("她身边的人还没定好（启动后不久会自动生成）。")
            return
        core = [p for p in ps if p["core"]]
        other = [p for p in ps if not p["core"]]
        fmt = lambda p: keep(f"· {p['name']}（{p['role']}）" + (f"：{p['descr']}" if p["descr"] else ""))  # noqa: E731
        text = "她身边的人\n" + "\n".join(fmt(p) for p in core)
        if other:
            text += "\n\n后来聊天里出现过的\n" + "\n".join(fmt(p) for p in other[:15])
        await update.message.reply_text(text)

    # ---------------- 这一周她的表现 ----------------
    async def cmd_quality(self, update: Update, ctx):
        if not self._is_owner(update):
            return
        import asyncio
        from .report import weekly_report
        text, _, _ = await asyncio.to_thread(weekly_report, self.brain, False, True)
        await update.message.reply_text(text)

    # ---------------- 人设 ----------------
    async def cmd_persona(self, update: Update, ctx):
        """/persona 看现在的人设和能换的；/persona reload 改完人设文件后重新读；/persona use 文件名 换角色（会重启）"""
        if not self._is_owner(update):
            return
        from .personas import assign, list_personas, set_persona_file
        args = ctx.args or []
        msg = update.message
        if args and args[0] in ("reload", "重载", "刷新"):
            warns = self.brain.reload_persona()
            await msg.reply_text(f"人设已重新读取：{self.brain.persona.name}" + ("\n⚠️ " + "\n⚠️ ".join(warns) if warns else ""))
            return
        if args and args[0] in ("use", "换") and len(args) > 1:
            want = args[1]
            ps = [p for p in list_personas(self.cfg) if not p.get("error")]
            hit = next((p for p in ps if p["file"] == want or p["file"].endswith("/" + want) or
                        p["file"].endswith("/" + want + ".md") or p["name"] == want), None)
            if not hit:
                await msg.reply_text("没找到这个人设。发 /persona 看看有哪些。")
                return
            if hit.get("current"):
                await msg.reply_text("现在用的就是这个。")
                return
            share = "--share" in args or "共用" in args
            db = assign(self.cfg, hit["file"], share_with_current=share)
            set_persona_file(self.cfg.root, hit["file"])
            await msg.reply_text(f"好，换成 {hit['name']}（记忆：{db}）。程序马上重启（大约 15 秒），之后就是 TA 了。")
            self.restart_event.set()
            return
        lines = []
        for p in list_personas(self.cfg):
            if p.get("template"):
                continue
            mark = "▶ " if p.get("current") else "· "
            lines.append(f"{mark}{p['name']}（{p.get('identity') or '—'}）  {p['file'].removeprefix('persona/')}"
                         + ("" if p.get("memory") else "  · 新角色"))
        cur = self.brain.persona
        warn = ("\n\n⚠️ 现在的人设有几处建议：\n· " + "\n· ".join(cur.warnings)) if cur.warnings else ""
        await msg.reply_text("人设\n" + "\n".join(lines) +
                             "\n\n换角色：/persona use 文件名（每个角色有自己的记忆；加“共用”就沿用现在的记忆）"
                             "\n改了人设文件：/persona reload\n也可以在小屋“设置”页直接编辑。" + warn)
