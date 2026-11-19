"""Запросы к публичным API статуса Minecraft-серверов.

Основной провайдер — api.mcsrvstat.us, запасной — api.mcstatus.io.
Если первый не ответил, пробуем второй, чтобы монитор не мигал «офлайн».
"""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Callable

from .config import ServerConfig
from .models import ServerStatus

USER_AGENT = "mcmonitor/1.1 (Minecraft server monitor)"
DEFAULT_TIMEOUT = 8.0

# §a, §l и прочие коды форматирования Minecraft
_FORMAT_CODE = re.compile(r"§.")

Opener = Callable[[str, float], Any]


def _get_json(url: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _clean(text: str) -> str:
    return _FORMAT_CODE.sub("", text).strip()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _names(raw: Any) -> list[str]:
    """Достаёт ники из списка игроков любого из провайдеров."""
    names: list[str] = []
    if not isinstance(raw, list):
        return names
    for item in raw:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("name") or item.get("name_clean") or item.get("name_raw") or ""
        else:
            name = ""
        name = str(name).strip()
        if name:
            names.append(name)
    return names


def mcsrvstat_url(server: ServerConfig) -> str:
    return f"https://api.mcsrvstat.us/3/{server.address}"


def mcsrvstat_bedrock_url(server: ServerConfig) -> str:
    return f"https://api.mcsrvstat.us/bedrock/3/{server.address}"


def mcstatus_url(server: ServerConfig) -> str:
    url = f"https://api.mcstatus.io/v2/status/java/{server.host}"
    if server.port:
        url += f"?port={server.port}"
    return url


def mcstatus_bedrock_url(server: ServerConfig) -> str:
    url = f"https://api.mcstatus.io/v2/status/bedrock/{server.host}"
    if server.port:
        url += f"?port={server.port}"
    return url


def parse_mcsrvstat(data: Any) -> ServerStatus:
    if not isinstance(data, dict):
        raise ValueError("ответ mcsrvstat — не объект")
    players = data.get("players") or {}
    motd = data.get("motd") or {}
    clean = motd.get("clean")
    if isinstance(clean, list):
        motd_text = "\n".join(str(line) for line in clean)
    else:
        motd_text = str(clean or motd.get("raw") or "")
    version = data.get("version") or ""
    if isinstance(version, dict):
        version = version.get("name", "")
    online = bool(data.get("online"))
    return ServerStatus(
        online=online,
        online_count=_as_int(players.get("online")) if online else 0,
        max_players=_as_int(players.get("max")) if online else 0,
        players=_names(players.get("list")) if online else [],
        version=str(version),
        motd=_clean(motd_text),
    )


def parse_mcstatus(data: Any) -> ServerStatus:
    if not isinstance(data, dict):
        raise ValueError("ответ mcstatus — не объект")
    players = data.get("players") or {}
    motd = data.get("motd") or {}
    clean = motd.get("clean")
    if isinstance(clean, list):
        motd_text = "\n".join(str(line) for line in clean)
    else:
        motd_text = str(clean or motd.get("raw") or "")
    version = data.get("version") or ""
    if isinstance(version, dict):
        version = version.get("name_clean") or version.get("name_raw") or ""
    online = bool(data.get("online"))
    return ServerStatus(
        online=online,
        online_count=_as_int(players.get("online")) if online else 0,
        max_players=_as_int(players.get("max")) if online else 0,
        players=_names(players.get("list")) if online else [],
        version=str(version),
        motd=_clean(motd_text),
    )


def fetch_status(
    server: ServerConfig,
    timeout: float = DEFAULT_TIMEOUT,
    opener: Opener | None = None,
) -> ServerStatus:
    """Проверяет сервер: сначала основной API, затем запасной."""
    opener = opener or _get_json
    problems: list[str] = []
    if server.protocol == "bedrock":
        providers = (
            (mcsrvstat_bedrock_url(server), parse_mcsrvstat),
            (mcstatus_bedrock_url(server), parse_mcstatus),
        )
    else:
        providers = (
            (mcsrvstat_url(server), parse_mcsrvstat),
            (mcstatus_url(server), parse_mcstatus),
        )
    for url, parser in providers:
        try:
            data = opener(url, timeout)
        except Exception as exc:  # сеть, таймаут, битый JSON, 404
            problems.append(f"{type(exc).__name__}: {exc}")
            continue
        try:
            return parser(data)
        except Exception as exc:
            problems.append(f"разбор ответа: {exc}")
    return ServerStatus(online=False, error="; ".join(problems) or "нет ответа")
