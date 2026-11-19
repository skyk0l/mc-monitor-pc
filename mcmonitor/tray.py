"""Приложение в системном трее."""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pystray
from pystray import Menu, MenuItem

from . import api
from .config import AppConfig, ConfigError, ServerConfig, load_config
from .icon import make_icon
from .models import ServerStatus
from .notify import change_notice

log = logging.getLogger(__name__)


class TrayApp:
    """Иконка в трее + фоновый опрос серверов."""

    def __init__(self, config_path: Path, config: AppConfig | None = None) -> None:
        self.config_path = config_path
        self.config = config or load_config(config_path)
        self.statuses: dict[str, ServerStatus] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.icon = pystray.Icon(
            "mcmonitor",
            icon=make_icon(False),
            title="MC Monitor",
            menu=self._menu(),
        )

    # --- состояние ------------------------------------------------------
    def server_line(self, server: ServerConfig) -> str:
        status = self.statuses.get(server.address)
        if status is None:
            return f"{server.name}: проверяю…"
        if status.online:
            return f"{server.name}: {status.players_label}"
        return f"{server.name}: офлайн"

    def title(self) -> str:
        lines = ["MC Monitor"]
        for server in self.config.servers:
            lines.append(self.server_line(server))
        return "\n".join(lines)[:127]

    def any_online(self) -> bool:
        return any(status.online for status in self.statuses.values())

    def refresh(self) -> None:
        """Проверяет все серверы параллельно и обновляет трей."""
        servers = self.config.servers
        if not servers:
            self._redraw()
            return

        results: dict[str, ServerStatus] = {}
        workers = max(1, min(4, len(servers)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(api.fetch_status, server): server for server in servers}
            for future, server in futures.items():
                try:
                    results[server.address] = future.result()
                except Exception as exc:  # не должно случаться, но пусть будет
                    log.exception("сбой проверки %s", server.address)
                    results[server.address] = ServerStatus(online=False, error=str(exc))

        for server in servers:
            new_status = results[server.address]
            old_status = self.statuses.get(server.address)
            notice = change_notice(
                server.name,
                old_status,
                new_status,
                self.config,
                server.priority_players,
            )
            if notice:
                self._notify(notice.message, notice.title)
            self.statuses[server.address] = new_status
            if new_status.online:
                log.info(
                    "%s (%s): онлайн %s · %s",
                    server.name,
                    server.address,
                    new_status.players_label,
                    new_status.version or "версия неизвестна",
                )
            else:
                log.info(
                    "%s (%s): офлайн · %s",
                    server.name,
                    server.address,
                    new_status.error or "сервер не отвечает",
                )
        self._redraw()

    def _redraw(self) -> None:
        self.icon.icon = make_icon(self.any_online())
        self.icon.title = self.title()
        self.icon.menu = self._menu()
        try:
            self.icon.update_menu()
        except Exception:  # меню ещё не отрисовано — не страшно
            log.debug("update_menu до старта", exc_info=True)

    def _notify(self, message: str, title: str) -> None:
        try:
            self.icon.notify(message, title)
        except Exception:
            log.warning("не удалось показать уведомление", exc_info=True)

    # --- меню -----------------------------------------------------------
    @staticmethod
    def _noop(icon: pystray.Icon, item: MenuItem) -> None:
        return None

    def _menu(self) -> Menu:
        items: list[Any] = []
        for server in self.config.servers:
            items.append(MenuItem(self.server_line(server), self._noop, enabled=False))
            status = self.statuses.get(server.address)
            if status and status.online and status.players:
                names = [MenuItem(name, self._noop, enabled=False) for name in status.players[:20]]
                items.append(MenuItem("   👥 игроки", Menu(*names)))
        if not self.config.servers:
            items.append(
                MenuItem("Нет серверов — открой servers.json", self._noop, enabled=False)
            )
        items += [
            Menu.SEPARATOR,
            MenuItem("🔄 Обновить", self._on_refresh, default=True),
            MenuItem("📄 Открыть servers.json", self._on_open_config),
            MenuItem("♻ Перечитать настройки", self._on_reload),
            MenuItem("🔔 Проверить уведомления", self._on_test_notification),
            Menu.SEPARATOR,
            MenuItem("❌ Выход", self._on_quit),
        ]
        return Menu(*items)

    # --- действия меню ---------------------------------------------------
    def _on_refresh(self, icon: pystray.Icon, item: MenuItem) -> None:
        self._wake.set()

    def _on_open_config(self, icon: pystray.Icon, item: MenuItem) -> None:
        try:
            if not self.config_path.exists():
                self.config_path.write_text("{}\n", encoding="utf-8")
            os.startfile(self.config_path)  # type: ignore[attr-defined]
        except Exception:
            log.warning("не удалось открыть %s", self.config_path, exc_info=True)

    def _on_reload(self, icon: pystray.Icon, item: MenuItem) -> None:
        try:
            self.config = load_config(self.config_path)
        except ConfigError as exc:
            self._notify(f"Ошибка в настройках: {exc}", "MC Monitor")
            log.error("настройки не перечитаны: %s", exc)
            return
        self._redraw()
        self._wake.set()
        self._notify("Настройки перечитаны ✅", "MC Monitor")

    def _on_test_notification(self, icon: pystray.Icon, item: MenuItem) -> None:
        self._notify("Уведомления работают 👍", "MC Monitor")

    def _on_quit(self, icon: pystray.Icon, item: MenuItem) -> None:
        self._stop.set()
        self._wake.set()
        self.icon.stop()

    # --- жизненный цикл --------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                log.exception("ошибка обновления статусов")
            self._wake.wait(self.config.poll_interval)
            self._wake.clear()

    def _setup(self, icon: pystray.Icon) -> None:
        icon.visible = True
        threading.Thread(target=self._loop, name="mcmonitor-poller", daemon=True).start()

    def run(self) -> None:
        self.icon.run(self._setup)
