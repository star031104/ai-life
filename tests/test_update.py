"""自动更新：检查版本、只装完整且更新的 zip、不碰你的配置和人设、备份和回滚、新版起不来自动换回。（不联网）"""
import io
import zipfile
from types import SimpleNamespace

import pytest

from ailife import update as up


def make_zip(version="99.0", extra=None, drop=None, prefix="ai-life-main/"):
    files = {"run.py": "print('new')\n", "ailife/__init__.py": f'__version__ = "{version}"\n',
             "ailife/bot.py": "# new bot\n", "requirements.txt": "httpx\n", "ailife/new_mod.py": "x = 1\n",
             "config.yaml": "SHOULD NOT WRITE\n", "persona/yuki.md": "SHOULD NOT WRITE\n",
             "persona/template.md": "new template\n", "data/x.db": "no\n"}
    files.update(extra or {})
    for d in drop or []:
        files.pop(d, None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(prefix + k, v)
    return buf.getvalue()


def fake_cfg(root):
    return SimpleNamespace(root=root, get=lambda k, d=None: {"update": {}, "proxy": ""}.get(k, d))


@pytest.fixture
def tree(tmp_path, monkeypatch):
    (tmp_path / "ailife").mkdir()
    (tmp_path / "persona").mkdir()
    (tmp_path / "run.py").write_text("print('old')\n")
    (tmp_path / "ailife" / "bot.py").write_text("# old bot\n")
    (tmp_path / "config.yaml").write_text("mine\n")
    (tmp_path / "persona" / "yuki.md").write_text("my persona\n")
    monkeypatch.setattr(up, "current_version", lambda: "9.6")
    return tmp_path


def fetcher(zip_bytes, version="99.0"):
    def fetch(url, proxy, limit, timeout=60):
        if url.endswith("__init__.py"):
            return f'__version__ = "{version}"'.encode()
        return zip_bytes
    return fetch


def test_check_and_versions(tree):
    info = up.check(fake_cfg(tree), fetcher(b"", "9.10"))
    assert info["newer"] and info["latest"] == "9.10" and info["source"] == "GitHub"
    assert not up.check(fake_cfg(tree), fetcher(b"", "9.6"))["newer"]
    assert up.vtuple("9.10") > up.vtuple("9.9")


def test_gitee_first(tree):
    cfg = SimpleNamespace(root=tree, get=lambda k, d=None: {"update": {"gitee": "me/ai-life"}}.get(k, d))
    assert [s["name"] for s in up.sources(cfg)] == ["Gitee", "GitHub"]


@pytest.mark.parametrize("data,msg", [(b"not a zip", "不是完整的 zip"), (make_zip(drop=["ailife/bot.py"]), "少了"),
                                      (make_zip(extra={"../evil.py": "x"}), "奇怪的路径")])
def test_bad_zip_refused(tree, data, msg):
    with pytest.raises(up.UpdateError, match=msg):
        up.apply(fake_cfg(tree), fetcher(data), pip=False)
    assert (tree / "run.py").read_text() == "print('old')\n"            # 一个文件都没动


def test_not_newer_zip_refused(tree):
    with pytest.raises(up.UpdateError, match="不比现在"):
        up.apply(fake_cfg(tree), fetcher(make_zip(version="9.5")), pip=False)


def test_apply_and_rollback(tree):
    st = up.apply(fake_cfg(tree), fetcher(make_zip()), pip=False)
    assert st["to"] == "99.0" and len(st["sha256"]) == 64
    assert (tree / "run.py").read_text() == "print('new')\n" and (tree / "ailife" / "new_mod.py").exists()
    assert (tree / "config.yaml").read_text() == "mine\n"                   # 你的配置不碰
    assert (tree / "persona" / "yuki.md").read_text() == "my persona\n"     # 你的人设不碰
    assert (tree / "persona" / "template.md").read_text() == "new template\n"   # 新的模板照常加
    assert not (tree / "data" / "x.db").exists()
    assert (tree / "data" / "backup" / st["backup"]).exists()
    up.rollback(tree)
    assert (tree / "run.py").read_text() == "print('old')\n" and (tree / "ailife" / "bot.py").read_text() == "# old bot\n"
    assert not (tree / "ailife" / "new_mod.py").exists() and not (tree / "persona" / "template.md").exists()
    assert up.load_state(tree)["state"] == "rolled_back"
    with pytest.raises(up.UpdateError):
        up.rollback(tree)


def test_quick_exit_rolls_back_after_two(tree):
    up.apply(fake_cfg(tree), fetcher(make_zip()), pip=False)
    assert up.on_quick_exit(tree) == ""                                  # 第一次：再给一次机会
    assert "换回" in up.on_quick_exit(tree)
    assert (tree / "run.py").read_text() == "print('old')\n"


def test_confirmed_update_not_rolled_back(tree):
    up.apply(fake_cfg(tree), fetcher(make_zip()), pip=False)
    st = up.load_state(tree)
    st["state"] = "confirmed"
    up._save_state(tree, st)
    assert up.on_quick_exit(tree) == "" and up.on_quick_exit(tree) == ""
    assert (tree / "run.py").read_text() == "print('new')\n"
