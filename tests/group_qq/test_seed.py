"""seed：事先写好的群笔记、画像、别名、群梗、按群的开关灌进她的数据（QQ 版按群号、QQ 号）。"""
from aigroup.core.store import Store
from aigroup.seed import apply

SEED = {"groups": [{"gid": 123456, "notes": "- 每晚开黑", "norms": "- 消息拆短句发",
                    "lore": [{"keys": ["没绷住"], "text": "笑出来了的意思"}],
                    "people": [{"qq": 10001, "name": "小林", "aliases": ["xl", "林林"],
                                "profile": {"印象": "爱分享日常", "记得的事": ["养花", ""], "瞎写": "x"}}]},
                   {"name": "没写群号"}]}


def test_seed_apply_qq(tmp_path):
    db = Store(tmp_path / "g.db")
    st = apply(SEED, db, lambda x: int(str(x)), lambda n: "")
    assert st["groups"] == 1 and st["people"] == 1 and st["lore"] == 1 and st["missing"] == ["没写群号"]
    assert db.get("qq_group_notes_123456") == "- 每晚开黑"
    r = db.conn.execute("SELECT profile FROM qq_profiles WHERE user_id=10001 AND group_id=123456").fetchone()
    assert "爱分享日常" in r["profile"] and "瞎写" not in r["profile"]
    assert db.conn.execute("SELECT aliases FROM qq_people WHERE user_id=10001").fetchone()[0] == "xl、林林"
