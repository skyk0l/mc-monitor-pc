"""Командная строка: трей, разовая проверка, самопроверка."""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__, api
from .config import (
    AppConfig,
    ConfigError,
    ServerConfig,
    app_dir,
    default_config_path,
    load_config,
)

LOG_NAME = "mcmonitor.log"


def setup_logging(log_path: Path) -> None:
    handlers: list[logging.Handler] = [
        RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    ]
    if getattr(sys, "stderr", None) and sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def describe(server: ServerConfig, status) -> list[str]:
    lines = [f"{server.name} · {server.address}"]
    if status.online:
        version = f" · {status.version}" if status.version else ""
        lines.append(f"   🟢 онлайн · {status.players_label}{version}")
        motd = status.first_motd_line
        if motd:
            lines.append(f"   💬 {motd}")
        if status.players:
            shown = ", ".join(status.players[:10])
            if len(status.players) > 10:
                shown += " и др."
            lines.append(f"   👥 {shown}")
    else:
        lines.append(f"   🔴 недоступен · {status.error or 'сервер не отвечает'}")
    return lines


def run_once(config: AppConfig) -> int:
    """Проверяет серверы один раз и печатает отчёт (без GUI)."""
    if not config.servers:
        print("В servers.json нет ни одного сервера.")
        return 0
    online = 0
    for server in config.servers:
        status = api.fetch_status(server)
        online += int(status.online)
        print("\n".join(describe(server, status)))
    print(f"\nОнлайн: {online}/{len(config.servers)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mcmonitor",
        description="MC Monitor — окно управления и плавающий виджет Minecraft-серверов.",
    )
    parser.add_argument("--config", type=Path, help="путь к servers.json")
    parser.add_argument(
        "--once",
        action="store_true",
        help="проверить серверы один раз, напечатать отчёт и выйти",
    )
    parser.add_argument("--interval", type=int, help="интервал опроса в секундах")
    parser.add_argument(
        "--tray",
        action="store_true",
        help="старый режим: иконка в системном трее вместо виджета",
    )
    parser.add_argument(
        "--preview",
        type=Path,
        help="нарисовать виджет в PNG-файл и выйти (для скриншотов)",
    )
    parser.add_argument("--version", action="version", version=f"MC Monitor {__version__}")
    parser.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    config_path = args.config or default_config_path()
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"Не удалось прочитать настройки: {exc}", file=sys.stderr)
        return 2

    if args.interval:
        config.poll_interval = max(15, args.interval)

    if args.once:
        return run_once(config)

    setup_logging(app_dir() / LOG_NAME)

    if args.tray:
        from .tray import TrayApp

        TrayApp(config_path, config).run()
        return 0

    try:
        from .widget import WidgetApp
    except ImportError:
        print(
            "Для виджета нужен PySide6. Установи его:\n"
            "  py -3.11 -m pip install PySide6\n"
            "Или запусти старый трей: py -3.11 -m mcmonitor --tray",
            file=sys.stderr,
        )
        return 3

    if args.preview:
        app = WidgetApp(config_path, config)
        app.preview(args.preview)
        print(f"Превью сохранено: {args.preview}")
        return 0

    if args.selftest:
        app = WidgetApp(config_path, config)
        target = Path(tempfile.gettempdir()) / "mcmonitor-selftest.png"
        app.preview(target)
        menu = app.widget._build_menu()
        print(f"OK: виджет рисуется ({target}), пунктов меню: {len(menu.actions())}")
        return 0

    return WidgetApp(config_path, config).run()

