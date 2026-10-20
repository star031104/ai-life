"""把合并以前的三个文件夹（ai-life、ai-life-group、ai-life-wechat）里她们自己的东西搬进新仓库的 home/。

    python scripts/migrate_old.py                 自动在旁边找（和这个仓库同一层的 ai-life-group、ai-life-wechat）
    python scripts/migrate_old.py --qq D:\\x\\ai-life-group --wechat D:\\x\\ai-life-wechat
    python scripts/migrate_old.py --yuki          有希的东西也从仓库根目录挪进 home/yuki（不挪也能用）
    python scripts/migrate_old.py --move          搬走而不是复制（默认复制，旧文件夹原样留着）

搬的是：config.yaml、config.llm.yaml、.env、data/、napcat/、persona/ 里你自己的人设、还有其它不是代码的文件（比如种子文件）。
代码（aigroup/、ailife/、scripts/、tests/ …）不搬：新仓库里都有。目标里已经有的文件不覆盖。
先把旧的程序都关掉（托盘里点“退出”）再搬。
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent
SKIP = {".venv", "venv", "__pycache__", ".git", ".github", ".pytest_cache", ".ruff_cache", "aigroup", "ailife", "scripts",
        "tests", "tools", "deploy", "docs", "home"}
CODE_FILES = {"run.py", "README.md", "CHANGELOG.md", "LICENSE", "NOTICE", "pyproject.toml", "requirements.txt", "install.bat",
              "install.sh", "setup.bat", "start.bat", "start.sh", "start.vbs", "get.ps1", ".gitignore", ".gitattributes",
              "config.example.yaml", "config.qq.example.yaml", "config.wechat.example.yaml"}


def _copy(src: Path, dst: Path, move: bool, done: list):
    if src.is_dir():
        for p in sorted(src.iterdir()):
            if p.name in ("__pycache__",):
                continue
            _copy(p, dst / p.name, move, done)
        if move:
            try:
                src.rmdir()
            except OSError:
                pass
        return
    if dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    (shutil.move if move else shutil.copy2)(str(src), str(dst))
    done.append(dst)


def migrate(old: Path, home: Path, move: bool = False) -> list[Path]:
    """old 里不是代码的东西 → home。persona/ 只搬新仓库里没有（或内容不一样）的人设。"""
    done: list[Path] = []
    for p in sorted(old.iterdir()):
        if p.name in SKIP or (p.is_file() and p.name in CODE_FILES):
            continue
        if p.name == "persona" and p.is_dir():
            for f in sorted(p.glob("*")):
                same = (CODE / "persona" / f.name)
                if f.is_file() and same.exists() and filecmp.cmp(f, same, shallow=False):
                    continue                                  # 自带的人设：新仓库里有
                _copy(f, home / "persona" / f.name, move, done)
            continue
        _copy(p, home / p.name, move, done)
    return done


def fix_paths(cfg: Path, old: Path, home: Path):
    """config.yaml 里写死的旧文件夹路径（比如 napcat_dir）换成新的。"""
    if not cfg.exists():
        return
    t = cfg.read_text(encoding="utf-8")
    n = t
    for o, h in ((str(old), str(home)), (str(old).replace("\\", "/"), str(home).replace("\\", "/")),
                 (str(old).replace("\\", "\\\\"), str(home).replace("\\", "\\\\"))):
        n = n.replace(o, h)
    if n != t:
        cfg.write_text(n, encoding="utf-8")
        print(f"  {cfg} 里的旧路径换成了 {home}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把旧的三个文件夹里的配置和数据搬进 home/")
    ap.add_argument("--qq", help="旧的 ai-life-group 文件夹")
    ap.add_argument("--wechat", help="旧的 ai-life-wechat 文件夹")
    ap.add_argument("--yuki", action="store_true", help="有希的配置和数据也从仓库根目录挪进 home/yuki")
    ap.add_argument("--move", action="store_true", help="搬走（默认复制）")
    a = ap.parse_args(argv)
    jobs = []
    for name, given, guess in (("qq", a.qq, "ai-life-group"), ("wechat", a.wechat, "ai-life-wechat")):
        old = Path(given).expanduser() if given else CODE.parent / guess
        if old.is_dir() and (old / "config.yaml").exists():
            jobs.append((name, old))
        elif given:
            print(f"找不到 {old / 'config.yaml'}，跳过 {name}")
    if a.yuki:
        if (CODE / "config.yaml").exists():
            jobs.append(("yuki", CODE))
        else:
            print("仓库根目录没有有希的 config.yaml：不用挪")
    if not jobs:
        print("没找到要搬的旧文件夹。用 --qq / --wechat 写上路径。")
        return 1
    for name, old in jobs:
        home = CODE / "home" / name
        if (home / "config.yaml").exists() and name != "yuki":
            print(f"home/{name} 里已经有 config.yaml：只补没有的文件")
        if name == "yuki":
            done = []
            for n in ("config.yaml", "config.llm.yaml", ".env", "data", "napcat"):
                if (CODE / n).exists():
                    _copy(CODE / n, home / n, True, done)          # 同一个盘里挪，很快
            for f in sorted((CODE / "persona").glob("*.md")):
                if f.name not in {"template.md", "linxi.md", "shenyan.md", "xiaoman.md"}:
                    _copy(f, home / "persona" / f.name, True, done)
        else:
            done = migrate(old, home, a.move)
            fix_paths(home / "config.yaml", old, home)
        print(f"{name}: {'搬' if a.move or name == 'yuki' else '复制'}了 {len(done)} 个文件 → home/{name}")
    print("好了。以后用 start.bat（或 python run.py yuki / qq / wechat）启动。旧文件夹确认没问题后可以删掉。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
