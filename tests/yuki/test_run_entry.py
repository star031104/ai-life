"""12.0 合并后的入口：run.py 选角色、找她的家；托盘、搬家脚本至少能编译。"""
import importlib.util
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run():
    spec = importlib.util.spec_from_file_location("run_entry", ROOT / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_home_of(monkeypatch, tmp_path):
    m = _run()
    monkeypatch.delenv("AILIFE_HOME", raising=False)
    monkeypatch.delenv("AIGROUP_HOME", raising=False)
    monkeypatch.setattr(m, "CODE", tmp_path)
    assert m.home_of("qq") == tmp_path / "home" / "qq"
    assert m.home_of("yuki") == tmp_path / "home" / "yuki"
    (tmp_path / "config.yaml").write_text("x")                    # 老安装：有希的配置在根目录
    assert m.home_of("yuki") == tmp_path
    (tmp_path / "home" / "yuki").mkdir(parents=True)
    assert m.home_of("yuki") == tmp_path / "home" / "yuki"
    monkeypatch.setenv("AIGROUP_HOME", str(tmp_path / "elsewhere"))
    assert m.home_of("wechat") == (tmp_path / "elsewhere").resolve()
    assert m.home_of("qq", str(tmp_path / "o")) == (tmp_path / "o").resolve()
    assert m.ALIASES["wx"] == "wechat" and m.ALIASES["group"] == "qq"


def test_scripts_compile():
    for p in ("scripts/tray.pyw", "scripts/migrate_old.py", "run.py", "deploy/server/ops.py"):
        py_compile.compile(str(ROOT / p), doraise=True)


def test_migrate_copies_only_her_things(tmp_path):
    spec = importlib.util.spec_from_file_location("mig", ROOT / "scripts" / "migrate_old.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    old = tmp_path / "old"
    for f, t in {"config.yaml": f"qq: {{napcat_dir: {old}/napcat}}", ".env": "K=1", "data/group.db": "x",
                 "persona/local_x.md": "mine", "aigroup/app.py": "code", "run.py": "code", "seed_a.yaml": "s"}.items():
        (old / f).parent.mkdir(parents=True, exist_ok=True)
        (old / f).write_text(t)
    (old / "persona" / "template.md").write_text((ROOT / "persona" / "template.md").read_text(encoding="utf-8"),
                                                 encoding="utf-8")
    home = tmp_path / "home" / "qq"
    done = {p.relative_to(home).as_posix() for p in m.migrate(old, home)}
    assert done == {"config.yaml", ".env", "data/group.db", "persona/local_x.md", "seed_a.yaml"}
    m.fix_paths(home / "config.yaml", old, home)
    assert str(home) in (home / "config.yaml").read_text() and (old / "config.yaml").exists()


def test_update_restarts_other_roles(tmp_path):
    from ailife.core.update import restart_others
    for n in ("yuki", "qq", "wechat"):
        (tmp_path / "home" / n / "data").mkdir(parents=True)
    out = restart_others(tmp_path, tmp_path / "home" / "yuki")
    assert out == ["qq", "wechat"]
    assert (tmp_path / "home" / "qq" / "data" / "tray_cmd").read_text() == "restart"
    assert not (tmp_path / "home" / "yuki" / "data" / "tray_cmd").exists()
