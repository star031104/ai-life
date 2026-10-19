"""独立的群聊角色（config 里 mode: group）。

和一对一的她是两个“人”：自己的人设、自己的数据文件夹（instance_root）、自己的 QQ 小号。
只跑群聊需要的东西：大脑（人设 + 心情 + 她自己的一天）、QQ 连接、群聊、每天的日程。
不启动 Telegram、私聊、桌宠、小屋、电脑感知。
"""
from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger("ailife.group")


def check(cfg) -> list[str]:
    """启动前检查：返回问题列表（空＝没问题）。"""
    q = cfg.get("qq") or {}
    probs = []
    if not q.get("enabled"):
        probs.append("qq.enabled 没打开")
    if not [g for g in (q.get("groups") or []) if str(g).strip().isdigit()]:
        probs.append("qq.groups 是空的：群聊角色至少要在一个群里（白名单）")
    if not str(cfg.get("instance_root") or "").strip():
        probs.append("没写 instance_root：群聊角色的数据会和一对一的她混在同一个 data 文件夹里")
    return probs


async def run_group(cfg):
    from .brain import Brain
    from .qq import OneBot
    from .qq_group import GroupChat

    probs = check(cfg)
    for p in probs:
        log.error("群聊角色的配置有问题：%s", p)
    if any("groups" in p or "enabled" in p for p in probs):
        return None
    q = cfg.get("qq") or {}
    brain = Brain(cfg)
    ob = OneBot(str(q.get("ws_url") or "ws://127.0.0.1:3002"), str(q.get("access_token") or ""))
    groups = [int(g) for g in q.get("groups") or [] if str(g).strip().isdigit()]
    gc = GroupChat(cfg, brain, ob, int(q.get("owner_id") or 0), groups, list(q.get("aliases") or []))
    ob.handlers.append(gc.on_event)
    ob.start()
    log.info("群聊角色 %s 启动：群 %s（数据在 %s）", brain.persona.name, groups, cfg.root)

    async def daily():
        now = brain.now()
        try:
            await brain.refresh_world()
        except Exception:  # noqa: BLE001
            pass
        try:
            await brain.memory.plan_life(now)      # 她今天自己的安排（群里聊起来前后一致）
        except Exception as e:  # noqa: BLE001
            log.info("今天的安排没排好：%s（等会儿再试）", e)

    last_daily = 0.0
    try:
        while True:
            if time.time() - last_daily > 1800:
                last_daily = time.time()
                await daily()
            if ob.ready:
                try:
                    await gc.idle_tick(1.0)
                except Exception as e:  # noqa: BLE001
                    log.info("冒泡检查出错：%s", e)
            await asyncio.sleep(60)
    finally:
        await ob.stop()
