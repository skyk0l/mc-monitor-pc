"""Иконки приложения и трея на основе общего знака MC Monitor."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ONLINE = (76, 200, 92)
OFFLINE = (214, 69, 69)


def _base_icon(size: int) -> Image.Image:
    from .config import resource_path

    image = Image.open(resource_path("assets/icon.png")).convert("RGBA")
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
    return canvas


def make_icon(online: bool = True, size: int = 64) -> Image.Image:
    """Использует иконку Android и добавляет точку текущего статуса в трее."""
    image = _base_icon(size)

    dot = ImageDraw.Draw(image)
    radius = max(6, size // 6)
    left = size - radius - 3
    top = size - radius - 3
    dot.ellipse(
        [left - 2, top - 2, left + radius + 2, top + radius + 2],
        fill=(250, 250, 250, 255),
    )
    dot.ellipse(
        [left, top, left + radius, top + radius],
        fill=(ONLINE if online else OFFLINE) + (255,),
    )
    return image


def save_ico(path: Path, online: bool = True) -> None:
    """Сохраняет многоразмерный .ico для ярлыка и PyInstaller."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = _base_icon(256)
    image.save(
        path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def save_png(path: Path, online: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _base_icon(256).save(path, format="PNG")
