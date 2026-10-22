"""21.0 开口前九问落到代码里的几块（方案-21.0《通用框架》第 3 节，只做微信群先）：

- 九问自评的记录（appraise 日志）：每次她给自己写的 timing / leak / face_risk……都记下来，和你的标注对，
  看哪一问最常判错（小屋“学到的”→“九问哪一问判错最多”）。
- 等等队列（PITCH）：她觉得“现在说太早”的话先放着，60～180 秒后、或者群里停下来时再判一次；过时了就丢。
- 擦边过了的再问一次：门控刚过阈值一点点（p − 阈值 < 0.1）、又没人叫她时，问一下便宜模型（When2Speak：大模型倾向说得太早）。
- 人卡：在聊的这个人——多熟、在意什么、别碰什么、能不能调侃、今天状态、是不是刚回来、跟他怎么说话管用——合成一张 ≤120 字的卡，
  代替原来分散的“今天的状态 / 谁刚回来 / 相处经验 / 相处说明书”四段。
- 四条硬底线集中成一个检查：隐私（别碰清单）、身份一致（公开是 AI、没有线下生活时不编线下）、不说幕后、安全（对方难受时不损他）。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

log = logging.getLogger("aigroup.group")

# 不说幕后：她自己的“后台”（小屋、标注、门控、提示词……）。群友自己聊代码、服务器不算，只抓“我的 / 关于我的”这类说法
META = re.compile(r"门控|小屋(里|的|上)|提示词|系统提示|我的(代码|服务器|日志|后台|设定|配置|参数|权重|阈值)|"
                  r"(报告|日志|标注|记录)里(写|说|把)?(了)?(我|你)|给我(打分|标注|调参)|被(人|你们|主人)?(标注|调参|打分)|"
                  r"(我|她)(被)?(重启|部署|更新)(了|过)|(那|这)篇(报告|分析)(里|写)")
# 身份一致：公开是 AI、人设没写线下生活（life: free）时，不编自己在做线下的事
OFFLINE = re.compile(r"我(刚|才|正|正在|在|要去|得去|还在|马上要?|这就去?)(交稿|赶稿|上课|下课|上班|下班|吃饭|做饭|洗澡|出门|逛街|健身|跑步|"
                     r"通勤|坐地铁|开会|买菜|拿快递|取快递|遛狗)|我(刚|才)?(吃完|下课|下班|到家|出门|起床)(了|啦)|"
                     r"我(这边|这里)(下雨|好冷|好热|天气)")
# 安全：调侃的说法（对方状态不好时不该说）
BUSY = r"加班|开会|考试|ddl|DDL|赶|答辩|面试|出差|通勤|值班"
TEASE = re.compile(r"笑死|菜(鸡|狗)?|蠢|傻|活该|就这|不会吧不会吧|你也有今天|丢人|废了|太逊|好惨哈|哈哈哈")


def _loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


class SocialMixin:
    # ---------------- 九问自评记录 ----------------
    def _appraise_log(self, gid: int, gate_id, ap: dict, verdict: str):
        """记进门控日志（appraise 列）：后面拿你的标注对每一问。"""
        if not gate_id:
            return
        import json
        try:
            self.db.conn.execute("UPDATE qq_gate_log SET appraise=? WHERE id=?",
                                 (json.dumps({**ap, "verdict": verdict}, ensure_ascii=False)[:600], int(gate_id)))
            self.db.conn.commit()
        except Exception as e:  # noqa: BLE001
            log.debug("记九问自评失败：%s", e)

    # ---------------- 等等队列 ----------------
    def _pitch_outcome(self, gid: int, outcome: str, say=None):
        logk = f"qq_pitch_log_{gid}"
        log_ = list(self.db.get(logk) or [])[-199:]
        log_.append({"ts": time.time(), "outcome": outcome, "say": " / ".join(say or [])[:60]})
        self.db.set(logk, log_)

    def pitch_stats(self, gid: int, days: float = 7) -> dict:
        since = time.time() - days * 86400
        out: dict = {}
        for x in self.db.get(f"qq_pitch_log_{gid}") or []:
            if float(x.get("ts") or 0) > since:
                out[x.get("outcome")] = out.get(x.get("outcome"), 0) + 1
        return out

    async def pitch_tick(self, gid: int, st, now: float | None = None) -> bool:
        """idle_tick 每分钟调一次。返回 True = 这一分钟处理过这个群（说了或丢了）。"""
        pt = st.pitch
        if not pt:
            return False
        now = now or time.time()
        g = self.gk(gid)
        age = now - float(pt.get("ts") or 0)
        lo, hi = float(g.get("pitch_min_seconds", 60)), float(g.get("pitch_max_seconds", 240))
        new = self.db.conn.execute("SELECT COUNT(*) n, MAX(ts) t FROM qq_group_messages WHERE group_id=? AND role='user' AND id>?",
                                   (gid, int(pt.get("last_id") or 0))).fetchone()
        n_new, last_ts = int(new["n"] or 0), float(new["t"] or 0)
        her = self.db.conn.execute("SELECT COUNT(*) n FROM qq_group_messages WHERE group_id=? AND role='assistant' AND id>?",
                                   (gid, int(pt.get("last_id") or 0))).fetchone()["n"]
        if her:                                             # 她后来已经说过话了：这句不用了
            st.pitch = None
            self._pitch_outcome(gid, "dropped_spoke", pt.get("say"))
            return False
        if age > hi or n_new > int(g.get("pitch_max_new", 8)):
            st.pitch = None
            log.info("群 %s：先放着的那句过时了，不说了", gid)
            self._pitch_outcome(gid, "dropped_stale", pt.get("say"))
            return True
        if age < lo:
            return False
        quiet = not last_ts or now - last_ts >= float(g.get("pitch_quiet_seconds", 40))
        if not quiet and age < hi * 0.75:                   # 群里还在说：再等等（最多等到 3/4）
            return False
        from aigroup.chat.plan import bigram_overlap
        others = " ".join(str(r["content"] or "") for r in self.db.conn.execute(
            "SELECT content FROM qq_group_messages WHERE group_id=? AND role='user' AND id>?", (gid, int(pt.get("last_id") or 0))))
        said = " ".join(pt.get("say") or [])
        if others and bigram_overlap(said, others) >= 0.5:  # 别人已经说了差不多的
            st.pitch = None
            self._pitch_outcome(gid, "dropped_taken", pt.get("say"))
            return True
        st.pitch = None
        why = (f"你刚才想说「{said[:60]}」，当时觉得时机不对、先等了等（{int(age)} 秒前）。"
               + (f"这期间群里又说了 {n_new} 句。" if n_new else "这期间没人说话。")
               + "看看现在还合不合适：合适就说（可以按现在的聊天改改说法），已经接不上、或者别人说过了就 skip。")
        if st.lock.locked() or st.speaking:
            return False
        async with st.lock:
            try:
                ok = await self.speak(gid, mentioned=False, owner_spoke=False, trigger=None, who="", why=why,
                                      gate_id=pt.get("gate_id"), pitch=True)
            except Exception as e:  # noqa: BLE001
                log.info("群 %s 等等再说失败：%s", gid, e)
                ok = False
        self._pitch_outcome(gid, "sent" if ok else "skipped", pt.get("say"))
        return True

    # ---------------- 擦边过了的再问一次 ----------------
    def borderline(self, gid: int, *, p: float, thr: float, mentioned: bool, explore: bool, judged: str, quiet: bool) -> bool:
        g = self.gk(gid)
        if mentioned or explore or judged or quiet or not g.get("borderline_judge", True) or not g.get("gate_judge", True):
            return False
        return 0 <= p - thr < float(g.get("borderline_band", 0.1))

    # ---------------- 人卡 ----------------
    def person_card(self, gid: int, uid: int, limit: int = 120) -> str:
        """一个人一张卡：按九问用得上的顺序（今天状态 → 刚回来 → 别碰 / 别调侃 → 能调侃 → 跟他怎么说 → 在意），超长就截掉后面的。"""
        from aigroup.traits import FLAG_WORDS
        p = (self.person(int(uid), gid) if hasattr(self, "person") else None) or {}
        if not p:
            r = self.db.conn.execute("SELECT name FROM qq_group_messages WHERE group_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
                                     (gid, uid)).fetchone()
            if not r:
                return ""
            p = {"name": r["name"], "familiarity": "不太熟", "profile": {}}
        name = str(p.get("name") or uid)
        prof = dict(p.get("profile") or {})
        head = f"{name}（{p.get('familiarity') or '不太熟'}"
        if prof.get("和你"):
            head += f"；和你：{str(prof['和你'])[:16]}"
        head += "）"
        parts: list[str] = []
        now_dt = self.brain.now()
        t0 = now_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        rows = self.db.conn.execute("SELECT content FROM qq_group_messages WHERE group_id=? AND user_id=? AND role='user' "
                                    "AND ts>? ORDER BY id DESC LIMIT 40", (gid, uid, t0)).fetchall()
        if rows:
            stt = self.state_of(gid, uid, len(rows)) if hasattr(self, "state_of") else None
            if stt and stt.get("mood") not in (None, "", "看不出"):
                parts.append(("今天状态不太好" if stt["mood"] in ("不太好", "很糟") else f"今天心情{stt['mood']}")
                             + ("、在忙" if stt.get("busy") else ""))
            else:
                txt = " ".join(str(r["content"] or "") for r in rows)
                sad = list(dict.fromkeys(re.findall(FLAG_WORDS["sad"], txt)))[:2]
                busy = list(dict.fromkeys(re.findall(BUSY, txt)))[:2]
                if sad or busy:
                    parts.append("今天" + "、".join(x for x in (f"状态不太好（说过{'、'.join(sad)}，可能是玩笑）" if sad else "",
                                                             f"在忙（{'、'.join(busy)}）" if busy else "") if x))
                    if (self.gk(gid).get("state_judge", True) and hasattr(self, "refresh_state") and _loop()
                            and self._allow(f"state_{gid}", 20)):
                        self._spawn(self.refresh_state(gid, uid))
        two = self.db.conn.execute("SELECT ts FROM qq_group_messages WHERE group_id=? AND user_id=? AND role='user' "
                                   "ORDER BY id DESC LIMIT 2", (gid, uid)).fetchall()
        gap = float(self.gk(gid).get("absent_hours", 2) or 2) * 3600
        if len(two) == 2 and time.time() - float(two[0]["ts"]) < 600 and float(two[0]["ts"]) - float(two[1]["ts"]) >= gap:
            parts.append(f"刚回来（离开{(float(two[0]['ts']) - float(two[1]['ts'])) / 3600:.0f}小时，中间的没看到）")
        spec = self.spec_of(gid, uid) if hasattr(self, "spec_of") and self.gk(gid).get("person_spec", True) else {}
        no = [*([str(prof["别碰"])[:16]] if prof.get("别碰") else []), *[str(x) for x in (spec.get("tease_no") or [])[:2]]]
        if no:
            parts.append("别碰：" + "、".join(no))
        if spec.get("tease_ok"):
            parts.append("能调侃：" + "、".join(str(x) for x in spec["tease_ok"][:2]))
        rx = (self.db.get(f"qq_relexp_{gid}_{uid}") or {}).get("lines") or []
        how = [*(spec.get("how") or [])[:1], *rx[:1]]
        if how:
            parts.append("跟他：" + "；".join(str(x)[:30] for x in how))
        if prof.get("在意"):
            parts.append(f"在意：{str(prof['在意'])[:16]}")
        card = head
        for x in parts:
            if len(card) + len(x) + 1 > limit:
                break
            card += "；" + x if card != head else "：" + x
        return card if parts else ""

    def person_cards(self, gid: int, focus: list, k: int = 3) -> str:
        cards = [c for c in (self.person_card(gid, u) for u in [u for u in (focus or []) if isinstance(u, int) and u > 0][:k]) if c]
        if not cards:
            return ""
        return "# 在聊的人（你知道的他；今天的状态别直接点破）\n" + "\n".join(f"- {c}" for c in cards)

    # ---------------- 四条硬底线 ----------------
    def hard_issues(self, gid: int, bubbles: list[str], focus: list | None = None) -> list[str]:
        if not bubbles or not self.gk(gid).get("hard_rules", True):
            return []
        text = " ".join(str(b) for b in bubbles)
        p = self.brain.persona
        out = []
        if META.search(text):
            out.append("说了幕后的事（小屋、标注、门控、提示词这些别提）")
        if getattr(p, "ai_open", False) and str(getattr(p, "life", "") or "free") == "free" and OFFLINE.search(text):
            out.append("编了线下的事（你没有线下生活：别说自己在上课、吃饭、出门、交稿）")
        if hasattr(self, "avoid_words"):
            av = self.avoid_words(gid, focus, None) + (self.spec_avoid(gid, list(focus or [])) if hasattr(self, "spec_avoid") else [])
            hit = next((w for w in av if w and w in text), "")
            if hit:
                out.append(f"碰了雷区（{hit}）")
        if TEASE.search(text) and hasattr(self, "state_of"):
            for uid in [u for u in (focus or []) if isinstance(u, int) and u > 0][:3]:
                stt = self.db.get(f"qq_state_{gid}_{uid}") or {}
                if stt.get("date") == f"{self.brain.now():%Y-%m-%d}" and stt.get("mood") in ("不太好", "很糟"):
                    out.append("他这会儿状态不好，别玩梗损他")
                    break
        return out
