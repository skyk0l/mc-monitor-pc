"""Генерирует assets/icon.ico и assets/icon.png: python tools/make_icon.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcmonitor.icon import save_ico, save_png  # noqa: E402


def main() -> None:
    assets = ROOT / "assets"
    save_ico(assets / "icon.ico")
    save_png(assets / "icon.png")
    print(f"готово: {assets}")


if __name__ == "__main__":
    main()
