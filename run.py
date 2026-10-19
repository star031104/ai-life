"""ai-life 启动入口：python run.py [命令]。命令列表见 ailife/cli.py（python run.py help）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ailife.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
