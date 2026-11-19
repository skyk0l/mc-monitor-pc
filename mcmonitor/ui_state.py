"""Положение, прозрачность и закрепление плавающего виджета."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIN_OPACITY = 0.5
MAX_OPACITY = 1.0
DEFAULT_OPACITY = 0.93


@dataclass
class WindowState:
    x: int | None = None
    y: int | None = None
    opacity: float = DEFAULT_OPACITY
    always_on_top: bool = True
    widget_visible: bool = True

    def clamped(self) -> "WindowState":
        return WindowState(
            x=self.x,
            y=self.y,
            opacity=min(MAX_OPACITY, max(MIN_OPACITY, float(self.opacity))),
            always_on_top=bool(self.always_on_top),
            widget_visible=bool(self.widget_visible),
        )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_state(path: Path) -> WindowState:
    if not path.exists():
        return WindowState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return WindowState()
    if not isinstance(data, dict):
        return WindowState()
    return WindowState(
        x=_int_or_none(data.get("x")),
        y=_int_or_none(data.get("y")),
        opacity=_float(data.get("opacity"), DEFAULT_OPACITY),
        always_on_top=bool(data.get("always_on_top", True)),
        widget_visible=bool(data.get("widget_visible", True)),
    ).clamped()


def save_state(path: Path, state: WindowState) -> None:
    payload = {
        "x": state.x,
        "y": state.y,
        "opacity": round(state.opacity, 2),
        "always_on_top": state.always_on_top,
        "widget_visible": state.widget_visible,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
