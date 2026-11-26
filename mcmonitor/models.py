"""Общие модели данных."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ServerStatus:
    """Снимок состояния одного сервера."""

    online: bool = False
    online_count: int = 0
    max_players: int = 0
    players: list[str] = field(default_factory=list)
    version: str = ""
    motd: str = ""
    favicon: str = ""
    error: str = ""
    ping_ms: int | None = None

    @property
    def players_label(self) -> str:
        """Короткая подпись для меню: «3/20» или «офлайн»."""
        if not self.online:
            return "офлайн"
        if self.max_players:
            return f"{self.online_count}/{self.max_players}"
        return str(self.online_count)

    @property
    def unique_players(self) -> list[str]:
        """Ники без повторов, по алфавиту — так список не «прыгает»."""
        seen: set[str] = set()
        result: list[str] = []
        for name in self.players:
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(name)
        return sorted(result, key=str.casefold)

    @property
    def hides_player_list(self) -> bool:
        """Сервер онлайн и кто-то есть, но ники не отдаёт."""
        return self.online and self.online_count > 0 and not self.players

    @property
    def first_motd_line(self) -> str:
        for line in self.motd.splitlines():
            line = line.strip()
            if line:
                return line[:80]
        return ""
