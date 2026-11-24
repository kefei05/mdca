"""让 ``python -m mdca`` 等同于命令行入口。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
