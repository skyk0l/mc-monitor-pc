"""Windows-wide keyboard shortcuts for the floating widget."""
from __future__ import annotations

import ctypes
import re
import sys
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication

_WM_HOTKEY = 0x0312
_HOTKEY_ID = 0x4D43
_MODIFIERS = {
    "ctrl": 0x0002,
    "control": 0x0002,
    "shift": 0x0004,
    "alt": 0x0001,
    "win": 0x0008,
    "meta": 0x0008,
}
_NAMED_KEYS = {
    "space": 0x20,
    "tab": 0x09,
    "backspace": 0x08,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "del": 0x2E,
    "delete": 0x2E,
    "ins": 0x2D,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pgup": 0x21,
    "pageup": 0x21,
    "pgdown": 0x22,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
}


def parse_hotkey(sequence: str) -> tuple[int, int]:
    parts = [part.strip().lower() for part in sequence.split("+") if part.strip()]
    if len(parts) < 2:
        raise ValueError("Глобальная клавиша должна содержать модификатор и клавишу.")

    modifiers = 0
    key = 0
    for part in parts:
        if part in _MODIFIERS:
            modifiers |= _MODIFIERS[part]
            continue
        if key:
            raise ValueError("Поддерживается только одно сочетание клавиш.")
        if re.fullmatch(r"[a-z0-9]", part):
            key = ord(part.upper())
        elif re.fullmatch(r"f(?:[1-9]|1[0-2])", part):
            key = 0x70 + int(part[1:]) - 1
        elif part in _NAMED_KEYS:
            key = _NAMED_KEYS[part]
        else:
            raise ValueError(f"Неизвестная клавиша: {part}.")
    if not modifiers or not key:
        raise ValueError("Не удалось распознать сочетание клавиш.")
    return modifiers, key


class GlobalHotkey(QAbstractNativeEventFilter):
    def __init__(
        self,
        application: QCoreApplication,
        sequence: str,
        callback: Callable[[], None],
    ) -> None:
        super().__init__()
        self._application = application
        self._callback = callback
        self._user32 = None
        self._registered = False
        self._sequence = ""
        if sys.platform != "win32":
            raise RuntimeError("Глобальные клавиши доступны только в Windows.")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.RegisterHotKey.argtypes = [
            wintypes.HWND, wintypes.INT, wintypes.UINT, wintypes.UINT
        ]
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, wintypes.INT]
        self._user32.UnregisterHotKey.restype = wintypes.BOOL
        self.set_sequence(sequence)

    def set_sequence(self, sequence: str) -> None:
        modifiers, key = parse_hotkey(sequence)
        previous = self._sequence
        if self._registered:
            self.unregister()
        try:
            self._register(modifiers, key)
        except OSError:
            if previous:
                old_modifiers, old_key = parse_hotkey(previous)
                self._register(old_modifiers, old_key)
            raise
        self._sequence = sequence

    def _register(self, modifiers: int, key: int) -> None:
        if not self._user32.RegisterHotKey(None, _HOTKEY_ID, modifiers, key):
            error = ctypes.get_last_error()
            raise OSError(error, "Не удалось зарегистрировать глобальное сочетание клавиш.")
        self._registered = True
        self._application.installNativeEventFilter(self)

    def unregister(self) -> None:
        if self._registered and self._user32 is not None:
            if not self._user32.UnregisterHotKey(None, _HOTKEY_ID):
                error = ctypes.get_last_error()
                raise OSError(error, "Не удалось освободить глобальное сочетание клавиш.")
            self._registered = False
            self._application.removeNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):  # noqa: ANN001
        address = message if isinstance(message, int) else getattr(message, "value", None)
        if not address:
            return False, 0
        native_message = ctypes.cast(
            address, ctypes.POINTER(wintypes.MSG)
        ).contents
        if native_message.message == _WM_HOTKEY and native_message.wParam == _HOTKEY_ID:
            self._callback()
            return True, 0
        return False, 0
