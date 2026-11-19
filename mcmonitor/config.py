"""Настройки приложения: servers.json рядом с программой."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_INTERVAL = 60
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
            server = replace(
                server,
                show_in_widget=bool(value.get("show_in_widget", False)),
                group_id=str(value.get("group_id") or ""),
                sort_order=int(value.get("sort_order") or 0),
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
        return cls(
            servers=servers,
            poll_interval=max(MIN_INTERVAL, interval),
            notify_joins=bool(data.get("notify_joins", True)),
            notify_offline=bool(data.get("notify_offline", True)),
            notify_online=bool(data.get("notify_online", True)),
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


def default_config_path() -> Path:
    return app_dir() / "servers.json"


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
