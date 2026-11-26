"""Пути к настройкам и пользовательским данным приложения."""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_INTERVAL = 15
LEGACY_DEFAULT_INTERVAL = 60
MIN_INTERVAL = 15
SERVER_PROTOCOLS = ("java", "bedrock")


class ConfigError(RuntimeError):
    """Не удалось прочитать или разобрать настройки."""


@dataclass(frozen=True)
class ServerConfig:
    """Один Minecraft-сервер из списка."""

    name: str
    host: str
    port: int | None = None
    protocol: str = "java"
    show_in_widget: bool = False
    group_id: str | None = None
    sort_order: int = 0
    pinned: bool = False
    added_at: float = field(default_factory=time.time)
    priority_players: tuple[str, ...] = ()

    @property
    def address(self) -> str:
        """Адрес в привычном виде: host или host:port."""
        return f"{self.host}:{self.port}" if self.port else self.host

    @classmethod
    def parse(cls, value: Any) -> "ServerConfig":
        """Принимает строку «host:port» или объект {name, address}."""
        if isinstance(value, str):
            return cls.from_address(value, value)
        if isinstance(value, dict):
            raw = str(value.get("address") or value.get("host") or "").strip()
            if not raw:
                raise ConfigError("у сервера не указан address")
            name = str(value.get("name") or raw).strip()
            server = cls.from_address(
                raw, name, protocol=str(value.get("protocol") or "java")
            )
            port = value.get("port")
            if port:
                try:
                    server = replace(server, port=int(port))
                except (TypeError, ValueError) as exc:
                    raise ConfigError(f"неправильный port у «{name}»") from exc
            raw_priorities = value.get("priority_players", [])
            if not isinstance(raw_priorities, (list, tuple)):
                raise ConfigError(f"неправильный priority_players у «{name}»")
            priorities: list[str] = []
            seen_priorities: set[str] = set()
            for raw_priority in raw_priorities:
                if not isinstance(raw_priority, str):
                    raise ConfigError(f"неправильный ник в priority_players у «{name}»")
                priority = raw_priority.strip()
                if not priority or len(priority) > 32:
                    raise ConfigError(f"неправильный ник в priority_players у «{name}»")
                key = priority.casefold()
                if key not in seen_priorities:
                    priorities.append(priority)
                    seen_priorities.add(key)
            try:
                added_at = float(value.get("added_at") or server.added_at)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ConfigError(f"неправильный added_at у «{name}»") from exc
            if not math.isfinite(added_at) or added_at <= 0:
                added_at = server.added_at
            server = replace(
                server,
                show_in_widget=bool(value.get("show_in_widget", False)),
                group_id=str(value.get("group_id") or ""),
                sort_order=int(value.get("sort_order") or 0),
                pinned=bool(value.get("pinned", False)),
                added_at=added_at,
                priority_players=tuple(priorities),
            )
            if not server.group_id:
                server = replace(server, group_id=None)
            return server
        raise ConfigError(f"непонятная запись сервера: {value!r}")

    @classmethod
    def from_address(
        cls, raw: str, name: str, protocol: str = "java"
    ) -> "ServerConfig":
        raw = raw.strip()
        host, port = raw, None
        if ":" in raw:
            maybe_host, maybe_port = raw.rsplit(":", 1)
            if maybe_port.isdigit():
                host, port = maybe_host.strip(), int(maybe_port)
        if not host:
            raise ConfigError("пустой адрес сервера")
        protocol = protocol.strip().lower()
        if protocol not in SERVER_PROTOCOLS:
            raise ConfigError(f"неподдерживаемый протокол: {protocol}")
        return cls(name=name.strip() or host, host=host, port=port, protocol=protocol)


@dataclass
class AppConfig:
    """Что и как проверяем."""

    servers: list[ServerConfig] = field(default_factory=list)
    poll_interval: int = DEFAULT_INTERVAL
    notify_joins: bool = True
    notify_offline: bool = True
    notify_online: bool = True
    notification_start_minute: int = 0
    notification_end_minute: int = 1440
    notification_dnd_until: float = 0
    server_sort_mode: str = "manual"
    accent_color: str = "#7ee0a3"
    widget_bg_color: str = "#252B30"
    widget_opacity: float = 0.85
    widget_accent_color: str = "#A7C6A4"
    hotkey_show_widget: str = "ctrl+shift+m"

    @classmethod
    def from_dict(cls, data: Any) -> "AppConfig":
        if not isinstance(data, dict):
            raise ConfigError("в servers.json должен быть объект { ... }")
        raw_servers = data.get("servers")
        if raw_servers is None:
            raw_servers = []
        if not isinstance(raw_servers, list):
            raise ConfigError("поле servers должно быть списком")
        servers = [ServerConfig.parse(item) for item in raw_servers]
        try:
            interval = int(data.get("poll_interval") or DEFAULT_INTERVAL)
        except (TypeError, ValueError):
            interval = DEFAULT_INTERVAL
        if interval == LEGACY_DEFAULT_INTERVAL:
            interval = DEFAULT_INTERVAL
        try:
            notification_start = int(data.get("notification_start_minute", 0))
        except (TypeError, ValueError):
            notification_start = 0
        try:
            notification_end = int(data.get("notification_end_minute", 1440))
        except (TypeError, ValueError):
            notification_end = 1440
        notification_start = min(max(notification_start, 0), 1439)
        notification_end = min(max(notification_end, 0), 1440)
        try:
            dnd_until = float(data.get("notification_dnd_until", 0))
        except (TypeError, ValueError):
            dnd_until = 0
        if not math.isfinite(dnd_until) or (dnd_until < 0 and dnd_until != -1):
            dnd_until = 0
        sort_mode = str(data.get("server_sort_mode") or "manual")
        if sort_mode not in {"manual", "name", "online", "date"}:
            sort_mode = "manual"
        return cls(
            servers=servers,
            poll_interval=max(MIN_INTERVAL, interval),
            notify_joins=bool(data.get("notify_joins", True)),
            notify_offline=bool(data.get("notify_offline", True)),
            notify_online=bool(data.get("notify_online", True)),
            notification_start_minute=notification_start,
            notification_end_minute=notification_end,
            notification_dnd_until=dnd_until,
            server_sort_mode=sort_mode,
            accent_color=str(data.get("accent_color", "#7ee0a3")),
            widget_bg_color=str(data.get("widget_bg_color", "#252B30")),
            widget_opacity=float(data.get("widget_opacity", 0.85)),
            widget_accent_color=str(data.get("widget_accent_color", "#A7C6A4")),
            hotkey_show_widget=str(data.get("hotkey_show_widget", "ctrl+shift+m")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "poll_interval": self.poll_interval,
            "notify_joins": self.notify_joins,
            "notify_offline": self.notify_offline,
            "notify_online": self.notify_online,
            "notification_start_minute": self.notification_start_minute,
            "notification_end_minute": self.notification_end_minute,
            "notification_dnd_until": self.notification_dnd_until,
            "server_sort_mode": self.server_sort_mode,
            "accent_color": self.accent_color,
            "widget_bg_color": self.widget_bg_color,
            "widget_opacity": self.widget_opacity,
            "widget_accent_color": self.widget_accent_color,
            "hotkey_show_widget": self.hotkey_show_widget,
            "servers": [
                {
                    "name": server.name,
                    "address": server.address,
                    "protocol": server.protocol,
                    "show_in_widget": server.show_in_widget,
                    "group_id": server.group_id,
                    "sort_order": server.sort_order,
                    "pinned": server.pinned,
                    "added_at": server.added_at,
                    "priority_players": list(server.priority_players),
                }
                for server in self.servers
            ],
        }


def app_dir() -> Path:
    """Папка программы: рядом с .exe или с исходниками."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(relative_path: str) -> Path:
    """Путь к ресурсу, в том числе внутри сборки PyInstaller."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    root = Path(bundle_dir) if bundle_dir else Path(__file__).resolve().parent.parent
    return root / relative_path


def user_data_dir() -> Path:
    if not getattr(sys, "frozen", False):
        return app_dir()
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return root / "MC Monitor"


def is_installed_by_setup() -> bool:
    return (app_dir() / "installed.marker").is_file()


def _migrate_legacy_data(source_dir: Path, destination_dir: Path) -> None:
    filenames = (
        "servers.json",
        "window.json",
        "account.session",
        "sync_state.json",
        "mcmonitor.log",
    )
    pending = [
        (source_dir / name, destination_dir / name)
        for name in filenames
        if (source_dir / name).is_file() and not (destination_dir / name).exists()
    ]
    if not pending:
        return
    destination_dir.mkdir(parents=True, exist_ok=True)
    for source, destination in pending:
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            raise ConfigError(f"Не удалось перенести файл {source.name}: {exc}") from exc


def default_config_path() -> Path:
    data_dir = user_data_dir()
    legacy_dir = app_dir()
    if data_dir != legacy_dir:
        _migrate_legacy_data(legacy_dir, data_dir)
    return data_dir / "servers.json"


def load_config(path: Path) -> AppConfig:
    """Читает servers.json; если файла нет — создаёт пустой список серверов."""
    if not path.exists():
        config = AppConfig()
        save_config(path, config)
        return config
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name}: битый JSON — {exc}") from exc
    return AppConfig.from_dict(data)


def save_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8")
