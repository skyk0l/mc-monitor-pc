"""Правила уведомлений: кто зашёл, кто упал, кто поднялся."""
from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig
from .models import ServerStatus

TITLE = "MC Monitor"


@dataclass(frozen=True)
class Notice:
    title: str
    message: str


def new_players(old: ServerStatus | None, new: ServerStatus) -> list[str]:
    """Ники, которых не было в прошлой проверке."""
    if not old or not old.online or not new.online:
        return []
    seen = set(old.players)
    return [name for name in new.players if name not in seen]


def change_notice(
    server_name: str,
    old: ServerStatus | None,
    new: ServerStatus,
    config: AppConfig,
) -> Notice | None:
    """Сравнивает два снимка и решает, стоит ли пищать."""
    if old is None:
        return None  # первая проверка — просто запоминаем состояние
    if old.online and not new.online and config.notify_offline:
        return Notice(TITLE, f"🔴 {server_name} недоступен")
    if not old.online and new.online and config.notify_online:
        return Notice(TITLE, f"🟢 {server_name} снова онлайн · {new.players_label}")
    if not (old.online and new.online):
        return None

    joined = new_players(old, new)
    if joined and config.notify_joins:
        preview = ", ".join(joined[:5])
        if len(joined) > 5:
            preview += " и др."
        if len(joined) == 1:
            message = f"🎮 {server_name}: зашёл {preview} · {new.players_label}"
        else:
            message = f"🎮 {server_name}: зашли {preview} · {new.players_label}"
        return Notice(TITLE, message)

    if new.online_count > old.online_count and config.notify_joins:
        delta = new.online_count - old.online_count
        # Many big servers hide the player list — тогда считаем по количеству.
        if delta == 1:
            word = "игрок"
        elif 2 <= delta % 10 <= 4 and not 12 <= delta % 100 <= 14:
            word = "игрока"
        else:
            word = "игроков"
        return Notice(TITLE, f"🎮 {server_name}: +{delta} {word} · {new.players_label}")
    return None
