"""Основное окно управления серверами и системный трей."""
from __future__ import annotations

import base64
import logging
import math
import threading
from dataclasses import replace

from PySide6.QtCore import (
    QEasingCurve,
    QBuffer,
    QIODevice,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QImage,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QToolButton,
)

from .config import ConfigError, ServerConfig, resource_path, save_config
from . import cloud_sync
from .auto_update import check_for_updates
from .models import ServerStatus
from .settings import SettingsDialog

log = logging.getLogger(__name__)


def _window_stylesheet(accent_hex: str) -> str:
    accent = QColor(accent_hex)
    if not accent.isValid():
        accent = QColor("#7ee0a3")
    accent_rgb = f"{accent.red()},{accent.green()},{accent.blue()}"
    return (
        WINDOW_QSS
        .replace("#9de3b5", accent.lighter(118).name())
        .replace("#7fcbb5", accent.name())
        .replace("#b1edc4", accent.lighter(125).name())
        .replace("#75bc98", accent.name())
        .replace("rgba(125,193,159,24)", f"rgba({accent_rgb},24)")
        .replace("rgba(161,213,184,60)", f"rgba({accent_rgb},60)")
    )


WINDOW_QSS = """
QMainWindow {
    background: #10151b;
}
QLabel {
    color: #e8eee9;
    background: transparent;
    font-family: "Segoe UI";
    font-weight: 500;
}
QFrame#serverCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(31, 47, 49, 248), stop:1 rgba(20, 31, 36, 248));
    border: 1px solid rgba(153, 212, 184, 55);
    border-radius: 16px;
}
QFrame#serverCard:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(37, 58, 56, 250), stop:1 rgba(24, 39, 42, 250));
    border-color: rgba(157, 224, 185, 112);
}
QFrame#summaryCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(33, 54, 51, 238), stop:1 rgba(24, 43, 48, 238));
    border: 1px solid rgba(155, 216, 189, 56);
    border-radius: 16px;
}
QFrame#emptyCard {
    background: rgba(25, 39, 42, 220);
    border: 1px solid rgba(143, 209, 179, 60);
    border-radius: 20px;
}
QToolButton#searchButton, QToolButton#settingsButton {
    color: #d7e8df;
    background: rgba(36, 57, 58, 210);
    border: 1px solid rgba(133, 188, 177, 52);
    border-radius: 12px;
    font-size: 19px;
    font-weight: 700;
}
QToolButton#searchButton:hover, QToolButton#settingsButton:hover {
    color: #ffffff;
    background: rgba(67, 103, 87, 220);
    border-color: rgba(179, 231, 199, 110);
}
QToolButton#accountButton {
    color: #e8f4ec;
    background: rgba(36, 57, 58, 210);
    border: 1px solid rgba(133, 188, 177, 70);
    border-radius: 20px;
    font-size: 16px;
}
QToolButton#accountButton:hover {
    background: rgba(67, 103, 87, 220);
    border-color: rgba(179, 231, 199, 130);
}
QPushButton {
    color: #e8f4ec;
    background: rgba(125, 193, 159, 42);
    border: 1px solid rgba(161, 213, 184, 85);
    border-radius: 12px;
    padding: 9px 13px;
    font-size: 11px;
    font-weight: 800;
}
QPushButton:hover {
    color: #f5fff8;
    background: rgba(127, 211, 170, 76);
    border-color: rgba(179, 231, 199, 155);
}
QPushButton:pressed { background: rgba(98, 167, 132, 115); }
QPushButton#primaryButton {
    color: #10231a;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #9de3b5, stop:1 #7fcbb5);
    border: 0;
    border-radius: 15px;
    padding: 13px 16px;
    font-size: 13px;
    font-weight: 900;
}
QPushButton#primaryButton:hover {
    background: #b1edc4;
}
QPushButton#quietButton {
    color: #c9ded4;
    background: rgba(36, 57, 58, 210);
    border-color: rgba(133, 188, 177, 52);
}
QPushButton#widgetMembershipButton {
    color: #bce2cc;
    background: rgba(54, 91, 73, 72);
    border: 1px solid rgba(142, 204, 169, 72);
    border-radius: 9px;
    padding: 6px 10px;
    font-size: 10px;
    font-weight: 700;
}
QPushButton#widgetMembershipButton:hover {
    color: #effff4;
    background: rgba(75, 125, 97, 128);
    border-color: rgba(176, 230, 194, 150);
}
QPushButton#removeButton {
    color: #f0b5ac;
    background: rgba(126, 62, 59, 66);
    border-color: rgba(224, 139, 128, 85);
    padding: 6px 11px;
}
QLabel#sectionCaption {
    color: #8ca99d;
    font-size: 9px;
    font-weight: 900;
    letter-spacing: 1.3px;
}
QLabel#serverCount {
    color: #b9d1c5;
    font-size: 11px;
    font-weight: 700;
}
QLabel#onlineBadge {
    color: #b5ebc7;
    background: rgba(109, 184, 137, 35);
    border: 1px solid rgba(143, 213, 169, 65);
    border-radius: 10px;
    padding: 6px 9px;
    font-size: 10px;
    font-weight: 900;
}
QLabel#statusBadge {
    border-radius: 9px;
    padding: 4px 8px;
    font-size: 9px;
    font-weight: 900;
}
QLabel#serverIcon {
    color: #c9ded4;
    background: rgba(125,193,159,24);
    border: 1px solid rgba(161,213,184,60);
    border-radius: 9px;
    font-size: 9px;
    font-weight: 900;
}
QLabel#emptyTitle {
    color: #e4f2e9;
    font-size: 15px;
    font-weight: 900;
}
QLabel#emptyDescription {
    color: #a5bdb1;
    font-size: 11px;
    font-weight: 600;
}
QScrollArea, QScrollArea > QWidget > QWidget {
    background: transparent;
    border: 0;
}
QScrollBar:vertical {
    background: rgba(12, 19, 22, 80);
    width: 7px;
    margin: 4px 0;
    border-radius: 3px;
}
QScrollBar::handle:vertical {
    background: rgba(157, 199, 181, 100);
    min-height: 28px;
    border-radius: 3px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QDialog {
    background: #172126;
}
QLineEdit, QComboBox {
    color: #edf4ef;
    background: #10191d;
    border: 1px solid #3b5550;
    border-radius: 10px;
    padding: 10px 12px;
    font-weight: 700;
    selection-background-color: #75bc98;
}
QComboBox QAbstractItemView {
    color: #edf4ef;
    background: #10191d;
    selection-background-color: #315d4a;
}
"""


def _button(text: str, object_name: str = "") -> QPushButton:
    button = QPushButton(text)
    if object_name:
        button.setObjectName(object_name)
    button.setCursor(Qt.PointingHandCursor)
    return button


def _action_icon(kind: str) -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#c9ddd3"), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if kind == "search":
        painter.drawEllipse(QRectF(9, 8, 34, 34))
        painter.drawLine(QPointF(36, 36), QPointF(54, 54))
    elif kind == "account":
        painter.drawEllipse(QRectF(23, 8, 18, 18))
        shoulders = QPainterPath(QPointF(9, 54))
        shoulders.cubicTo(10, 39, 20, 33, 32, 33)
        shoulders.cubicTo(44, 33, 54, 39, 55, 54)
        painter.drawPath(shoulders)
    elif kind == "settings":
        painter.drawEllipse(QRectF(17, 17, 30, 30))
        painter.drawEllipse(QRectF(27, 27, 10, 10))
        for start, end in (
            ((32, 4), (32, 17)),
            ((32, 47), (32, 60)),
            ((4, 32), (17, 32)),
            ((47, 32), (60, 32)),
            ((12, 12), (21, 21)),
            ((43, 43), (52, 52)),
            ((12, 52), (21, 43)),
            ((43, 21), (52, 12)),
        ):
            painter.drawLine(QPointF(*start), QPointF(*end))
    elif kind == "refresh":
        painter.drawArc(QRectF(12, 12, 40, 40), 38 * 16, 285 * 16)
        painter.drawLine(QPointF(43, 12), QPointF(54, 12))
        painter.drawLine(QPointF(54, 12), QPointF(53, 24))
    painter.end()
    return QIcon(pixmap)


class AuroraSurface(QWidget):
    """Медленно меняющийся тёмный фон с зелёно-бирюзовым свечением."""

    def __init__(self) -> None:
        super().__init__()
        self.phase = 0.0
        self._accent = QColor("#7ee0a3")
        self._accent_animation: QVariantAnimation | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance)

    def setAccentColor(self, color: QColor) -> None:
        if not color.isValid():
            return
        if self._accent_animation is not None:
            self._accent_animation.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(420)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.setStartValue(self._accent)
        animation.setEndValue(color)
        animation.valueChanged.connect(self._set_accent)
        animation.finished.connect(lambda: setattr(self, "_accent_animation", None))
        self._accent_animation = animation
        animation.start()

    def _set_accent(self, value: object) -> None:
        self._accent = QColor(value)
        self.update()

    def _advance(self) -> None:
        self.phase = (self.phase + 0.0025) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        bounds = self.rect()

        base = QLinearGradient(
            QPointF(bounds.topLeft()), QPointF(bounds.bottomRight())
        )
        base.setColorAt(0.0, QColor("#172122"))
        base.setColorAt(0.52, QColor("#132623"))
        base.setColorAt(1.0, QColor("#181e25"))
        painter.fillRect(bounds, base)

        angle = self.phase * math.tau
        green_center = QPointF(
            bounds.width() * (0.5 + 0.36 * math.sin(angle)),
            bounds.height() * (0.25 + 0.16 * math.cos(angle)),
        )
        green = QRadialGradient(
            green_center, max(bounds.width(), bounds.height()) * 0.82
        )
        accent = QColor(self._accent)
        accent.setAlpha(76)
        soft_accent = QColor(self._accent)
        soft_accent.setAlpha(38)
        clear_accent = QColor(self._accent)
        clear_accent.setAlpha(0)
        green.setColorAt(0.0, accent)
        green.setColorAt(0.56, soft_accent)
        green.setColorAt(1.0, clear_accent)
        painter.fillRect(bounds, green)

        blue_center = QPointF(
            bounds.width() * (0.5 - 0.38 * math.sin(angle + 1.1)),
            bounds.height() * (0.72 + 0.13 * math.cos(angle + 1.1)),
        )
        blue = QRadialGradient(
            blue_center, max(bounds.width(), bounds.height()) * 0.74
        )
        neighbor = QColor(self._accent)
        hue = neighbor.hsvHue()
        neighbor.setHsv((hue + 38) % 360 if hue >= 0 else 38, neighbor.hsvSaturation(),
                        neighbor.value())
        neighbor_glow = QColor(neighbor)
        neighbor_glow.setAlpha(52)
        neighbor_clear = QColor(neighbor)
        neighbor_clear.setAlpha(0)
        blue.setColorAt(0.0, neighbor_glow)
        blue.setColorAt(1.0, neighbor_clear)
        painter.fillRect(bounds, blue)
        painter.end()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: ANN001
        self._timer.stop()
        super().hideEvent(event)


class ServerDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Добавить сервер")
        self.setModal(True)
        self.setMinimumWidth(340)
        self.server: ServerConfig | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        title = QLabel("Новый сервер")
        title.setStyleSheet("font-size: 19px; font-weight: 700;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Например, Выживание")
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("play.example.com или host:порт")
        self.protocol_input = QComboBox()
        self.protocol_input.addItem("Java Edition", "java")
        self.protocol_input.addItem("Bedrock Edition", "bedrock")
        form.addRow("Название", self.name_input)
        form.addRow("Адрес", self.address_input)
        form.addRow("Издание", self.protocol_input)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #ff9a91;")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.address_input.returnPressed.connect(self._accept)

    def _accept(self) -> None:
        raw_address = self.address_input.text().strip()
        try:
            if not raw_address:
                raise ConfigError("Укажи адрес Minecraft-сервера.")
            server = ServerConfig.from_address(
                raw_address,
                self.name_input.text().strip() or raw_address,
                protocol=self.protocol_input.currentData(),
            )
            if server.port is not None and not 1 <= server.port <= 65535:
                raise ConfigError("Порт должен быть от 1 до 65535.")
        except ConfigError as exc:
            self.error_label.setText(str(exc))
            return
        self.server = server
        self.accept()


class AccountDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Аккаунт MC Monitor")
        self.setModal(True)
        self.setMinimumWidth(340)
        self.mode: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Никнейм и пароль"))

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Никнейм (3–16 символов)")
        self.username_input.setMaxLength(16)
        layout.addWidget(self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Пароль (от 8 символов)")
        self.password_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.password_input)

        note = QLabel("Без email сбросить забытый пароль нельзя.")
        note.setStyleSheet("color: #9db1a7; font-size: 11px;")
        layout.addWidget(note)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #ff9a91;")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        register = _button("Создать аккаунт", "quietButton")
        register.clicked.connect(lambda: self._submit("signup"))
        buttons.addWidget(register)
        login = _button("Войти", "primaryButton")
        login.clicked.connect(lambda: self._submit("login"))
        buttons.addWidget(login)
        layout.addLayout(buttons)
        cancel = _button("Отмена", "quietButton")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)
        self.password_input.returnPressed.connect(lambda: self._submit("login"))

    def _submit(self, mode: str) -> None:
        username = self.username_input.text().strip()
        password = self.password_input.text()
        try:
            cloud_sync.validate_credentials(username, password)
        except cloud_sync.CloudError as exc:
            self.error_label.setText(str(exc))
            return
        self.mode = mode
        self.accept()


class _CloudBridge(QObject):
    completed = Signal(object)


class ProfileDialog(QDialog):
    saveRequested = Signal()
    avatarSelected = Signal(bytes)

    def __init__(self, username: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Мой профиль")
        self.setModal(True)
        self.setMinimumWidth(390)
        self.avatar_bytes: bytes | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        self.identity = QLabel("@" + username)
        self.identity.setStyleSheet("color: #9db1a7;")
        layout.addWidget(self.identity)

        self.avatar_preview = QLabel("MM")
        self.avatar_preview.setFixedSize(72, 72)
        self.avatar_preview.setAlignment(Qt.AlignCenter)
        self.avatar_preview.setStyleSheet(
            "background: rgba(126,224,163,32); border: 1px solid rgba(126,224,163,90); "
            "border-radius: 36px; font-size: 20px; font-weight: 900;"
        )
        layout.addWidget(self.avatar_preview, 0, Qt.AlignHCenter)
        avatar_button = _button("Выбрать JPEG-аватар", "quietButton")
        avatar_button.clicked.connect(self._choose_avatar)
        layout.addWidget(avatar_button)

        form = QFormLayout()
        form.setSpacing(10)
        self.display_name = QLineEdit()
        self.display_name.setMaxLength(32)
        self.display_name.setPlaceholderText("Как тебя показывать")
        self.bio = QTextEdit()
        self.bio.setPlaceholderText("Коротко о себе")
        self.bio.setMaximumHeight(90)
        self.share_servers = QCheckBox("Публично показывать адреса моих серверов")
        form.addRow("Имя", self.display_name)
        form.addRow("О себе", self.bio)
        form.addRow("", self.share_servers)
        layout.addLayout(form)
        privacy_note = QLabel(
            "Выключено по умолчанию. При включении адреса будут видны "
            "авторизованным пользователям в поиске."
        )
        privacy_note.setWordWrap(True)
        privacy_note.setStyleSheet("color: #9db1a7; font-size: 11px;")
        layout.addWidget(privacy_note)

        self.status = QLabel("Загружаю профиль…")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #9db1a7;")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        save = _button("Сохранить", "primaryButton")
        save.clicked.connect(self.saveRequested.emit)
        buttons.addWidget(save)
        close = _button("Закрыть", "quietButton")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def set_profile(
        self, profile: cloud_sync.PublicProfile, avatar_bytes: bytes | None = None
    ) -> None:
        self.identity.setText("@" + profile.username)
        self.display_name.setText(profile.display_name)
        self.bio.setPlainText(profile.bio)
        self.share_servers.setChecked(profile.share_servers)
        if avatar_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(avatar_bytes, "JPEG"):
                self.avatar_preview.setPixmap(
                    pixmap.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
        else:
            self.avatar_preview.setPixmap(QPixmap())
            self.avatar_preview.setText("MM")
        self.status.setText(
            "Аватар установлен." if profile.avatar_path else "Аватар не установлен."
        )

    def _choose_avatar(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Выбрать аватар", "", "Изображения (*.png *.jpg *.jpeg *.webp)"
        )
        if not filename:
            return
        image = QImage(filename)
        if image.isNull():
            self.status.setText("Не удалось открыть выбранное изображение.")
            return
        side = min(image.width(), image.height())
        image = image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side)
        image = image.scaled(512, 512, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not image.save(buffer, "JPEG", 86):
            buffer.close()
            self.status.setText("Не удалось преобразовать изображение в JPEG.")
            return
        data = bytes(buffer.data())
        buffer.close()
        if len(data) > 2 * 1024 * 1024:
            self.status.setText("После сжатия аватар превышает 2 МБ.")
            return
        self.avatar_bytes = data
        self.avatar_preview.setPixmap(
            QPixmap.fromImage(image).scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.status.setText("Аватар готов к загрузке.")


class CommunityDialog(QDialog):
    lookupRequested = Signal(int, str)
    followRequested = Signal(str, bool)
    serversRequested = Signal(object)
    addServerRequested = Signal(object)
    avatarLoaded = Signal(object, object)

    def __init__(
        self,
        followed_only: bool = False,
        parent: QWidget | None = None,
        initial_mode: int = 0,
        initial_query: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сообщество MC Monitor")
        self.setModal(True)
        self.setMinimumSize(500, 450)
        self._closed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)
        self.avatarLoaded.connect(self._set_avatar)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Ник, имя или адрес сервера (от 2 символов)")
        self.search.textChanged.connect(self._search_changed)
        layout.addWidget(self.search)

        self.tabs = QTabWidget()
        self.tabs.addTab(QWidget(), "Игроки")
        self.tabs.addTab(QWidget(), "Серверы")
        self.tabs.addTab(QWidget(), "Подписки")
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs)

        self.results = QWidget()
        self.results_layout = QVBoxLayout(self.results)
        self.results_layout.setContentsMargins(2, 2, 2, 2)
        self.results_layout.setSpacing(8)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.results)
        layout.addWidget(self.scroll, 1)

        close = _button("Закрыть", "quietButton")
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self.query = ""
        if followed_only:
            self.tabs.setCurrentIndex(2)
        elif initial_query:
            self.tabs.setCurrentIndex(initial_mode)
            self.search.setText(initial_query)
        else:
            self.set_status("Введи не менее двух символов для поиска.")

    @property
    def mode(self) -> int:
        return self.tabs.currentIndex()

    def _search_changed(self, query: str) -> None:
        self.query = query.strip()
        if self.mode != 2:
            self.lookupRequested.emit(self.mode, self.query)

    def _tab_changed(self, mode: int) -> None:
        self.search.setVisible(mode != 2)
        if mode == 2:
            self.lookupRequested.emit(mode, "")
        elif self.search.text().strip():
            self.lookupRequested.emit(mode, self.search.text().strip())
        else:
            self.set_status("Введи не менее двух символов для поиска.")

    def set_status(self, text: str) -> None:
        self._clear()
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color: #9db1a7; padding: 8px;")
        self.results_layout.addWidget(label)
        self.results_layout.addStretch(1)

    def set_profiles(
        self, profiles: list[cloud_sync.PublicProfile], followed: bool = False
    ) -> None:
        self._clear()
        if not profiles:
            self.set_status("Ты пока ни на кого не подписан." if followed else "Ничего не найдено.")
            return
        for profile in profiles:
            row = QFrame()
            row.setObjectName("serverCard")
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            identity = QHBoxLayout()
            avatar = QLabel(profile.username[:1].upper())
            avatar.setFixedSize(38, 38)
            avatar.setAlignment(Qt.AlignCenter)
            avatar.setStyleSheet(
                "background: rgba(126,224,163,32); border: 1px solid rgba(126,224,163,90); "
                "border-radius: 19px; font-size: 15px; font-weight: 900;"
            )
            avatar.setProperty("profileId", profile.user_id)
            identity.addWidget(avatar)
            title = QLabel(
                profile.display_name + "  @" + profile.username
                if profile.display_name
                else "@" + profile.username
            )
            title.setStyleSheet("font-size: 14px; font-weight: 800;")
            identity.addWidget(title, 1)
            row_layout.addLayout(identity)
            if profile.avatar_path:
                threading.Thread(
                    target=self._download_avatar,
                    args=(avatar, profile),
                    daemon=True,
                ).start()
            if profile.bio:
                description = QLabel(profile.bio)
                description.setWordWrap(True)
                description.setStyleSheet("color: #9db1a7;")
                row_layout.addWidget(description)
            actions = QHBoxLayout()
            follow = _button(
                "Отписаться" if profile.is_following else "Подписаться", "quietButton"
            )
            follow.clicked.connect(
                lambda _checked=False, p=profile: self.followRequested.emit(
                    p.user_id, p.is_following
                )
            )
            actions.addWidget(follow)
            if profile.share_servers:
                servers = _button("Серверы", "quietButton")
                servers.clicked.connect(
                    lambda _checked=False, p=profile: self.serversRequested.emit(p)
                )
                actions.addWidget(servers)
            actions.addStretch(1)
            row_layout.addLayout(actions)
            self.results_layout.addWidget(row)
        self.results_layout.addStretch(1)

    def _download_avatar(
        self, label: QLabel, profile: cloud_sync.PublicProfile
    ) -> None:
        try:
            data = cloud_sync.download_avatar(profile)
            if data:
                image = QImage.fromData(data, "JPEG")
                if image.isNull():
                    raise cloud_sync.CloudError("Файл аватара повреждён.")
                if not self._closed:
                    try:
                        self.avatarLoaded.emit(label, image)
                    except RuntimeError:
                        log.debug("Окно сообщества уже закрыто")
        except cloud_sync.CloudError as exc:
            log.warning("Не удалось загрузить аватар @%s: %s", profile.username, exc)

    def _set_avatar(self, label: QLabel, image: QImage) -> None:
        try:
            label.setPixmap(
                QPixmap.fromImage(image).scaled(
                    38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
            label.setText("")
        except RuntimeError:
            log.debug("Результат загрузки аватара устарел")

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self._closed = True
        super().closeEvent(event)

    def set_servers(self, servers: list[cloud_sync.PublicServer]) -> None:
        self._clear()
        if not servers:
            self.set_status("Публичных серверов не найдено.")
            return
        for server in servers:
            row = QFrame()
            row.setObjectName("serverCard")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 9, 12, 9)
            details = QLabel(
                f"{server.name}\n{server.address} · {server.protocol.upper()} · @{server.username}"
            )
            details.setWordWrap(True)
            row_layout.addWidget(details, 1)
            add = _button("Добавить", "quietButton")
            add.clicked.connect(
                lambda _checked=False, item=server: self.addServerRequested.emit(item)
            )
            row_layout.addWidget(add)
            self.results_layout.addWidget(row)
        self.results_layout.addStretch(1)

    def _clear(self) -> None:
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


class ServerCard(QFrame):
    widgetMembershipRequested = Signal(str, bool)
    removeRequested = Signal(str)
    menuRequested = Signal(str, object)  # address, QPoint

    def __init__(self, server: ServerConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.server = server
        self.setObjectName("serverCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        root = QVBoxLayout(self)
        root.setContentsMargins(15, 13, 14, 12)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(9)
        self.server_icon = QLabel()
        self.server_icon.setObjectName("serverIcon")
        self.server_icon.setFixedSize(34, 34)
        self.server_icon.setAlignment(Qt.AlignCenter)
        self.server_icon.setText("MC")
        top.addWidget(self.server_icon)

        self.dot = QLabel("●")
        self.dot.setFixedWidth(13)
        self.dot.setStyleSheet("color: #d8bd83; font-size: 13px;")
        top.addWidget(self.dot)

        self.name = QLabel(server.name)
        self.name.setStyleSheet("font-size: 16px; font-weight: 900;")
        self.name.setMinimumWidth(0)
        top.addWidget(self.name, 1)

        self.protocol_badge = QLabel("JAVA" if server.protocol != "bedrock" else "BEDROCK")
        self.protocol_badge.setStyleSheet(
            "color: #9de064; background: rgba(157, 224, 100, 18); "
            "border: 1px solid rgba(157, 224, 100, 42); border-radius: 6px; "
            "padding: 2px 8px; font-size: 9px; font-weight: 900;"
        )
        if server.protocol == "bedrock":
            self.protocol_badge.setStyleSheet(
                "color: #ffc978; background: rgba(255, 201, 120, 18); "
                "border: 1px solid rgba(255, 201, 120, 42); border-radius: 6px; "
                "padding: 2px 8px; font-size: 9px; font-weight: 900;"
            )
        top.addWidget(self.protocol_badge)

        self.count = QLabel("—")
        self.count.setStyleSheet(
            "color: #c6edd3; font-size: 21px; font-weight: 900;"
        )
        top.addWidget(self.count)
        root.addLayout(top)

        address_row = QHBoxLayout()
        address_row.setSpacing(8)
        self.address = QLabel(server.address)
        self.address.setStyleSheet(
            "color: #9ab5aa; font-size: 11px; font-weight: 700;"
        )
        self.address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        address_row.addWidget(self.address, 1)
        self.status_badge = QLabel("ОЖИДАНИЕ")
        self.status_badge.setObjectName("statusBadge")
        self._set_status_badge("#e5c88c", "rgba(186, 146, 72, 36)")
        address_row.addWidget(self.status_badge)
        root.addLayout(address_row)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: rgba(159, 196, 177, 42);")
        root.addWidget(divider)

        self.detail = QLabel("Ожидает проверки")
        self.detail.setStyleSheet(
            "color: #c0d0c7; font-size: 12px; font-weight: 700;"
        )
        self.detail.setWordWrap(True)
        root.addWidget(self.detail)

        actions = QHBoxLayout()
        actions.addStretch(1)
        widget_button = _button(
            "Убрать из виджета" if server.show_in_widget else "Добавить в виджет",
            "widgetMembershipButton",
        )
        widget_button.clicked.connect(
            lambda _checked=False, address=server.address, included=server.show_in_widget:
                self.widgetMembershipRequested.emit(address, not included)
        )
        actions.addWidget(widget_button)
        remove_button = _button("Удалить", "removeButton")
        remove_button.clicked.connect(
            lambda _checked=False, address=server.address: self.removeRequested.emit(address)
        )
        actions.addWidget(remove_button)

        menu_button = QToolButton()
        menu_button.setText("⋮")
        menu_button.setCursor(Qt.PointingHandCursor)
        menu_button.setStyleSheet(
            "QToolButton { color: #8ca99d; font-size: 20px; background: transparent; border: none; padding: 4px; }"
            "QToolButton:hover { color: #ffffff; background: rgba(255,255,255,10); border-radius: 8px; }"
        )
        menu_button.clicked.connect(lambda: self.menuRequested.emit(server.address, menu_button.mapToGlobal(menu_button.rect().bottomLeft())))
        actions.addWidget(menu_button)
        root.addLayout(actions)

    def _set_status_badge(self, color: str, background: str) -> None:
        self.status_badge.setStyleSheet(
            f"color: {color}; background: {background}; "
            "border: 1px solid rgba(255, 255, 255, 20); "
            "border-radius: 9px; padding: 4px 8px; "
            "font-size: 9px; font-weight: 900;"
        )

    def update_status(self, status: ServerStatus | None) -> None:
        if status is None:
            self._set_server_icon("")
            self.dot.setStyleSheet("color: #d8bd83; font-size: 13px;")
            self.count.setText("—")
            self.status_badge.setText("ПРОВЕРКА")
            self._set_status_badge("#e5c88c", "rgba(186, 146, 72, 36)")
            self.detail.setText("Проверяю сервер…")
            return
        if not status.online:
            self._set_server_icon(status.favicon)
            self.dot.setStyleSheet("color: #ee8f89; font-size: 13px;")
            self.count.setText("—")
            self.status_badge.setText("ОФФЛАЙН")
            self._set_status_badge("#f0aaa3", "rgba(186, 80, 78, 40)")
            self.detail.setText(status.error or "Сервер недоступен")
            return
        self.dot.setStyleSheet("color: #8ed8ac; font-size: 13px;")
        self._set_server_icon(status.favicon)
        self.status_badge.setText("ОНЛАЙН")
        self._set_status_badge("#a8e8bd", "rgba(85, 172, 117, 40)")
        maximum = f" / {status.max_players}" if status.max_players else ""
        self.count.setText(f"{status.online_count}{maximum}")
        details: list[str] = []
        if status.first_motd_line:
            details.append(status.first_motd_line)
        if status.version:
            details.append(status.version)
        if status.unique_players:
            names = ", ".join(status.unique_players[:5])
            if len(status.unique_players) > 5:
                names += "…"
            details.append(names)
        elif status.hides_player_list:
            details.append("Список игроков скрыт сервером")
        elif status.online_count == 0:
            details.append("Сейчас никого нет")
        self.detail.setText(" · ".join(details) if details else "Сервер онлайн")

    def _set_server_icon(self, encoded: str) -> None:
        pixmap = QPixmap()
        try:
            if encoded and pixmap.loadFromData(base64.b64decode(encoded, validate=True)):
                self.server_icon.setPixmap(
                    pixmap.scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                self.server_icon.setText("")
                return
        except (ValueError, TypeError):
            pass
        self.server_icon.setPixmap(QPixmap())
        self.server_icon.setText("MC")


class MainWindow(QMainWindow):
    def __init__(self, app_controller) -> None:  # noqa: ANN001
        super().__init__()
        self.controller = app_controller
        self._cards: dict[str, ServerCard] = {}
        self._settings_dialog: SettingsDialog | None = None
        self._profile_dialog: ProfileDialog | None = None
        self._community_dialog: CommunityDialog | None = None
        self._community_request_id = 0
        self.setWindowTitle("MC Monitor")
        self.setWindowIcon(QIcon(str(resource_path("assets/icon.ico"))))
        self.setMinimumSize(410, 580)
        self.resize(460, 620)
        self.setStyleSheet(_window_stylesheet(self.controller.config.accent_color))

        surface = AuroraSurface()
        surface.setAccentColor(QColor(self.controller.config.accent_color))
        self._aurora_surface = surface
        self.setCentralWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(13)

        # Keep the brand on its own row, like the Android screen.
        self.logo = QLabel()
        self.logo.setAlignment(Qt.AlignCenter)
        pixmap = QIcon(str(resource_path("assets/mcm-logo.png"))).pixmap(280, 54)
        if not pixmap.isNull():
            self.logo.setPixmap(
                pixmap.scaled(
                    280, 54, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
        else:
            self.logo.setText("MC MONITOR")
            self.logo.setStyleSheet("font-size: 24px; font-weight: 900; color: #9de064;")
        self.logo.setFixedHeight(50)
        layout.addWidget(self.logo)

        caption = QLabel("JAVA + BEDROCK  ·  МОНИТОРИНГ СЕРВЕРОВ")
        caption.setAlignment(Qt.AlignCenter)
        caption.setObjectName("sectionCaption")
        layout.addWidget(caption)

        # Search, account and settings live together on a dedicated action row.
        header = QHBoxLayout()
        header.setSpacing(8)

        self.search_button = QToolButton()
        self.search_button.setObjectName("searchButton")
        self.search_button.setIcon(_action_icon("search"))
        self.search_button.setIconSize(QSize(21, 21))
        self.search_button.setFixedSize(40, 40)
        self.search_button.setCursor(Qt.PointingHandCursor)
        self.search_button.setToolTip("Поиск серверов")
        self.search_button.setAccessibleName("Поиск серверов")
        self.search_button.clicked.connect(self._toggle_search)
        header.addWidget(self.search_button)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск серверов…")
        self.search_input.setMinimumWidth(120)
        self.search_input.setMaximumWidth(280)
        self.search_input.setPlaceholderText(
            "Мои серверы · Enter — общий поиск"
        )
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setVisible(False)
        self.search_input.textChanged.connect(self._on_search_changed)
        self.search_input.returnPressed.connect(self._search_community)
        header.addWidget(self.search_input, 1)
        self.header_spacer = QWidget()
        self.header_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        header.addWidget(self.header_spacer, 1)

        self.account_button = QToolButton()
        self.account_button.setObjectName("accountButton")
        self.account_button.setFixedSize(40, 40)
        self.account_button.setCursor(Qt.PointingHandCursor)
        self.account_button.setIcon(_action_icon("account"))
        self.account_button.setIconSize(QSize(21, 21))
        self.account_button.setToolTip("Аккаунт")
        self.account_button.setAccessibleName("Аккаунт")
        self.account_button.clicked.connect(self._account_clicked)
        header.addWidget(self.account_button)

        self.settings_button = QToolButton()
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setIcon(_action_icon("settings"))
        self.settings_button.setIconSize(QSize(21, 21))
        self.settings_button.setFixedSize(40, 40)
        self.settings_button.setToolTip("Настройки")
        self.settings_button.setAccessibleName("Настройки")
        self.settings_button.setCursor(Qt.PointingHandCursor)
        self.settings_button.clicked.connect(self._open_settings)
        header.addWidget(self.settings_button)

        layout.addLayout(header)

        summary = QFrame()
        summary.setObjectName("summaryCard")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(14, 10, 12, 10)
        summary_layout.setSpacing(9)
        summary_text = QVBoxLayout()
        summary_text.setSpacing(2)
        summary_title = QLabel("МОИ СЕРВЕРЫ")
        summary_title.setStyleSheet(
            "color: #e2efe7; font-size: 12px; font-weight: 900; letter-spacing: 0.6px;"
        )
        summary_text.addWidget(summary_title)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("serverCount")
        summary_text.addWidget(self.summary_label)
        summary_layout.addLayout(summary_text, 1)
        self.online_badge = QLabel("● 0 В СЕТИ")
        self.online_badge.setObjectName("onlineBadge")
        self.online_badge.setAlignment(Qt.AlignCenter)
        summary_layout.addWidget(self.online_badge)
        refresh_button = QToolButton()
        refresh_button.setObjectName("settingsButton")
        refresh_button.setIcon(_action_icon("refresh"))
        refresh_button.setIconSize(QSize(19, 19))
        refresh_button.setFixedSize(38, 36)
        refresh_button.setCursor(Qt.PointingHandCursor)
        refresh_button.setToolTip("Обновить все серверы")
        refresh_button.setAccessibleName("Обновить все серверы")
        refresh_button.clicked.connect(self.controller.request_refresh)
        summary_layout.addWidget(refresh_button)
        layout.addWidget(summary)

        self.empty_label = QFrame()
        self.empty_label.setObjectName("emptyCard")
        empty_layout = QHBoxLayout(self.empty_label)
        empty_layout.setContentsMargins(16, 13, 16, 13)
        empty_layout.setSpacing(14)
        empty_icon = QLabel()
        empty_icon.setAlignment(Qt.AlignCenter)
        empty_icon.setFixedSize(54, 54)
        empty_pixmap = self.windowIcon().pixmap(54, 54)
        if not empty_pixmap.isNull():
            empty_icon.setPixmap(
                empty_pixmap.scaled(
                    48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
        empty_layout.addWidget(empty_icon)
        empty_text = QVBoxLayout()
        empty_text.setSpacing(5)
        self.empty_title = QLabel("Пора в игру?")
        self.empty_title.setObjectName("emptyTitle")
        empty_text.addWidget(self.empty_title)
        self.empty_description = QLabel(
            "Добавь Minecraft-сервер, чтобы\nследить за онлайном и игроками."
        )
        self.empty_description.setObjectName("emptyDescription")
        self.empty_description.setWordWrap(True)
        empty_text.addWidget(self.empty_description)
        empty_layout.addLayout(empty_text, 1)
        layout.addWidget(self.empty_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_content = QWidget()
        self.list_layout = QVBoxLayout(self.list_content)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_content)
        self.scroll.setMinimumHeight(0)
        self.scroll.setMaximumHeight(360)
        layout.addWidget(self.scroll)

        add_button = _button("+   Добавить сервер", "primaryButton")
        add_button.clicked.connect(self._add_server)
        layout.addWidget(add_button)

        window_state = getattr(self.controller, "window_state", None)
        self.widget_toggle_button = _button(
            "Убрать виджет" if getattr(window_state, "widget_visible", False)
            else "Включить виджет",
            "quietButton",
        )
        self.widget_toggle_button.clicked.connect(self.controller.toggle_widget)
        layout.addWidget(self.widget_toggle_button)

        footer = QLabel("Закрытие окна свернёт MC Monitor в системный трей")
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet("color: #748a82; font-size: 10px;")
        layout.addWidget(footer)

        self._cloud_bridge = _CloudBridge(self)
        self._cloud_bridge.completed.connect(self._cloud_completed)
        self._social_bridge = _CloudBridge(self)
        self._social_bridge.completed.connect(self._social_completed)
        self._cloud_busy = False
        self._cloud_pending = False
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(5 * 60 * 1000)
        self._sync_timer.timeout.connect(
            lambda: self._start_cloud_operation("sync", silent=True)
        )
        self._sync_timer.start()
        self.refresh_servers()
        self._update_account_button()
        QTimer.singleShot(0, self._sync_existing_account)

    def _account_clicked(self) -> None:
        try:
            session = cloud_sync.load_session(self.controller.config_path)
        except cloud_sync.CloudError as exc:
            QMessageBox.warning(self, "Сессия аккаунта", str(exc))
            return
        if session is None:
            dialog = AccountDialog(self)
            if dialog.exec() == QDialog.Accepted and dialog.mode:
                self._start_cloud_operation(
                    dialog.mode,
                    dialog.username_input.text().strip(),
                    dialog.password_input.text(),
                )
            return

        menu = QMenu(self)
        menu.addAction("Мой профиль", self._open_profile)
        menu.addAction("Поиск игроков и серверов", self._open_community)
        menu.addAction("Мои подписки", lambda: self._open_community(followed_only=True))
        menu.addSeparator()
        menu.addAction("Синхронизировать сейчас", lambda: self._start_cloud_operation("sync"))
        menu.addAction("Выйти из аккаунта", self._logout)
        menu.exec(self.account_button.mapToGlobal(
            QPoint(0, self.account_button.height())
        ))

    def _open_profile(self) -> None:
        try:
            session = cloud_sync.load_session(self.controller.config_path)
        except cloud_sync.CloudError as exc:
            QMessageBox.warning(self, "Сессия аккаунта", str(exc))
            return
        if session is None:
            return
        if self._profile_dialog is not None:
            self._profile_dialog.show()
            self._profile_dialog.raise_()
            return
        dialog = ProfileDialog(session.username, self)
        self._profile_dialog = dialog
        dialog.saveRequested.connect(lambda current=dialog: self._save_profile(current))
        dialog.finished.connect(lambda _result: self._clear_profile_dialog(dialog))
        dialog.show()
        self._run_social("profile_load", dialog=dialog)

    def _clear_profile_dialog(self, dialog: ProfileDialog) -> None:
        if self._profile_dialog is dialog:
            self._profile_dialog = None
        dialog.deleteLater()

    def _save_profile(self, dialog: ProfileDialog) -> None:
        self._run_social(
            "profile_save",
            dialog=dialog,
            display_name=dialog.display_name.text(),
            bio=dialog.bio.toPlainText(),
            share_servers=dialog.share_servers.isChecked(),
            avatar=dialog.avatar_bytes,
        )
        dialog.status.setText("Сохраняю профиль…")

    def _open_community(
        self,
        followed_only: bool = False,
        initial_mode: int = 0,
        initial_query: str = "",
    ) -> None:
        try:
            if cloud_sync.load_session(self.controller.config_path) is None:
                QMessageBox.information(self, "Сообщество", "Сначала войди в аккаунт.")
                return
        except cloud_sync.CloudError as exc:
            QMessageBox.warning(self, "Сессия аккаунта", str(exc))
            return
        if self._community_dialog is not None:
            self._community_dialog.show()
            self._community_dialog.raise_()
            if followed_only:
                self._community_dialog.tabs.setCurrentIndex(2)
            elif initial_query:
                self._community_dialog.tabs.setCurrentIndex(initial_mode)
                self._community_dialog.search.setText(initial_query)
            return
        dialog = CommunityDialog(
            followed_only,
            self,
            initial_mode=initial_mode,
            initial_query=initial_query,
        )
        self._community_dialog = dialog
        dialog.followRequested.connect(self._community_follow)
        dialog.serversRequested.connect(self._community_profile_servers)
        dialog.addServerRequested.connect(self._add_public_server)
        dialog.finished.connect(lambda _result: self._clear_community_dialog(dialog))
        dialog.lookupRequested.connect(self._schedule_community_lookup)
        dialog.show()
        self._community_lookup(dialog.mode, dialog.search.text().strip())

    def _clear_community_dialog(self, dialog: CommunityDialog) -> None:
        if self._community_dialog is dialog:
            self._community_dialog = None
        dialog.deleteLater()

    def _schedule_community_lookup(self, mode: int, query: str) -> None:
        if self._community_dialog is None:
            return
        self._community_request_id += 1
        request_id = self._community_request_id
        if mode == 2:
            self._run_social(
                "community_followed",
                dialog=self._community_dialog,
                request_id=request_id,
            )
            return
        if len(query) < 2:
            self._community_dialog.set_status("Введи не менее двух символов для поиска.")
            return
        QTimer.singleShot(
            300,
            lambda: self._community_lookup(mode, query, request_id),
        )

    def _community_lookup(
        self, mode: int, query: str, request_id: int | None = None
    ) -> None:
        dialog = self._community_dialog
        if dialog is None:
            return
        if request_id is None:
            self._community_request_id += 1
            request_id = self._community_request_id
        if request_id != self._community_request_id:
            return
        if mode == 2:
            self._run_social(
                "community_followed", dialog=dialog, request_id=request_id
            )
        elif len(query) >= 2:
            action = "community_profiles" if mode == 0 else "community_servers"
            self._run_social(
                action, dialog=dialog, query=query, request_id=request_id
            )
        else:
            dialog.set_status("Введи не менее двух символов для поиска.")

    def _community_follow(self, user_id: str, is_following: bool) -> None:
        self._run_social(
            "community_unfollow" if is_following else "community_follow",
            dialog=self._community_dialog,
            user_id=user_id,
        )

    def _community_profile_servers(self, profile: cloud_sync.PublicProfile) -> None:
        self._run_social(
            "community_user_servers",
            dialog=self._community_dialog,
            profile=profile,
            request_id=self._community_request_id,
        )

    def _add_public_server(self, server: cloud_sync.PublicServer) -> None:
        try:
            config = ServerConfig.from_address(
                server.address, server.name, protocol=server.protocol
            )
        except ConfigError as exc:
            QMessageBox.warning(self, "Адрес сервера", str(exc))
            return
        if any(item.address.lower() == config.address.lower()
               for item in self.controller.config.servers):
            QMessageBox.information(self, "Сервер уже добавлен", config.address)
            return
        self._save_config([*self.controller.config.servers, config])

    def _run_social(self, action: str, dialog=None, **payload) -> None:  # noqa: ANN001
        config_path = self.controller.config_path

        def worker() -> None:
            try:
                session = cloud_sync.load_session(config_path)
                if session is None:
                    raise cloud_sync.CloudError("Сначала войди в аккаунт.")
                if action == "profile_load":
                    profile = cloud_sync.fetch_profile(session)
                    result = (profile, cloud_sync.download_avatar(profile))
                elif action == "profile_save":
                    profile = cloud_sync.update_profile(
                        session,
                        payload["display_name"],
                        payload["bio"],
                        payload["share_servers"],
                    )
                    if payload.get("avatar") is not None:
                        profile = cloud_sync.upload_avatar(session, payload["avatar"])
                        avatar_data = payload["avatar"]
                    else:
                        avatar_data = cloud_sync.download_avatar(profile)
                    result = (profile, avatar_data)
                elif action == "community_profiles":
                    result = cloud_sync.search_profiles(session, payload["query"])
                elif action == "community_servers":
                    result = cloud_sync.search_public_servers(session, payload["query"])
                elif action == "community_followed":
                    result = cloud_sync.list_followed_profiles(session)
                elif action == "community_user_servers":
                    profile = payload["profile"]
                    result = cloud_sync.list_public_servers(session, profile.user_id)
                elif action in {"community_follow", "community_unfollow"}:
                    mutate = (
                        cloud_sync.follow_profile
                        if action == "community_follow"
                        else cloud_sync.unfollow_profile
                    )
                    mutate(session, payload["user_id"])
                    result = None
                else:
                    raise cloud_sync.CloudError("Неизвестная операция сообщества.")
                cloud_sync.save_session(config_path, session)
                self._social_bridge.completed.emit({
                    "action": action,
                    "dialog": dialog,
                    "payload": payload,
                    "result": result,
                    "error": None,
                })
            except (cloud_sync.CloudError, OSError, ValueError) as exc:
                self._social_bridge.completed.emit({
                    "action": action,
                    "dialog": dialog,
                    "payload": payload,
                    "result": None,
                    "error": str(exc),
                })

        threading.Thread(target=worker, daemon=True).start()

    def _social_completed(self, response: dict) -> None:
        action = response["action"]
        dialog = response.get("dialog")
        error = response.get("error")
        if action.startswith("profile_") and dialog is self._profile_dialog:
            if error:
                dialog.status.setText(error)
            elif action == "profile_load":
                profile, avatar = response["result"]
                dialog.set_profile(profile, avatar)
            elif action == "profile_save":
                profile, avatar = response["result"]
                dialog.set_profile(profile, avatar)
                dialog.accept()
                QMessageBox.information(self, "Профиль", "Изменения сохранены.")
            return
        if dialog is not self._community_dialog:
            return
        request_id = response.get("payload", {}).get("request_id")
        if request_id is not None and request_id != self._community_request_id:
            return
        if error:
            dialog.set_status(error)
            return
        if action in {"community_profiles", "community_followed"}:
            dialog.set_profiles(
                response["result"], followed=action == "community_followed"
            )
        elif action in {"community_servers", "community_user_servers"}:
            dialog.set_servers(response["result"])
        elif action in {"community_follow", "community_unfollow"}:
            self._community_lookup(dialog.mode, dialog.search.text().strip())

    def _open_settings(self) -> None:
        if self._settings_dialog is None:
            dialog = SettingsDialog(self.controller.config, self)
            dialog.settingsChanged.connect(self._apply_settings)
            dialog.finished.connect(self._settings_closed)
            self._settings_dialog = dialog
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _settings_closed(self, _result: int) -> None:
        dialog = self._settings_dialog
        self._settings_dialog = None
        if dialog is not None:
            dialog.deleteLater()

    def _apply_settings(self, config: AppConfig) -> None:
        previous_hotkey = self.controller.config.hotkey_show_widget
        hotkey_error: OSError | RuntimeError | ValueError | None = None
        try:
            self.controller.set_hotkey(config.hotkey_show_widget)
        except (OSError, RuntimeError, ValueError) as exc:
            hotkey_error = exc
            log.warning("Не удалось применить новую горячую клавишу: %s", exc)
            config = replace(config, hotkey_show_widget=previous_hotkey)
        try:
            save_config(self.controller.config_path, config)
        except OSError as exc:
            log.exception("не удалось сохранить настройки")
            if hotkey_error is None:
                try:
                    self.controller.set_hotkey(previous_hotkey)
                except (OSError, RuntimeError, ValueError):
                    log.exception("не удалось восстановить прежнюю горячую клавишу")
            QMessageBox.critical(
                self, "Не удалось сохранить", f"Настройки не сохранены:\n{exc}"
            )
            return
        self.setStyleSheet(_window_stylesheet(config.accent_color))
        self._aurora_surface.setAccentColor(QColor(config.accent_color))
        self.controller.reload_config()
        self.controller.widget.resize_to_content()
        self.controller.widget.update()
        if hotkey_error is not None:
            self.controller.show_toast(
                "Остальные настройки сохранены. Сочетание занято — оставил прежнее."
            )

    def _on_search_changed(self, text: str) -> None:
        query = text.strip().casefold()
        visible_count = 0
        for address, card in self._cards.items():
            server = card.server
            searchable = [
                server.name,
                server.address,
                server.protocol,
                server.group_id or "",
                *server.priority_players,
            ]
            status = self.controller.statuses.get(address)
            if status is not None:
                searchable.extend(status.unique_players)
            visible = not query or any(
                query in value.casefold()
                for value in searchable
                if value
            )
            card.setVisible(visible)
            visible_count += int(visible)
        no_servers = not self.controller.config.servers
        no_results = bool(query and not no_servers and visible_count == 0)
        self.empty_title.setText(
            "Ничего не найдено" if no_results else "Пора в игру?"
        )
        self.empty_description.setText(
            "Нажми Enter для поиска серверов и игроков в общем каталоге."
            if no_results
            else "Добавь Minecraft-сервер, чтобы\nследить за онлайном и игроками."
        )
        self.empty_label.setVisible(no_servers or no_results)
        self.scroll.setVisible(not no_servers and not no_results)

    def _search_community(self) -> None:
        query = self.search_input.text().strip()
        if len(query) < 2:
            QMessageBox.information(
                self,
                "Поиск в каталоге",
                "Для поиска публичных серверов и игроков введи не менее двух символов.",
            )
            return
        self._open_community(initial_mode=1, initial_query=query)

    def _update_account_button(self) -> None:
        try:
            session = cloud_sync.load_session(self.controller.config_path)
        except cloud_sync.CloudError as exc:
            log.warning("Не удалось открыть сессию аккаунта: %s", exc)
            session = None
        self.account_button.setText(
            session.username[:1].upper() if session else ""
        )
        self.account_button.setIcon(
            QIcon() if session else _action_icon("account")
        )
        self.account_button.setToolTip(
            "Аккаунт: " + session.username
            if session
            else "Войти или создать аккаунт"
        )
        self.account_button.setAccessibleName(
            "Аккаунт " + session.username if session else "Войти в аккаунт"
        )

    def set_widget_visibility(self, visible: bool) -> None:
        self.widget_toggle_button.setText(
            "Убрать виджет" if visible else "Включить виджет"
        )
        self.widget_toggle_button.setToolTip(
            "Скрыть плавающий виджет" if visible else "Показать плавающий виджет"
        )

    def _toggle_search(self) -> None:
        opening = not self.search_input.isVisible()
        if opening:
            self.search_input.show()
            self.header_spacer.hide()
            self.search_button.setIcon(QIcon())
            self.search_button.setText("×")
            self.search_button.setToolTip("Закрыть поиск")
            self.search_button.setAccessibleName("Закрыть поиск")
            self.search_input.setFocus()
            return
        self.search_input.clear()
        self.search_input.hide()
        self.header_spacer.show()
        self.search_button.setText("")
        self.search_button.setIcon(_action_icon("search"))
        self.search_button.setToolTip("Поиск серверов")
        self.search_button.setAccessibleName("Поиск серверов")

    def _logout(self) -> None:
        try:
            cloud_sync.clear_session(self.controller.config_path)
        except OSError as exc:
            log.exception("не удалось удалить локальную сессию")
            QMessageBox.warning(self, "Не удалось выйти", str(exc))
            return
        self._update_account_button()
        QMessageBox.information(
            self, "Вы вышли", "Локальный список серверов оставлен на этом устройстве."
        )

    def _sync_existing_account(self) -> None:
        try:
            session = cloud_sync.load_session(self.controller.config_path)
        except cloud_sync.CloudError as exc:
            log.warning("Сохранённая сессия недоступна: %s", exc)
            return
        if session:
            self._start_cloud_operation("sync", silent=True)

    def _start_cloud_operation(
        self,
        mode: str,
        username: str = "",
        password: str = "",
        silent: bool = False,
    ) -> None:
        if self._cloud_busy:
            if mode == "sync":
                self._cloud_pending = True
            return
        if mode == "sync":
            try:
                if cloud_sync.load_session(self.controller.config_path) is None:
                    return
            except cloud_sync.CloudError as exc:
                QMessageBox.warning(self, "Сессия аккаунта", str(exc))
                return
        self._cloud_busy = True
        self.account_button.setEnabled(False)
        config_path = self.controller.config_path
        local_servers = list(self.controller.config.servers)
        worker = threading.Thread(
            target=self._cloud_worker,
            args=(mode, username, password, silent, config_path, local_servers),
            daemon=True,
        )
        worker.start()

    def _cloud_worker(
        self,
        mode: str,
        username: str,
        password: str,
        silent: bool,
        config_path,
        local_servers: list[ServerConfig],
    ) -> None:
        session = None
        try:
            if mode == "signup":
                session = cloud_sync.sign_up(username, password)
                cloud_sync.save_session(config_path, session)
            elif mode == "login":
                session = cloud_sync.sign_in(username, password)
                cloud_sync.save_session(config_path, session)
            else:
                session = cloud_sync.load_session(config_path)
                if session is None:
                    raise cloud_sync.CloudError("Сначала войди в аккаунт.")

            state = cloud_sync.read_sync_state(config_path)
            previous_user_id = state.get("user_id")
            base_servers = (
                cloud_sync.servers_from_state(state)
                if previous_user_id == session.user_id
                else None
            )
            merged = cloud_sync.synchronize(
                session,
                local_servers,
                base_servers,
                previous_user_id,
            )
            cloud_sync.save_session(config_path, session)
            cloud_sync.write_sync_state(config_path, session.user_id, merged)
            self._cloud_bridge.completed.emit({
                "session": session,
                "servers": merged,
                "local_snapshot": local_servers,
                "error": None,
                "silent": silent,
                "mode": mode,
            })
        except (cloud_sync.CloudError, OSError, ValueError) as exc:
            log.warning("Ошибка синхронизации: %s", exc)
            if session is not None:
                try:
                    cloud_sync.save_session(config_path, session)
                except OSError:
                    log.exception("не удалось сохранить сессию после ошибки")
            self._cloud_bridge.completed.emit({
                "session": session,
                "servers": None,
                "error": str(exc),
                "silent": silent,
                "mode": mode,
            })

    def _cloud_completed(self, result: dict) -> None:
        self._cloud_busy = False
        self.account_button.setEnabled(True)
        self._update_account_button()
        servers = result.get("servers")
        if servers is not None:
            local_snapshot = result.get("local_snapshot")
            if local_snapshot is not None:
                servers = cloud_sync.merge_server_lists(
                    local_snapshot,
                    list(self.controller.config.servers),
                    servers,
                )
            self._save_config(servers, sync=False)
        error = result.get("error")
        if error:
            if not result.get("silent"):
                QMessageBox.warning(self, "Синхронизация не выполнена", error)
        elif not result.get("silent"):
            QMessageBox.information(self, "Готово", "Список серверов синхронизирован.")
        if self._cloud_pending:
            self._cloud_pending = False
            QTimer.singleShot(0, lambda: self._start_cloud_operation("sync", silent=True))

    def refresh_servers(self) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()

        servers = self.controller.config.servers
        self._update_summary()
        self.empty_label.setVisible(not servers)
        self.scroll.setVisible(bool(servers))
        card_height = 0
        for server in servers:
            card = ServerCard(server, self.list_content)
            card.widgetMembershipRequested.connect(self._set_widget_membership)
            card.removeRequested.connect(self._remove_server)
            card.menuRequested.connect(self._show_server_menu)
            card.update_status(self.controller.statuses.get(server.address))
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)
            self._cards[server.address] = card
            card_height += card.sizeHint().height()
        if servers:
            card_height += self.list_layout.spacing() * (len(servers) - 1)
            self.scroll.setFixedHeight(min(360, max(120, card_height)))
        else:
            self.scroll.setFixedHeight(0)
        self._on_search_changed(self.search_input.text())
        desired_height = self.centralWidget().layout().sizeHint().height()
        self.resize(self.width(), min(760, max(580, desired_height)))

    def _update_summary(self) -> None:
        servers = self.controller.config.servers
        online_count = sum(
            1
            for server in servers
            if (status := self.controller.statuses.get(server.address)) is not None
            and status.online
        )
        self.summary_label.setText(
            f"{len(servers)} {self._server_word(len(servers))}  ·  {online_count} онлайн"
        )
        self.online_badge.setText(f"● {online_count} В СЕТИ")

    @staticmethod
    def _server_word(count: int) -> str:
        last_two = count % 100
        last = count % 10
        if 11 <= last_two <= 14:
            return "серверов"
        if last == 1:
            return "сервер"
        if 2 <= last <= 4:
            return "сервера"
        return "серверов"

    def refresh_statuses(self) -> None:
        for address, card in self._cards.items():
            card.update_status(self.controller.statuses.get(address))
        self._update_summary()

    def _add_server(self) -> None:
        dialog = ServerDialog(self)
        if dialog.exec() != QDialog.Accepted or dialog.server is None:
            return
        candidate = dialog.server
        if any(s.address.casefold() == candidate.address.casefold()
               for s in self.controller.config.servers):
            QMessageBox.information(self, "Сервер уже добавлен", "Этот адрес уже есть в списке.")
            return
        self._save_config([*self.controller.config.servers, candidate])

    def _remove_server(self, address: str) -> None:
        server = next(
            (item for item in self.controller.config.servers if item.address == address),
            None,
        )
        if server is None:
            return
        answer = QMessageBox.question(
            self,
            "Удалить сервер?",
            f"Убрать «{server.name}» из списка?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        servers = [
            item for item in self.controller.config.servers if item.address != address
        ]
        self._save_config(servers)

    def _show_server_menu(self, address: str, pos) -> None:
        server = next(
            (item for item in self.controller.config.servers if item.address == address),
            None,
        )
        if server is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1c1f28;
                color: #e7ecf3;
                border: 1px solid rgba(255, 255, 255, 26);
                border-radius: 8px;
                padding: 6px;
            }
            QMenu::item {
                padding: 8px 22px 8px 16px;
                border-radius: 6px;
                font-size: 12px;
            }
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 22);
            }
            QMenu::separator {
                height: 1px;
                background: rgba(255, 255, 255, 18);
                margin: 5px 10px;
            }
        """)

        priorities_action = menu.addAction("Приоритеты игроков…")
        priorities_action.triggered.connect(lambda: self._show_priorities(server))

        groups_action = menu.addAction("Добавить в группу…")
        groups_action.triggered.connect(lambda: self._show_groups(server))

        menu.addSeparator()

        edit_action = menu.addAction("Изменить…")
        edit_action.triggered.connect(lambda: self._edit_server(server))

        menu.addSeparator()

        delete_action = menu.addAction("Удалить")
        delete_action.triggered.connect(lambda: self._remove_server(address))

        menu.exec(pos)

    def _set_widget_membership(self, address: str, included: bool) -> None:
        servers = list(self.controller.config.servers)
        updated = [
            replace(server, show_in_widget=included)
            if server.address == address
            else server
            for server in servers
        ]
        if updated != servers:
            self._save_config(updated)

    def _show_priorities(self, server: ServerConfig) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Приоритеты игроков — " + server.name)
        dialog.setMinimumWidth(340)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Отслеживать вход и выход указанных игроков:"))
        from PySide6.QtWidgets import QTextEdit
        priorities_edit = QTextEdit()
        priorities_edit.setPlaceholderText("Например:\nNotch\nJeb_\nDinnerbone")
        priorities_edit.setPlainText("\n".join(server.priority_players))
        priorities_edit.setMaximumHeight(100)
        layout.addWidget(priorities_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            raw_names = priorities_edit.toPlainText().replace(",", "\n").splitlines()
            names: list[str] = []
            seen: set[str] = set()
            for raw_name in raw_names:
                name = raw_name.strip()
                if not name:
                    continue
                if len(name) > 32 or any(character.isspace() for character in name):
                    QMessageBox.warning(
                        self,
                        "Некорректный ник",
                        "Каждый ник должен быть не длиннее 32 символов и без пробелов.",
                    )
                    return
                if name.casefold() not in seen:
                    names.append(name)
                    seen.add(name.casefold())
            updated = [
                replace(item, priority_players=tuple(names))
                if item.address == server.address else item
                for item in self.controller.config.servers
            ]
            self._save_config(updated)

    def _show_groups(self, server: ServerConfig) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle('Добавить в группу')
        dialog.setMinimumWidth(340)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(QLabel('Выберите группу или создайте новую:'))
        from PySide6.QtWidgets import QComboBox
        group_combo = QComboBox()
        group_combo.addItem('(без группы)', None)
        groups = list(dict.fromkeys(
            item.group_id
            for item in self.controller.config.servers
            if item.group_id
        ))
        for group_id in groups:
            group_combo.addItem(group_id, group_id)
        group_combo.addItem('+ Создать новую группу…', '__new__')
        if server.group_id:
            existing_index = group_combo.findData(server.group_id)
            if existing_index >= 0:
                group_combo.setCurrentIndex(existing_index)
        layout.addWidget(group_combo)
        group_name_input = QLineEdit()
        group_name_input.setPlaceholderText('Название новой группы')
        group_name_input.setVisible(False)
        layout.addWidget(group_name_input)
        def on_group_changed(idx):
            show = group_combo.itemData(idx) == '__new__'
            group_name_input.setVisible(show)
        group_combo.currentIndexChanged.connect(on_group_changed)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Добавить')
        buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            group_id = group_combo.currentData()
            if group_id == '__new__':
                name = group_name_input.text().strip()
                if not name or len(name) > 60:
                    QMessageBox.warning(self, 'Группа', 'Название должно содержать от 1 до 60 символов.')
                    return
                existing = next(
                    (group for group in groups if group.casefold() == name.casefold()),
                    None,
                )
                group_id = existing or name
            servers = list(self.controller.config.servers)
            updated = []
            for s in servers:
                if s.address == server.address:
                    s = replace(s, group_id=group_id or None)
                updated.append(s)
            self._save_config(updated)

    def _edit_server(self, server: ServerConfig) -> None:
        dialog = ServerDialog(self)
        dialog.name_input.setText(server.name)
        dialog.address_input.setText(server.address)
        dialog.protocol_input.setCurrentText('Bedrock Edition' if server.protocol == 'bedrock' else 'Java Edition')
        if dialog.exec() != QDialog.Accepted or dialog.server is None:
            return
        updated_server = replace(
            dialog.server,
            show_in_widget=server.show_in_widget,
            group_id=server.group_id,
            sort_order=server.sort_order,
            priority_players=server.priority_players,
        )
        updated = [
            updated_server if item.address == server.address else item
            for item in self.controller.config.servers
        ]
        self._save_config(updated)

    def _save_config(self, servers: list[ServerConfig], *, sync: bool = True) -> None:
        updated = replace(self.controller.config, servers=servers)
        try:
            save_config(self.controller.config_path, updated)
        except OSError as exc:
            log.exception("не удалось сохранить список серверов")
            QMessageBox.critical(
                self, "Не удалось сохранить", f"Список серверов не сохранён:\n{exc}"
            )
            return
        self.controller.config = updated
        self.controller.reload_config()
        self.refresh_servers()
        if sync:
            self._start_cloud_operation("sync", silent=True)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        event.ignore()
        self.hide()
        if not self.controller.tray.available:
            self.controller.tray.show_unavailable_notice()


class TrayController:
    def __init__(self, app_controller) -> None:  # noqa: ANN001
        self.controller = app_controller
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.icon = QSystemTrayIcon(
            QIcon(str(resource_path("assets/icon.ico"))),
            app_controller.qapp,
        )
        self.icon.setToolTip("MC Monitor")
        menu = QMenu()
        menu.addAction("Открыть MC Monitor", self.show_window)
        self.widget_action = menu.addAction("Включить виджет")
        self.widget_action.triggered.connect(self.controller.toggle_widget)
        menu.addAction("Обновить серверы", app_controller.request_refresh)
        menu.addAction(
            "Проверить обновления",
            lambda: check_for_updates(app_controller.main_window),
        )
        menu.addSeparator()
        menu.addAction("Выход", app_controller.quit)
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._activated)

    def show(self) -> None:
        self.icon.show()
        if not self.available:
            log.warning("Системный трей недоступен")

    def hide(self) -> None:
        self.icon.hide()

    def show_window(self) -> None:
        window = self.controller.main_window
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def set_widget_visibility(self, visible: bool) -> None:
        self.widget_action.setText(
            "Убрать виджет" if visible else "Включить виджет"
        )

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.Trigger,
            QSystemTrayIcon.DoubleClick,
        ):
            self.show_window()

    def show_unavailable_notice(self) -> None:
        if not self.available:
            QMessageBox.information(
                self.controller.main_window,
                "Системный трей недоступен",
                "Окно скрыто, но Windows не сообщила о доступной области уведомлений.",
            )

    def show_message(self, title: str, message: str) -> bool:
        if self.available and self.icon.isVisible():
            self.icon.showMessage(
                title,
                message,
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
            return True
        return False
