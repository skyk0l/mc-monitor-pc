"""Правила уведомлений: кто зашёл, кто упал, кто поднялся."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

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


def changed_priority_players(
    old: ServerStatus | None,
    new: ServerStatus,
    priorities: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Return joined and left players restricted to the configured watch list."""
    if not old or not old.online or not new.online:
        return [], []
    watched = {name.casefold() for name in priorities if name.strip()}
    if not watched:
        return [], []
    before = {name.casefold(): name for name in old.players}
    after = {name.casefold(): name for name in new.players}
    joined = [after[name] for name in watched & (after.keys() - before.keys())]
    left = [before[name] for name in watched & (before.keys() - after.keys())]
    return sorted(joined, key=str.casefold), sorted(left, key=str.casefold)


def change_notice(
    server_name: str,
    old: ServerStatus | None,
    new: ServerStatus,
    config: AppConfig,
    priority_players: Iterable[str] = (),
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

    if not config.notify_joins:
        return None
    joined, left = changed_priority_players(old, new, priority_players)
    if joined or left:
        parts: list[str] = []
        if joined:
            parts.append("зашёл " + ", ".join(joined[:5]))
        if left:
            parts.append("вышел " + ", ".join(left[:5]))
        return Notice(TITLE, f"🎯 {server_name}: {'; '.join(parts)} · {new.players_label}")
    return None
