"""Основное окно управления серверами и системный трей."""
from __future__ import annotations

import logging
import threading
from dataclasses import replace

from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
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

WINDOW_QSS = """
QMainWindow {
    background: #10151b;
}
QLabel {
    color: #e8eee9;
    background: transparent;
    font-family: "Segoe UI";
    font-weight: 600;
}
QFrame#serverCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(32, 46, 49, 245), stop:1 rgba(22, 32, 37, 245));
    border: 1px solid rgba(153, 212, 184, 48);
    border-radius: 20px;
}
QFrame#serverCard:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(39, 58, 57, 250), stop:1 rgba(25, 40, 43, 250));
    border-color: rgba(157, 224, 185, 112);
}
QFrame#summaryCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(33, 54, 51, 238), stop:1 rgba(24, 43, 48, 238));
    border: 1px solid rgba(155, 216, 189, 56);
    border-radius: 18px;
}
QFrame#emptyCard {
    background: rgba(25, 39, 42, 220);
    border: 1px solid rgba(143, 209, 179, 60);
    border-radius: 20px;
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


class AuroraSurface(QWidget):
    """Медленно меняющийся тёмный фон с зелёно-бирюзовым свечением."""

    def __init__(self) -> None:
        super().__init__()
        self.phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._advance)
        self._timer.start()

    def _advance(self) -> None:
        self.phase = (self.phase + 0.006) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        bounds = self.rect()

        base = QLinearGradient(
            QPointF(bounds.topLeft()), QPointF(bounds.bottomRight())
        )
        base.setColorAt(0.0, QColor("#10151c"))
        base.setColorAt(0.52, QColor("#101b20"))
        base.setColorAt(1.0, QColor("#11151d"))
        painter.fillRect(bounds, base)

        travel = (self.phase * 2.0) - 0.5
        green_center = QPointF(bounds.width() * (0.18 + 0.48 * travel), bounds.height() * 0.23)
        green = QRadialGradient(green_center, max(bounds.width(), bounds.height()) * 0.74)
        green.setColorAt(0.0, QColor(67, 151, 110, 30))
        green.setColorAt(0.56, QColor(40, 119, 111, 16))
        green.setColorAt(1.0, QColor(22, 58, 60, 0))
        painter.fillRect(bounds, green)

        blue_center = QPointF(
            bounds.width() * (0.82 - 0.42 * travel),
            bounds.height() * 0.72,
        )
        blue = QRadialGradient(blue_center, max(bounds.width(), bounds.height()) * 0.68)
        blue.setColorAt(0.0, QColor(53, 121, 145, 22))
        blue.setColorAt(1.0, QColor(30, 70, 89, 0))
        painter.fillRect(bounds, blue)
        painter.end()


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


class ServerCard(QFrame):
    widgetRequested = Signal(str)
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
        widget_button = _button("ПЛАВАЮЩИЙ ВИДЖЕТ", "quietButton")
        widget_button.clicked.connect(
            lambda _checked=False, address=server.address: self.widgetRequested.emit(address)
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
            self.dot.setStyleSheet("color: #d8bd83; font-size: 13px;")
            self.count.setText("—")
            self.status_badge.setText("ПРОВЕРКА")
            self._set_status_badge("#e5c88c", "rgba(186, 146, 72, 36)")
            self.detail.setText("Проверяю сервер…")
            return
        if not status.online:
            self.dot.setStyleSheet("color: #ee8f89; font-size: 13px;")
            self.count.setText("—")
            self.status_badge.setText("ОФФЛАЙН")
            self._set_status_badge("#f0aaa3", "rgba(186, 80, 78, 40)")
            self.detail.setText(status.error or "Сервер недоступен")
            return
        self.dot.setStyleSheet("color: #8ed8ac; font-size: 13px;")
        self.status_badge.setText("ОНЛАЙН")
        self._set_status_badge("#a8e8bd", "rgba(85, 172, 117, 40)")
        maximum = f" / {status.max_players}" if status.max_players else ""
        self.count.setText(f"{status.online_count}{maximum}")
        details: list[str] = []
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


class MainWindow(QMainWindow):
    def __init__(self, app_controller) -> None:  # noqa: ANN001
        super().__init__()
        self.controller = app_controller
        self._cards: dict[str, ServerCard] = {}
        self._settings_dialog: SettingsDialog | None = None
        self.setWindowTitle("MC Monitor")
        self.setWindowIcon(QIcon(str(resource_path("assets/icon.ico"))))
        self.setMinimumSize(390, 500)
        self.resize(430, 530)
        self.setStyleSheet(WINDOW_QSS)

        surface = AuroraSurface()
        self.setCentralWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(13)

        # ---- Header: логотип + поиск + настройки + аватар ----
        header = QHBoxLayout()
        header.setSpacing(12)

        self.logo = QLabel()
        self.logo.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        pixmap = QIcon(str(resource_path("assets/mcm-logo.png"))).pixmap(360, 70)
        if not pixmap.isNull():
            self.logo.setPixmap(
                pixmap.scaled(
                    360, 70, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
        else:
            self.logo.setText("MC MONITOR")
            self.logo.setStyleSheet("font-size: 25px; font-weight: 900; color: #9de064;")
        self.logo.setFixedHeight(58)
        header.addWidget(self.logo, 1)

        # Поиск
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск серверов и пользователей…")
        self.search_input.setMinimumWidth(200)
        self.search_input.setMaximumWidth(320)
        self.search_input.textChanged.connect(self._on_search_changed)
        header.addWidget(self.search_input)

        self.settings_button = QToolButton()
        self.settings_button.setText("⚙")
        self.settings_button.setToolTip("Настройки")
        self.settings_button.setCursor(Qt.PointingHandCursor)
        self.settings_button.setStyleSheet(
            "QToolButton { color: #e8eee9; font-size: 18px; background: transparent; border: none; padding: 6px; }"
            "QToolButton:hover { color: #ffffff; background: rgba(255,255,255,12); border-radius: 10px; }"
        )
        self.settings_button.clicked.connect(self._open_settings)
        header.addWidget(self.settings_button)

        self.avatar_button = QToolButton()
        self.avatar_button.setFixedSize(34, 34)
        self.avatar_button.setCursor(Qt.PointingHandCursor)
        self.avatar_button.setStyleSheet(
            "QToolButton { color: #e8eee9; font-size: 16px; background: rgba(255,255,255,10); border: 1px solid rgba(255,255,255,26); border-radius: 17px; }"
            "QToolButton:hover { background: rgba(255,255,255,22); border-color: rgba(255,255,255,46); }"
        )
        self.avatar_button.setText("👤")
        self.avatar_button.setToolTip("Аккаунт и настройки")
        self.avatar_button.clicked.connect(self._account_clicked)
        header.addWidget(self.avatar_button)

        layout.addLayout(header)

        caption = QLabel("JAVA + BEDROCK  ·  МОНИТОРИНГ СЕРВЕРОВ")
        caption.setAlignment(Qt.AlignCenter)
        caption.setObjectName("sectionCaption")
        layout.addWidget(caption)

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
        self.account_button = _button("Войти", "quietButton")
        self.account_button.setMaximumWidth(110)
        self.account_button.setToolTip("Вход и синхронизация списка серверов")
        self.account_button.clicked.connect(self._account_clicked)
        summary_layout.addWidget(self.account_button)
        self.online_badge = QLabel("● 0 В СЕТИ")
        self.online_badge.setObjectName("onlineBadge")
        self.online_badge.setAlignment(Qt.AlignCenter)
        summary_layout.addWidget(self.online_badge)
        refresh_button = _button("↻", "quietButton")
        refresh_button.setFixedSize(38, 36)
        refresh_button.setToolTip("Обновить все серверы")
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
        empty_title = QLabel("Пора в игру?")
        empty_title.setObjectName("emptyTitle")
        empty_text.addWidget(empty_title)
        empty_description = QLabel(
            "Добавь Minecraft-сервер, чтобы\nследить за онлайном и игроками."
        )
        empty_description.setObjectName("emptyDescription")
        empty_description.setWordWrap(True)
        empty_text.addWidget(empty_description)
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

        show_widget = _button("Плавающий виджет", "quietButton")
        show_widget.clicked.connect(lambda: self.controller.show_widget())
        layout.addWidget(show_widget)

        footer = QLabel("Закрытие окна свернёт MC Monitor в системный трей")
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet("color: #748a82; font-size: 10px;")
        layout.addWidget(footer)

        self._cloud_bridge = _CloudBridge(self)
        self._cloud_bridge.completed.connect(self._cloud_completed)
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
        menu.addAction("Синхронизировать сейчас", lambda: self._start_cloud_operation("sync"))
        menu.addAction("Выйти из аккаунта", self._logout)
        menu.exec(self.account_button.mapToGlobal(
            QPoint(0, self.account_button.height())
        ))

    def _open_settings(self) -> None:
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self.controller.config, self)
            self._settings_dialog.settingsChanged.connect(self._apply_settings)
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _apply_settings(self, config: AppConfig) -> None:
        self.controller.config = config
        try:
            save_config(self.controller.config_path, config)
        except OSError as exc:
            log.exception("не удалось сохранить настройки")
            QMessageBox.critical(
                self, "Не удалось сохранить", f"Настройки не сохранены:\n{exc}"
            )
        self.controller.reload_config()

    def _on_search_changed(self, text: str) -> None:
        # Живой поиск по серверам — фильтруем карточки
        query = text.strip().lower()
        for address, card in self._cards.items():
            visible = not query or query in card.server.name.lower() or query in card.server.address.lower()
            card.setVisible(visible)

    def _update_account_button(self) -> None:
        try:
            session = cloud_sync.load_session(self.controller.config_path)
        except cloud_sync.CloudError as exc:
            log.warning("Не удалось открыть сессию аккаунта: %s", exc)
            session = None
        self.account_button.setText(session.username if session else "Войти")
        self.account_button.setToolTip(
            "Синхронизация серверов для " + session.username
            if session
            else "Вход и синхронизация списка серверов"
        )

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
            card.widgetRequested.connect(self.controller.show_widget)
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
        desired_height = self.centralWidget().layout().sizeHint().height()
        self.resize(self.width(), min(760, max(500, desired_height)))

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

        widget_text = "Убрать из виджета" if server.show_in_widget else "Показать в виджете"
        widget_action = menu.addAction(widget_text)
        widget_action.triggered.connect(lambda: self._toggle_widget(server))

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

    def _toggle_widget(self, server: ServerConfig) -> None:
        servers = list(self.controller.config.servers)
        updated = []
        for s in servers:
            if s.address == server.address:
                s = replace(s, show_in_widget=not s.show_in_widget)
            updated.append(s)
        self._save_config(updated)

    def _show_priorities(self, server: ServerConfig) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle('Приоритеits — ' + server.name)
        dialog.setMinimumWidth(340)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(QLabel('Никнеймы для отслеживания (через запятую):'))
        from PySide6.QtWidgets import QTextEdit
        priorities_edit = QTextEdit()
        priorities_edit.setPlaceholderText('Например: Notch, Jeb_, Dinnerbone')
        priorities_edit.setMaximumHeight(100)
        layout.addWidget(priorities_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Сохранить')
        buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            raw = priorities_edit.toPlainText()
            names = [n.strip() for n in raw.replace(',', '\n').split() if n.strip()]
            QMessageBox.information(self, 'Приоритеты', 'Никнеймы для «' + server.name + '»: ' + (', '.join(names) if names else '(пусто — уведомлений не будет)') + '\n\n(Приоритеты синхронизируются через Supabase при входе в аккаунт.)')

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
        group_combo.addItem('+ Создать новую группу…', '__new__')
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
                if name:
                    group_id = 'group_' + str(abs(hash(name)) % 100000)
                else:
                    QMessageBox.warning(self, 'Группа', 'Укажите название группы.')
                    return
            servers = list(self.controller.config.servers)
            updated = []
            for s in servers:
                if s.address == server.address:
                    s = replace(s, group_id=group_id)
                updated.append(s)
            self._save_config(updated)

    def _edit_server(self, server: ServerConfig) -> None:
        dialog = ServerDialog(self)
        dialog.name_input.setText(server.name)
        dialog.address_input.setText(server.address)
        dialog.protocol_input.setCurrentText('Bedrock Edition' if server.protocol == 'bedrock' else 'Java Edition')
        if dialog.exec() == QDialog.Accepted and dialog.server is not None:
            servers = list(self.controller.config.servers)
            updated = []
            for s in servers:
                if s.address == server.address:
                    s = dialog.server
                    s.show_in_widget = server.show_in_widget
                    s.group_id = server.group_id
                    s.sort_order = server.sort_order
                updated.append(s)
            self._save_config(updated)

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
        menu.addAction("Показать плавающий виджет", self.show_widget)
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

    def show_widget(self) -> None:
        self.controller.show_widget()

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
