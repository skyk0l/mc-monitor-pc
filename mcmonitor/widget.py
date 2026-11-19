"""Плавающий виджет: полупрозрачный, перетаскиваемый, с мягкими анимациями."""
from __future__ import annotations

import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation,
    Property,
    QEasingCurve,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from . import __version__, api
from .auto_update import check_for_updates
from .config import AppConfig, ConfigError, ServerConfig, load_config, resource_path
from .models import ServerStatus
from .notify import change_notice
from .ui_state import WindowState, load_state, save_state

log = logging.getLogger(__name__)

CARD_WIDTH = 300
CARD_MARGIN = 20
RADIUS = 26.0
HEADER_HEIGHT = 32
ROW_HEIGHT = 60
FOOTER_HEIGHT = 28
BUTTON_SIZE = 22

CHIP_HEIGHT = 25.0
CHIP_GAP = 7.0
CHIP_PAD = 24.0
CHIP_FONT_SIZE = 11
PANEL_PAD = 11.0
PANEL_SIDE = 14.0
PANEL_HEADER = 18.0
PANEL_MAX_HEIGHT = 190.0
PANEL_MIN_HEIGHT = 66.0

TEXT = QColor("#eef2f8")
SOFT = QColor(170, 179, 196)
DIM = QColor(116, 124, 141)
MINT = QColor("#7ee0a3")
CORAL = QColor("#ff8391")
AMBER = QColor("#ffc978")

CHIP_TINTS = (
    QColor("#7ee0a3"),  # мята
    QColor("#9ec8ff"),  # небо
    QColor("#d3b4ff"),  # лаванда
    QColor("#ffb3c7"),  # роза
    QColor("#ffd79a"),  # персик
    QColor("#9ff0e0"),  # аквамарин
)

MENU_QSS = """
QMenu {
    background-color: #1c1f28;
    color: #e7ecf3;
    border: 1px solid rgba(255, 255, 255, 26);
    padding: 6px;
}
QMenu::item {
    padding: 7px 22px 7px 16px;
    border-radius: 6px;
}
QMenu::item:selected {
    background-color: rgba(255, 255, 255, 22);
}
QMenu::separator {
    height: 1px;
    background: rgba(255, 255, 255, 18);
    margin: 5px 10px;
}
QMenu::indicator {
    width: 12px;
    height: 12px;
}
"""


def _font(size: int, weight: QFont.Weight = QFont.Weight.Normal, spacing: float = 0.0) -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Arial"])
    font.setPixelSize(size)
    font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return font


class _Bridge(QObject):
    """Переносит результат из потока проверки в поток интерфейса."""

    done = Signal(object, object)


class StatusWidget(QWidget):
    def __init__(self, app: "WidgetApp") -> None:
        super().__init__(None)
        self.app = app
        self.statuses = app.statuses
        self.state = app.window_state
        self._hover = 0.0
        self._pulse = 0.0
        self._spin = 0.0
        self._loading = False
        self._last_update = 0.0
        self._drag_offset: QPoint | None = None
        self._press_pos: QPoint | None = None
        self._dragging = False
        self._pressed_button: str | None = None
        self._hovered_button: str | None = None
        self._hovered_row: str | None = None
        self._refresh_rect = QRectF()
        self._close_rect = QRectF()
        self._closing = False
        self.display: dict[str, float] = {}
        self._count_anims: dict[str, QVariantAnimation] = {}
        self._brand_logo = QPixmap(str(resource_path("assets/mcm-logo.png")))

        self.expanded: str | None = None
        self._extra = 0.0
        self._extra_anim: QVariantAnimation | None = None
        self._player_scroll: dict[str, float] = {}
        self._player_area = QRectF()
        self._player_viewport = QRectF()

        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if self.state.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle("MC Monitor")
        self.setToolTip(
            "Клик по серверу — список игроков · Перетащи карточку · ПКМ — меню · Ctrl+колесо — прозрачность"
        )

        self._hover_anim = QPropertyAnimation(self, b"hover", self)
        self._hover_anim.setDuration(170)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._pulse_anim = QVariantAnimation(self)
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setDuration(2600)
        self._pulse_anim.setLoopCount(-1)
        self._pulse_anim.setEasingCurve(QEasingCurve.InOutSine)
        self._pulse_anim.valueChanged.connect(self._on_pulse)
        self._pulse_anim.start()

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(45)
        self._spin_timer.timeout.connect(self._tick_spin)

        self.resize_to_content()
        self.restore_position()
        self.setWindowOpacity(self.state.opacity)

    # --- плавные свойства ------------------------------------------------
    def _get_hover(self) -> float:
        return self._hover

    def _set_hover(self, value: float) -> None:
        self._hover = float(value)
        self.update()

    hover = Property(float, _get_hover, _set_hover)

    def _on_pulse(self, value: object) -> None:
        self._pulse = float(value)  # type: ignore[arg-type]
        if self.isVisible():
            self.update()

    def _tick_spin(self) -> None:
        self._spin = (self._spin + 16) % 360
        self.update()

    # --- геометрия -------------------------------------------------------
    def _widget_servers(self) -> list[ServerConfig]:
        return [server for server in self.app.config.servers if server.show_in_widget]

    def _row_count(self) -> int:
        return max(1, len(self._widget_servers()))

    def _base_card_height(self) -> float:
        return HEADER_HEIGHT + ROW_HEIGHT * self._row_count() + FOOTER_HEIGHT + 4

    def _card_height(self) -> float:
        return self._base_card_height() + self._extra

    def _card_rect(self) -> QRectF:
        return QRectF(CARD_MARGIN, CARD_MARGIN, CARD_WIDTH, self._card_height())

    def _row_layout(self) -> list[tuple[ServerConfig, QRectF]]:
        """Прямоугольники строк с учётом раскрытого списка игроков."""
        card = self._card_rect()
        top = card.top() + HEADER_HEIGHT
        rows: list[tuple[ServerConfig, QRectF]] = []
        for server in self._widget_servers():
            rows.append(
                (server, QRectF(card.left() + 8, top, card.width() - 16, ROW_HEIGHT))
            )
            top += ROW_HEIGHT
            if self.expanded == server.address:
                top += self._extra
        return rows

    def resize_to_content(self) -> None:
        self.resize(CARD_WIDTH + CARD_MARGIN * 2, int(self._card_height()) + CARD_MARGIN * 2)

    def restore_position(self) -> None:
        if self.state.x is not None and self.state.y is not None:
            self.move(self.state.x, self.state.y)
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 28, area.top() + 90)

    # --- список игроков --------------------------------------------------
    def _chip_layout(
        self, names: list[str], width: float
    ) -> tuple[list[tuple[str, QRectF]], float]:
        """Раскладывает ники «облачком» из плашек и считает высоту."""
        font = _font(CHIP_FONT_SIZE, QFont.Weight.DemiBold)
        metrics = QFontMetricsF(font)
        chips: list[tuple[str, QRectF]] = []
        x = 0.0
        y = 0.0
        for name in names:
            label = metrics.elidedText(name, Qt.ElideRight, width - CHIP_PAD)
            chip_width = min(width, metrics.horizontalAdvance(label) + CHIP_PAD)
            if x > 0 and x + chip_width > width:
                x = 0.0
                y += CHIP_HEIGHT + CHIP_GAP
            chips.append((label, QRectF(x, y, chip_width, CHIP_HEIGHT)))
            x += chip_width + CHIP_GAP
        height = y + CHIP_HEIGHT if chips else 0.0
        return chips, height

    def _list_height(self, address: str) -> float:
        status = self.statuses.get(address)
        if status is None or not status.online or not status.players:
            return PANEL_MIN_HEIGHT
        width = CARD_WIDTH - 2 * PANEL_SIDE - 2 * PANEL_PAD
        _, chips_height = self._chip_layout(status.unique_players, width)
        height = PANEL_PAD * 2 + PANEL_HEADER + chips_height + 4
        return min(PANEL_MAX_HEIGHT, max(PANEL_MIN_HEIGHT, height))

    def _set_extra(self, value: object) -> None:
        self._extra = max(0.0, float(value))  # type: ignore[arg-type]
        self.resize_to_content()
        self.update()

    def _animate_extra(self, target: float, done=None) -> None:  # noqa: ANN001
        if self._extra_anim is not None:
            self._extra_anim.stop()
        anim = QVariantAnimation(self)
        anim.setDuration(240)
        anim.setStartValue(self._extra)
        anim.setEndValue(float(target))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._set_extra)
        if done is not None:
            anim.finished.connect(done)
        self._extra_anim = anim
        anim.start()

    def _fit_for_height(self, extra: float) -> None:
        """Если карточка вырастает за низ экрана — чуть поднимаем её."""
        screen = QApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        new_height = int(self._base_card_height() + extra) + CARD_MARGIN * 2
        overflow = self.y() + new_height - (area.bottom() - 4)
        if overflow > 0:
            self.move(self.x(), max(area.top() + 4, self.y() - overflow))

    def toggle_players(self, address: str) -> None:
        if self.expanded == address:
            self._animate_extra(0.0, done=lambda: self._collapse_done(address))
            return
        self.expanded = address
        self._player_scroll[address] = 0.0
        target = self._list_height(address)
        self._fit_for_height(target)
        self._animate_extra(target)

    def _collapse_done(self, address: str) -> None:
        if self.expanded == address and self._extra <= 0.5:
            self.expanded = None
            self._player_scroll.pop(address, None)
            self.resize_to_content()
            self.update()

    def _sync_expanded(self) -> None:
        if not self.expanded:
            return
        if self._extra_anim is not None:
            if self._extra_anim.state() == QAbstractAnimation.State.Running:
                return
        target = self._list_height(self.expanded)
        if abs(target - self._extra) > 4:
            self._fit_for_height(target)
            self._animate_extra(target)

    # --- состояние -------------------------------------------------------
    def mark_loading(self, value: bool) -> None:
        if value == self._loading:
            return
        self._loading = value
        if value:
            self._spin_timer.start()
        else:
            self._spin_timer.stop()
            self._spin = 0
        self.update()

    def mark_updated(self) -> None:
        self._last_update = time.time()
        self.update()

    def status_changed(self, address: str) -> None:
        status = self.statuses.get(address)
        if status and status.online:
            self._animate_count(address, status.online_count)
        else:
            self.display.pop(address, None)
        if self.expanded == address:
            self._sync_expanded()
        self.update()

    def _animate_count(self, address: str, target: int) -> None:
        start = self.display.get(address, float(target))
        if abs(start - target) < 0.5:
            self.display[address] = float(target)
            return
        old = self._count_anims.get(address)
        if old is not None:
            old.stop()
        anim = QVariantAnimation(self)
        anim.setStartValue(float(start))
        anim.setEndValue(float(target))
        anim.setDuration(420)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(lambda value, a=address: self._set_display(a, value))
        self._count_anims[address] = anim
        anim.start()

    def _set_display(self, address: str, value: object) -> None:
        self.display[address] = float(value)  # type: ignore[arg-type]
        self.update()

    # --- анимации окна ---------------------------------------------------
    def animate_in(self) -> None:
        target = self.pos()
        self.setWindowOpacity(0.0)
        self.move(target + QPoint(0, 10))
        pos_anim = QPropertyAnimation(self, b"pos", self)
        pos_anim.setDuration(340)
        pos_anim.setStartValue(self.pos())
        pos_anim.setEndValue(target)
        pos_anim.setEasingCurve(QEasingCurve.OutCubic)
        op_anim = QPropertyAnimation(self, b"windowOpacity", self)
        op_anim.setDuration(300)
        op_anim.setStartValue(0.0)
        op_anim.setEndValue(self.state.opacity)
        op_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._intro = (pos_anim, op_anim)
        pos_anim.start()
        op_anim.start()

    def animate_out(self, callback) -> None:  # noqa: ANN001
        if self._closing:
            return
        self._closing = True
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(180)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.finished.connect(callback)
        self._out_anim = anim
        anim.start()

    # --- отрисовка -------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        card = self._card_rect()
        self._paint_shadow(painter, card)
        self._paint_card(painter, card)
        self._paint_header(painter, card)
        self._paint_rows(painter, card)
        self._paint_footer(painter, card)
        painter.end()

    def _paint_shadow(self, painter: QPainter, card: QRectF) -> None:
        """Мягкая плоская тень по контуру карточки, без круглого пятна."""
        painter.setPen(Qt.NoPen)
        shadow = card.translated(0.0, 6.0)
        for spread, alpha in (
            (13.5, 4),
            (11.5, 5),
            (9.5, 6),
            (7.5, 7),
            (6.0, 8),
            (4.5, 9),
            (3.0, 10),
            (1.5, 11),
            (0.5, 13),
        ):
            rect = shadow.adjusted(-spread, -spread * 0.15, spread, spread)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(rect, RADIUS + spread, RADIUS + spread)

    def _paint_card(self, painter: QPainter, card: QRectF) -> None:
        path = QPainterPath()
        path.addRoundedRect(card, RADIUS, RADIUS)
        base_color = QColor(self.app.config.widget_bg_color)
        if not base_color.isValid():
            base_color = QColor("#252B30")
        opacity = max(0.35, min(1.0, self.app.config.widget_opacity))
        top_color = base_color.lighter(118)
        middle_color = QColor(base_color)
        bottom_color = base_color.darker(112)
        for color in (top_color, middle_color, bottom_color):
            color.setAlpha(round(255 * opacity))
        gradient = QLinearGradient(card.topLeft(), card.bottomLeft())
        gradient.setColorAt(0.0, top_color)
        gradient.setColorAt(0.55, middle_color)
        gradient.setColorAt(1.0, bottom_color)
        painter.fillPath(path, gradient)

        sheen = QRadialGradient(QPointF(card.center().x(), card.top()), card.width() * 0.8)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 16))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(card, sheen)
        painter.restore()

        border = QPainterPath()
        border.addRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        painter.setBrush(Qt.NoBrush)
        border_color = QColor(self.app.config.widget_accent_color)
        if not border_color.isValid():
            border_color = QColor("#A7C6A4")
        border_color.setAlpha(int(18 + 24 * self._hover))
        painter.setPen(QPen(border_color, 1))
        painter.drawPath(border)

    def _paint_header(self, painter: QPainter, card: QRectF) -> None:
        if not self._brand_logo.isNull():
            logo = self._brand_logo.scaled(
                118, 19, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            painter.drawPixmap(
                QPointF(card.left() + 17, card.top() + (HEADER_HEIGHT - logo.height()) / 2),
                logo,
            )

        selected = self._widget_servers()
        group_names = {server.group_id for server in selected if server.group_id}
        if selected:
            title = next(iter(group_names)) if len(group_names) == 1 else "ИЗБРАННОЕ"
            title_font = _font(8, QFont.Weight.DemiBold, 0.8)
            painter.setFont(title_font)
            painter.setPen(QColor(160, 174, 191, 220))
            title_rect = QRectF(
                card.left() + 142,
                card.top() + 10,
                max(0, card.width() - 142 - 2 * BUTTON_SIZE - 40),
                HEADER_HEIGHT - 20,
            )
            painter.drawText(
                title_rect,
                Qt.AlignLeft | Qt.AlignVCenter,
                QFontMetricsF(title_font).elidedText(
                    title, Qt.ElideRight, max(0, int(title_rect.width()))
                ),
            )

        self._close_rect = QRectF(
            card.right() - 14 - BUTTON_SIZE,
            card.top() + (HEADER_HEIGHT - BUTTON_SIZE) / 2,
            BUTTON_SIZE,
            BUTTON_SIZE,
        )
        self._refresh_rect = self._close_rect.translated(-(BUTTON_SIZE + 6), 0)
        buttons_alpha = max(self._hover, 0.35 if self._loading else 0.0)

        cursor_pos = self.mapFromGlobal(QCursor.pos())
        self._hovered_button = None
        if buttons_alpha > 0.05:
            for name, rect in (("refresh", self._refresh_rect), ("close", self._close_rect)):
                hovered = self.underMouse() and rect.contains(cursor_pos)
                if hovered:
                    self._hovered_button = name
                if hovered:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(255, 255, 255, 20))
                    painter.drawEllipse(rect.adjusted(1, 1, -1, -1))

        glyph_alpha = int(235 * min(1.0, buttons_alpha + 0.15))
        glyph_color = QColor(self.app.config.widget_accent_color)
        if not glyph_color.isValid():
            glyph_color = QColor(198, 206, 222)
        glyph_color.setAlpha(glyph_alpha)
        painter.setPen(glyph_color)
        painter.save()
        painter.translate(self._refresh_rect.center())
        if self._loading:
            painter.rotate(self._spin)
        painter.setFont(_font(15))
        painter.drawText(QRectF(-12, -12, 24, 24), Qt.AlignCenter, "↻")
        painter.restore()

        painter.setPen(glyph_color)
        painter.setFont(_font(10, QFont.Weight.DemiBold))
        painter.drawText(self._close_rect, Qt.AlignCenter, "✕")

    def _paint_rows(self, painter: QPainter, card: QRectF) -> None:
        servers = self._widget_servers()
        top = card.top() + HEADER_HEIGHT
        if not servers:
            painter.setFont(_font(13, QFont.Weight.DemiBold))
            painter.setPen(SOFT)
            message = "Нет серверов" if not self.app.config.servers else "Виджет пока пуст"
            painter.drawText(QPointF(card.left() + 34, top + 24), message)
            painter.setFont(_font(10))
            painter.setPen(DIM)
            painter.drawText(
                QPointF(card.left() + 34, top + 42),
                "Добавь сервер в окне приложения" if not self.app.config.servers
                else "Выбери «Показать в виджете» в меню сервера",
            )
            return
        self._player_area = QRectF()
        for server, row in self._row_layout():
            if self._hovered_row == server.address and self._hover > 0.12:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 255, 255, 7))
                painter.drawRoundedRect(row.adjusted(0, 2, 0, -2), 15, 15)
            self._paint_row(painter, card, row, server)
            if self.expanded == server.address and self._extra > 1:
                self._paint_player_panel(painter, card, row.bottom() + 2, server)

    def _paint_row(
        self, painter: QPainter, card: QRectF, row: QRectF, server: ServerConfig
    ) -> None:
        row_top = row.top()
        status = self.statuses.get(server.address)
        online = status.online if status else None
        online_color = QColor(self.app.config.widget_accent_color)
        if not online_color.isValid():
            online_color = MINT
        color = online_color if online else CORAL if online is False else AMBER
        dot = QPointF(card.left() + 20, row_top + 20)

        if online:
            glow_alpha = int(52 + 38 * math.sin(self._pulse * math.pi))
            glow = QRadialGradient(dot, 11)
            glow.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), glow_alpha))
            glow.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(dot, 11, 11)

        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(dot, 4.2, 4.2)

        name_font = _font(14, QFont.Weight.DemiBold)
        painter.setFont(name_font)
        painter.setPen(TEXT if online else QColor(188, 196, 211, 215))
        name = QFontMetricsF(name_font).elidedText(server.name, Qt.ElideRight, 150)
        painter.drawText(QPointF(card.left() + 34, row_top + 22), name)

        chevron_x = card.left() + 34 + QFontMetricsF(name_font).horizontalAdvance(name) + 12
        self._paint_chevron(painter, QPointF(chevron_x, row_top + 17), self.expanded == server.address)

        if status is None:
            sub = "проверяю…"
        elif status.online:
            sub = status.version or "версия неизвестна"
        else:
            sub = "сервер не отвечает"
        sub_font = _font(10)
        painter.setFont(sub_font)
        painter.setPen(QColor(126, 134, 151, 215))
        sub = QFontMetricsF(sub_font).elidedText(sub, Qt.ElideRight, 168)
        painter.drawText(QPointF(card.left() + 34, row_top + 40), sub)

        right = card.right() - 18
        if status and status.online:
            value = str(int(round(self.display.get(server.address, status.online_count))))
            suffix = f" / {status.max_players}" if status.max_players else ""
            num_font = _font(26, QFont.Weight.DemiBold)
            suffix_font = _font(12)
            num_width = QFontMetricsF(num_font).horizontalAdvance(value)
            x = right - num_width - QFontMetricsF(suffix_font).horizontalAdvance(suffix)
            painter.setFont(num_font)
            painter.setPen(TEXT)
            painter.drawText(QPointF(x, row_top + 27), value)
            painter.setFont(suffix_font)
            painter.setPen(QColor(126, 134, 151, 220))
            painter.drawText(QPointF(x + num_width, row_top + 27), suffix)
            label, label_color = "онлайн", online_color
        elif status is None:
            painter.setFont(_font(20, QFont.Weight.DemiBold))
            painter.setPen(AMBER)
            painter.drawText(
                QRectF(right - 90, row_top + 2, 90, 28), Qt.AlignRight | Qt.AlignVCenter, "…"
            )
            label, label_color = "проверяю", AMBER
        else:
            painter.setFont(_font(20, QFont.Weight.DemiBold))
            painter.setPen(QColor(120, 128, 145, 220))
            painter.drawText(
                QRectF(right - 90, row_top + 2, 90, 28), Qt.AlignRight | Qt.AlignVCenter, "—"
            )
            label, label_color = "офлайн", CORAL

        painter.setFont(_font(10))
        painter.setPen(label_color)
        painter.drawText(
            QRectF(right - 100, row_top + 30, 100, 14),
            Qt.AlignRight | Qt.AlignVCenter,
            label,
        )

    def _paint_chevron(self, painter: QPainter, anchor: QPointF, opened: bool) -> None:
        painter.save()
        painter.translate(anchor)
        if opened:
            painter.rotate(180)
        pen = QPen(QColor(132, 141, 159, 175), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath(QPointF(-4.0, -2.0))
        path.lineTo(0.0, 2.5)
        path.lineTo(4.0, -2.0)
        painter.drawPath(path)
        painter.restore()

    def _paint_player_panel(
        self, painter: QPainter, card: QRectF, panel_top: float, server: ServerConfig
    ) -> None:
        height = self._extra
        inner = QRectF(
            card.left() + PANEL_SIDE,
            panel_top,
            card.width() - 2 * PANEL_SIDE,
            max(1.0, height - 4),
        )
        self._player_area = inner

        path = QPainterPath()
        path.addRoundedRect(inner, 16, 16)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 9))
        painter.drawPath(path)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 14), 1))
        painter.drawPath(path)

        status = self.statuses.get(server.address)
        left = inner.left() + PANEL_PAD
        header_baseline = inner.top() + PANEL_PAD + 10
        painter.setFont(_font(8, QFont.Weight.DemiBold, 1.4))
        painter.setPen(QColor(138, 147, 165, 190))
        painter.drawText(QPointF(left, header_baseline), "ИГРОКИ")

        names = status.unique_players if status and status.online else []
        hint = ""
        if status is not None and status.online and status.hides_player_list:
            hint = "список скрыт"
        elif names and len(names) < (status.online_count if status else 0):
            hint = f"{len(names)} из {status.online_count}" if status else ""
        if hint:
            painter.setFont(_font(9))
            painter.setPen(QColor(126, 134, 151, 185))
            painter.drawText(
                QRectF(inner.right() - PANEL_PAD - 130, header_baseline - 10, 130, 14),
                Qt.AlignRight | Qt.AlignVCenter,
                hint,
            )

        content_top = inner.top() + PANEL_PAD + PANEL_HEADER
        viewport = QRectF(
            left,
            content_top,
            inner.width() - 2 * PANEL_PAD,
            max(1.0, inner.bottom() - PANEL_PAD - content_top),
        )
        self._player_viewport = viewport

        title = subtitle = ""
        if status is None:
            title, subtitle = "Проверяю…", "Список появится через секунду"
        elif not status.online:
            title, subtitle = "Сервер сейчас офлайн", "Ники увидим, когда он оживёт"
        elif status.hides_player_list:
            title, subtitle = "Сервер не показывает ники", "Видно только количество игроков"
        elif not names:
            title, subtitle = "Сейчас никого", "Заходи первым 🌙"

        if title:
            painter.setFont(_font(11, QFont.Weight.DemiBold))
            painter.setPen(QColor(206, 214, 228, 235))
            painter.drawText(
                QRectF(viewport.left(), viewport.top(), viewport.width(), 16),
                Qt.AlignLeft | Qt.AlignVCenter,
                title,
            )
            if subtitle:
                painter.setFont(_font(9))
                painter.setPen(QColor(120, 128, 145, 210))
                painter.drawText(
                    QRectF(viewport.left(), viewport.top() + 15, viewport.width(), 14),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    subtitle,
                )
            return

        chips, content_height = self._chip_layout(names, viewport.width())
        max_scroll = max(0.0, content_height - viewport.height())
        scroll = min(max(0.0, self._player_scroll.get(server.address, 0.0)), max_scroll)
        self._player_scroll[server.address] = scroll

        painter.save()
        painter.setClipRect(viewport)
        painter.translate(viewport.topLeft())
        chip_font = _font(CHIP_FONT_SIZE, QFont.Weight.DemiBold, 0.2)
        painter.setFont(chip_font)
        for index, (label, chip) in enumerate(chips):
            rect = chip.translated(0, -scroll)
            if rect.bottom() < 0 or rect.top() > viewport.height():
                continue
            tint = CHIP_TINTS[index % len(CHIP_TINTS)]
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(tint.red(), tint.green(), tint.blue(), 28))
            painter.drawRoundedRect(rect, 12.5, 12.5)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(tint.red(), tint.green(), tint.blue(), 66), 1))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 12.5, 12.5)
            painter.setPen(QColor(238, 245, 252, 245))
            painter.drawText(rect, Qt.AlignCenter, label)
        painter.restore()

        if max_scroll > 1:
            track = QRectF(
                inner.right() - PANEL_PAD + 2,
                viewport.top(),
                2.5,
                viewport.height(),
            )
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 14))
            painter.drawRoundedRect(track, 1.25, 1.25)
            thumb_height = max(18.0, track.height() * viewport.height() / content_height)
            offset = (track.height() - thumb_height) * (scroll / max_scroll)
            painter.setBrush(QColor(210, 220, 235, 80))
            painter.drawRoundedRect(
                QRectF(track.x(), track.y() + offset, track.width(), thumb_height),
                1.25,
                1.25,
            )

    def _paint_footer(self, painter: QPainter, card: QRectF) -> None:
        footer_top = card.bottom() - FOOTER_HEIGHT
        painter.setPen(QPen(QColor(255, 255, 255, 12), 1))
        painter.drawLine(
            QPointF(card.left() + 18, footer_top), QPointF(card.right() - 18, footer_top)
        )

        if self._loading:
            text = "обновляю…"
        elif self._last_update:
            age = time.time() - self._last_update
            stamp = (
                "только что"
                if age < 70
                else time.strftime("%H:%M", time.localtime(self._last_update))
            )
            text = f"обновлено {stamp}"
        else:
            text = "ожидаю первую проверку"

        painter.setFont(_font(10))
        painter.setPen(QColor(110, 118, 135, 220))
        painter.drawText(QPointF(card.left() + 18, footer_top + 19), text)
        painter.setPen(QColor(110, 118, 135, 130))
        painter.drawText(
            QRectF(card.right() - 100, footer_top + 8, 82, 18),
            Qt.AlignRight | Qt.AlignVCenter,
            f"v{__version__}",
        )

    # --- мышь ------------------------------------------------------------
    def _buttons_visible(self) -> bool:
        return self._hover > 0.2 or self._loading

    def _row_at(self, pos: QPoint) -> str | None:
        point = QPointF(pos)
        for server, row in self._row_layout():
            if row.contains(point):
                return server.address
        return None

    def _update_cursor(self, pos: QPoint) -> None:
        point = QPointF(pos)
        if self._dragging:
            self.setCursor(Qt.ClosedHandCursor)
        elif (
            self._refresh_rect.contains(point)
            or self._close_rect.contains(point)
            or self._row_at(pos)
        ):
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def _update_hover(self, pos: QPoint) -> None:
        row = self._row_at(pos) if self._card_rect().contains(QPointF(pos)) else None
        if row != self._hovered_row:
            self._hovered_row = row
            self.update()

    def enterEvent(self, event) -> None:  # noqa: ANN001
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        self._hovered_button = None
        self._hovered_row = None
        self.setCursor(Qt.ClosedHandCursor if self._dragging else Qt.ArrowCursor)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._dragging = False
            pos = event.position()
            if self._buttons_visible():
                if self._close_rect.contains(pos):
                    self._pressed_button = "close"
                    return
                if self._refresh_rect.contains(pos):
                    self._pressed_button = "refresh"
                    return
            self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            current = event.globalPosition().toPoint()
            if not self._dragging and self._press_pos is not None:
                if (current - self._press_pos).manhattanLength() > 4:
                    self._dragging = True
                    self._hovered_row = None
                    self.setCursor(Qt.ClosedHandCursor)
            if self._dragging:
                self.move(current - self._drag_offset)
                return
        pos = event.position().toPoint()
        self._update_hover(pos)
        self._update_cursor(pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        pos = event.position()
        if self._pressed_button:
            action = self._pressed_button
            self._pressed_button = None
            rect = self._close_rect if action == "close" else self._refresh_rect
            self._drag_offset = None
            self._press_pos = None
            if rect.contains(pos):
                if action == "close":
                    self.app.hide_widget()
                else:
                    self.app.request_refresh()
            self._update_cursor(pos.toPoint())
            super().mouseReleaseEvent(event)
            return

        if self._drag_offset is not None:
            was_dragging = self._dragging
            self._drag_offset = None
            self._press_pos = None
            self._dragging = False
            self._update_cursor(pos.toPoint())
            if was_dragging:
                self.app.remember_position()
            else:
                address = self._row_at(pos.toPoint())
                if address:
                    self.toggle_players(address)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: ANN001
        if event.modifiers() & Qt.ControlModifier:
            step = 0.05 if event.angleDelta().y() > 0 else -0.05
            self.set_opacity(round(self.state.opacity + step, 2))
            event.accept()
            return
        if (
            self.expanded
            and self._player_area.contains(QPointF(event.position()))
            and event.angleDelta().y()
        ):
            current = self._player_scroll.get(self.expanded, 0.0)
            self._player_scroll[self.expanded] = max(
                0.0, current - event.angleDelta().y() / 120.0 * 36.0
            )
            self.update()
            event.accept()
            return
        super().wheelEvent(event)

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet(MENU_QSS)

        refresh = menu.addAction("Обновить сейчас")
        refresh.triggered.connect(self.app.request_refresh)
        reload_action = menu.addAction("Перечитать настройки")
        reload_action.triggered.connect(self.app.reload_config)
        open_action = menu.addAction("Открыть servers.json")
        open_action.triggered.connect(self.app.open_config)

        if self.app.config.servers:
            players_menu = menu.addMenu("Игроки")
            for server in self.app.config.servers:
                action = players_menu.addAction(server.name)
                action.setCheckable(True)
                action.setChecked(self.expanded == server.address)
                action.triggered.connect(
                    lambda _checked=False, address=server.address: self.toggle_players(address)
                )
        menu.addSeparator()

        top_action = menu.addAction("Всегда сверху")
        top_action.setCheckable(True)
        top_action.setChecked(self.state.always_on_top)
        top_action.toggled.connect(self.set_always_on_top)

        opacity_menu = menu.addMenu("Прозрачность")
        for value in (0.6, 0.75, 0.85, 0.93, 1.0):
            action = opacity_menu.addAction(f"{int(value * 100)}%")
            action.triggered.connect(lambda _checked=False, v=value: self.set_opacity(v))

        menu.addSeparator()
        quit_action = menu.addAction("Выход")
        quit_action.triggered.connect(self.app.quit)
        return menu

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001
        self._build_menu().exec(event.globalPos())

    def set_always_on_top(self, enabled: bool) -> None:
        self.state.always_on_top = bool(enabled)
        position = self.pos()
        flags = self.windowFlags()
        if enabled:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.move(position)
        self.setWindowOpacity(self.state.opacity)
        self.show()
        self.app.remember_state()

    def set_opacity(self, value: float) -> None:
        self.state.opacity = min(1.0, max(0.5, float(value)))
        self.setWindowOpacity(self.state.opacity)
        self.app.remember_state()

    def closeEvent(self, event) -> None:  # noqa: ANN001
        event.ignore()
        self.app.hide_widget()


class Toast(QWidget):
    """Мягкое всплывающее уведомление рядом с виджетом."""

    WIDTH = 320
    HEIGHT = 88

    def __init__(self, text: str) -> None:
        super().__init__(None)
        self._text = text
        self._hiding = False
        flags = (
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput
            | Qt.NoDropShadowWindowHint
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.resize(self.WIDTH, self.HEIGHT)
        self.setWindowOpacity(0.0)
        self._anim: QPropertyAnimation | None = None

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        card = QRectF(10, 8, self.WIDTH - 20, 56)
        path = QPainterPath()
        path.addRoundedRect(card, 20, 20)

        shadow = QRadialGradient(card.center(), card.width() * 0.62)
        shadow.setColorAt(0.0, QColor(0, 0, 0, 110))
        shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(shadow)
        painter.drawEllipse(card.adjusted(-8, 4, 8, 16))

        gradient = QLinearGradient(card.topLeft(), card.bottomLeft())
        gradient.setColorAt(0.0, QColor(33, 36, 47, 250))
        gradient.setColorAt(1.0, QColor(22, 24, 32, 250))
        painter.fillPath(path, gradient)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 22), 1))
        painter.drawPath(path)

        painter.setPen(Qt.NoPen)
        painter.setBrush(MINT)
        painter.drawEllipse(QPointF(card.left() + 20, card.center().y()), 4.2, 4.2)

        font = _font(12)
        painter.setFont(font)
        painter.setPen(QColor(232, 238, 247))
        text = QFontMetricsF(font).elidedText(
            self._text, Qt.ElideRight, int(card.width() - 52)
        )
        painter.drawText(
            QRectF(card.left() + 34, card.top(), card.width() - 46, card.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            text,
        )
        painter.end()

    def pop(self, anchor: QWidget) -> None:
        screen = QApplication.screenAt(anchor.geometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = anchor.x() + (anchor.width() - self.width()) // 2
        y = anchor.y() - self.height() + 10
        if y < area.top() + 4:
            y = anchor.y() + anchor.height() - 10
        x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
        self.move(x, y)
        self.show()
        self.raise_()
        self._fade(0.0, 0.98, 200)
        QTimer.singleShot(4200, self.dismiss)

    def _fade(self, start: float, end: float, duration: int) -> None:
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(duration)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim = anim
        anim.start()

    def dismiss(self) -> None:
        if self._hiding:
            return
        self._hiding = True
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(240)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.finished.connect(self.close)
        self._anim = anim
        anim.start()


class WidgetApp:
    """Собирает виджет, опрос серверов и уведомления."""

    def __init__(self, config_path: Path, config: AppConfig | None = None) -> None:
        from .main_window import MainWindow, TrayController
        from PySide6.QtGui import QIcon

        self.config_path = config_path
        self.state_path = config_path.parent / "window.json"
        self.config = config or load_config(config_path)
        self.window_state = load_state(self.state_path)
        self.qapp = QApplication.instance() or QApplication([])
        self.qapp.setApplicationName("MC Monitor")
        self.qapp.setWindowIcon(QIcon(str(resource_path("assets/icon.ico"))))
        self.qapp.setQuitOnLastWindowClosed(False)
        self.statuses: dict[str, ServerStatus] = {}
        self.widget = StatusWidget(self)
        self.bridge = _Bridge()
        self.bridge.done.connect(self._on_status)
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.pending = 0
        self._refresh_again = False
        self.toast: Toast | None = None
        self.timer = QTimer(self.widget)
        self.timer.timeout.connect(self.refresh)
        self._quitting = False
        self.main_window = MainWindow(self)
        self.tray = TrayController(self)
        self.hotkey = None
        self._startup_hotkey_error = ""
        try:
            self.set_hotkey(self.config.hotkey_show_widget)
        except (OSError, RuntimeError, ValueError) as exc:
            self._startup_hotkey_error = str(exc)
            log.warning("Не удалось зарегистрировать горячую клавишу: %s", exc)

    # --- запуск ----------------------------------------------------------
    def run(self) -> int:
        self.qapp.aboutToQuit.connect(self.shutdown)
        self.tray.show()
        self.main_window.show()
        if self.window_state.widget_visible:
            self.show_widget()
        else:
            self.main_window.set_widget_visibility(False)
            self.tray.set_widget_visibility(False)
        self.refresh()
        QTimer.singleShot(
            5000,
            lambda: check_for_updates(self.main_window, silent=True),
        )
        self.timer.start(self.config.poll_interval * 1000)
        return self.qapp.exec()

    # --- опрос -----------------------------------------------------------
    def refresh(self) -> None:
        if self.pending:
            self._refresh_again = True
            return
        self._refresh_again = False
        servers = self.config.servers
        self.pending = len(servers)
        self.widget.mark_loading(bool(servers))
        self.main_window.refresh_statuses()
        for server in servers:
            future = self.pool.submit(api.fetch_status, server)
            future.add_done_callback(lambda f, s=server: self._deliver(s, f))
        if not servers:
            self.widget.mark_updated()

    def _deliver(self, server: ServerConfig, future) -> None:  # noqa: ANN001
        try:
            status = future.result()
        except Exception as exc:
            status = ServerStatus(online=False, error=str(exc))
        self.bridge.done.emit(server, status)

    def _on_status(self, server: ServerConfig, status: ServerStatus) -> None:
        if any(current.address == server.address for current in self.config.servers):
            old = self.statuses.get(server.address)
            notice = change_notice(
                server.name,
                old,
                status,
                self.config,
                server.priority_players,
            )
            self.statuses[server.address] = status
            if notice:
                if not self.tray.show_message(notice.title, notice.message):
                    if self.widget.isVisible():
                        self.show_toast(notice.message)
            self.widget.status_changed(server.address)
            self.main_window.refresh_statuses()
        self.pending = max(0, self.pending - 1)
        if not self.pending:
            self.widget.mark_loading(False)
            self.widget.mark_updated()
            if self._refresh_again:
                self._refresh_again = False
                QTimer.singleShot(0, self.refresh)

    # --- действия --------------------------------------------------------
    def request_refresh(self) -> None:
        self.refresh()

    def reload_config(self) -> None:
        try:
            config = load_config(self.config_path)
        except ConfigError as exc:
            self.show_toast(f"Ошибка в настройках: {exc}")
            return
        self.config = config
        hotkey_error: Exception | None = None
        try:
            self.set_hotkey(config.hotkey_show_widget)
            self._startup_hotkey_error = ""
        except (OSError, RuntimeError, ValueError) as exc:
            log.warning("Не удалось применить горячую клавишу: %s", exc)
            hotkey_error = exc
        self.timer.setInterval(config.poll_interval * 1000)
        addresses = {server.address for server in config.servers}
        for address in tuple(self.statuses):
            if address not in addresses:
                del self.statuses[address]
        self.widget.display = {
            address: value
            for address, value in self.widget.display.items()
            if address in addresses
        }
        if self.widget.expanded and self.widget.expanded not in addresses:
            self.widget.expanded = None
            self.widget._extra = 0.0
            self.widget._player_scroll.clear()
        self.widget._hovered_row = None
        self.widget.resize_to_content()
        self.widget.update()
        self.main_window.refresh_servers()
        if hotkey_error is None:
            self.show_toast("Настройки перечитаны ✅")
        self.refresh()

    def show_widget(self, address: str | None = None) -> None:
        if not self.widget.isVisible():
            self.widget._closing = False
            self.widget.show()
            self.widget.animate_in()
        self._set_widget_visibility(True)
        self.widget.raise_()
        if address and any(server.address == address for server in self.config.servers):
            if self.widget.expanded != address:
                self.widget.toggle_players(address)

    def toggle_widget(self) -> None:
        if self.widget.isVisible():
            self.hide_widget()
        else:
            self.show_widget()

    def set_hotkey(self, sequence: str) -> None:
        from .hotkeys import GlobalHotkey

        if self.hotkey is None:
            self.hotkey = GlobalHotkey(self.qapp, sequence, self.toggle_widget)
        else:
            self.hotkey.set_sequence(sequence)

    def hide_widget(self) -> None:
        self.widget.hide()
        self._set_widget_visibility(False)

    def _set_widget_visibility(self, visible: bool) -> None:
        changed = self.window_state.widget_visible != visible
        self.window_state.widget_visible = visible
        if changed:
            self.remember_state()
        self.main_window.set_widget_visibility(visible)
        self.tray.set_widget_visibility(visible)

    def open_config(self) -> None:
        try:
            if not self.config_path.exists():
                self.config_path.write_text("{}\n", encoding="utf-8")
            os.startfile(self.config_path)  # type: ignore[attr-defined]
        except OSError:
            log.warning("не удалось открыть %s", self.config_path, exc_info=True)

    def remember_position(self) -> None:
        self.window_state.x = self.widget.x()
        self.window_state.y = self.widget.y()
        save_state(self.state_path, self.window_state)

    def remember_state(self) -> None:
        save_state(self.state_path, self.window_state)

    def show_toast(self, message: str) -> None:
        if self.toast is not None:
            self.toast.close()
        self.toast = Toast(message)
        self.toast.pop(self.widget)

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.tray.hide()
        if self.widget.isVisible():
            self.widget.animate_out(self.qapp.quit)
        else:
            self.qapp.quit()

    def shutdown(self) -> None:
        self.remember_state()
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        if self.hotkey is not None:
            try:
                self.hotkey.unregister()
            except OSError:
                log.exception("Не удалось освободить горячую клавишу")
        self.tray.hide()
        self.main_window.hide()
        if self.toast is not None:
            self.toast.close()

    # --- превью ----------------------------------------------------------
    def preview(self, path: Path) -> None:
        counts = [12, 37, 0]
        demo_names = [
            "Player",
            "xhrony",
            "Steve",
            "Alex",
            "Nova",
            "minty_cloud",
            "Herobrine",
            "Sova",
        ]
        for index, server in enumerate(self.config.servers):
            online_count = counts[index % len(counts)]
            self.statuses[server.address] = ServerStatus(
                online=True,
                online_count=online_count,
                max_players=500 if index == 0 else 120,
                players=demo_names if index == 0 else [],
                version="Java 1.16.4-1.21.11" if index == 0 else "Paper 1.21.4",
            )
            self.widget.display[server.address] = float(online_count)
        self.widget._last_update = time.time()
        self.widget._hover = 0.35
        if self.config.servers:
            first = self.config.servers[0]
            self.widget.expanded = first.address
            self.widget._extra = self.widget._list_height(first.address)
            self.widget.resize_to_content()
        self.widget.ensurePolished()
        size = QSize(self.widget.width() * 2, self.widget.height() * 2)
        image = QImage(size, QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(2.0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self.widget.render(painter, QPoint(0, 0))
        painter.end()
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(path))
