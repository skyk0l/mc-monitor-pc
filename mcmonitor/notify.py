"""Правила уведомлений: кто зашёл, кто упал, кто поднялся."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable
from datetime import datetime

from .config import AppConfig
from .i18n import tr
from .models import ServerStatus

TITLE = "MC Monitor"


def notifications_allowed(
    config: AppConfig, now: datetime | None = None
) -> bool:
    current_time = now or datetime.now().astimezone()
    dnd_until = config.notification_dnd_until
    if dnd_until == -1 or (
        dnd_until > 0 and current_time.timestamp() < dnd_until
    ):
        return False

    minute = current_time.hour * 60 + current_time.minute
    start = config.notification_start_minute
    end = config.notification_end_minute
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


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
    if not notifications_allowed(config):
        return None
    if old is None:
        return None  # первая проверка — просто запоминаем состояние
    if old.online and not new.online and config.notify_offline:
        return Notice(TITLE, tr("🔴 {server} недоступен").format(server=server_name))
    if not old.online and new.online and config.notify_online:
        return Notice(
            TITLE,
            tr("🟢 {server} снова онлайн · {players}")
            .format(server=server_name, players=new.players_label),
        )
    if not (old.online and new.online):
        return None

    if not config.notify_joins:
        return None
    joined, left = changed_priority_players(old, new, priority_players)
    if joined or left:
        parts: list[str] = []
        if joined:
            parts.append(tr("зашёл ") + ", ".join(joined[:5]))
        if left:
            parts.append(tr("вышел ") + ", ".join(left[:5]))
        return Notice(
            TITLE,
            tr("🎯 {server}: {changes} · {players}").format(
                server=server_name,
                changes="; ".join(parts),
                players=new.players_label,
            ),
        )
    return None
