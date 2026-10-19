"""手机感知：快捷指令发来的事件归一化。"""


def test_normalize_phone():
    from ailife.sense.phone import normalize_phone
    assert normalize_phone("闹钟关了") == "刚起床"
    assert normalize_phone("到家啦") == "刚到家"
