"""Настройки внешнего вида, цветов виджетов и горячих клавиш (только ПК)."""
from __future__ import annotations

import time
import uuid
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QTime, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .config import AppConfig, WidgetProfile, save_config


class ColorChip(QFrame):
    """Квадратик цвета с мягкой обводкой — выбранный акцент."""

    clicked = Signal(QColor)

    def __init__(self, color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self._selected = False
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self._polish()

    def _polish(self) -> None:
        c = self._color
        selected = "3px solid #ffffff" if self._selected else "1px solid rgba(255,255,255,60)"
        self.setStyleSheet(
            f"ColorChip {{ background: {c.name()}; border-radius: 8px; border: {selected}; }}"
        )

    def setSelected(self, selected: bool) -> None:
        self._selected = selected
        self._polish()

    def color(self) -> QColor:
        return self._color

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._color)


class GradientPreview(QWidget):
    """Анимированный градиент на основе выбранного акцентного цвета."""

    def __init__(self, accent: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._accent = accent
        self._offset = 0.0
        self.setFixedHeight(120)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    def setAccent(self, color: QColor) -> None:
        self._accent = color
        self.update()

    def _tick(self) -> None:
        self._offset = (self._offset + 0.5) % 360
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        c = self._accent
        lighter = c.lighter(140)
        darker = c.darker(130)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, darker)
        grad.setColorAt(0.5, c)
        grad.setColorAt(1.0, lighter)
        painter.setBrush(grad)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 16, 16)
        glow = QLinearGradient(rect.center(), rect.bottomRight())
        glow.setColorAt(0.0, QColor(255, 255, 255, 18))
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(glow)
        painter.drawEllipse(rect.center(), rect.width() // 3, rect.height() // 3)


class SettingsDialog(QDialog):
    """Окно настроек: внешний вид, цвета виджетов, горячие клавиши."""

    settingsChanged = Signal(AppConfig)

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки MC Monitor")
        self.setMinimumWidth(520)
        self.setMinimumHeight(620)
        self._config = config
        self._working = replace(config, widgets=list(config.widgets))
        self._profile_checks: list[tuple[str, QCheckBox]] = []
        self._loading_profile = False
        self._editing_profile_id: str | None = None

        self._build_ui()
        self._load_from_config()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(16)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setSpacing(20)

        cl.addWidget(self._section("Внешний вид"))

        self.accentLabel = QLabel("Основной цвет (акцент)")
        self.accentLabel.setStyleSheet("font-weight: 700; font-size: 13px;")
        cl.addWidget(self.accentLabel)

        self.gradientPreview = GradientPreview(QColor(self._working.accent_color))
        cl.addWidget(self.gradientPreview)

        chipRow = QHBoxLayout()
        chipRow.setSpacing(10)
        palette = [
            "#7ee0a3", "#9ec8ff", "#d3b4ff", "#ffb3c7", "#ffd79a",
            "#9ff0e0", "#ff8391", "#ffc978", "#a7c6a4", "#7fb3d9",
        ]
        self.accentChips: list = []
        for hexColor in palette:
            chip = ColorChip(QColor(hexColor))
            chip.clicked.connect(self._pickAccent)
            self.accentChips.append(chip)
            chipRow.addWidget(chip)
        chipRow.addStretch()
        cl.addLayout(chipRow)

        customBtn = QPushButton("Выбрать свой цвет…")
        customBtn.setCursor(Qt.PointingHandCursor)
        customBtn.clicked.connect(self._pickCustomColor)
        cl.addWidget(customBtn)

        cl.addWidget(self._section("Уведомления"))
        cl.addWidget(QLabel("Разрешённое время уведомлений (локальное время)"))
        notificationTimeRow = QHBoxLayout()
        start = self._working.notification_start_minute
        end = self._working.notification_end_minute
        self.notificationStart = QTimeEdit(QTime(start // 60, start % 60))
        self.notificationEnd = QTimeEdit(
            QTime((end % 1440) // 60, end % 60)
        )
        self.notificationStart.setDisplayFormat("HH:mm")
        self.notificationEnd.setDisplayFormat("HH:mm")
        notificationTimeRow.addWidget(QLabel("С"))
        notificationTimeRow.addWidget(self.notificationStart)
        notificationTimeRow.addWidget(QLabel("до"))
        notificationTimeRow.addWidget(self.notificationEnd)
        notificationTimeRow.addStretch(1)
        cl.addLayout(notificationTimeRow)
        self.dndMode = QComboBox()
        for title, duration in (
            ("Не беспокоить выключен", 0),
            ("На 1 час", 3600),
            ("На 3 часа", 10800),
            ("На 8 часов", 28800),
            ("Пока не отключу", -1),
            ("Оставить активным", -2),
        ):
            self.dndMode.addItem(title, duration)
        dnd_until = self._working.notification_dnd_until
        if dnd_until == -1:
            self.dndMode.setCurrentIndex(4)
        elif dnd_until > time.time():
            self.dndMode.setCurrentIndex(5)
        cl.addWidget(self.dndMode)
        self.dndStatus = QLabel()
        self.dndStatus.setStyleSheet("color: #8ca99d; font-size: 11px;")
        self.dndMode.currentIndexChanged.connect(self._update_dnd_status)
        cl.addWidget(self.dndStatus)
        self._update_dnd_status()

        cl.addWidget(self._section("Цвета виджетов"))

        self.widgetBgBtn = self._colorButton("Фон виджета", self._working.widget_bg_color)
        cl.addWidget(self.widgetBgBtn)
        self.widgetAccentBtn = self._colorButton("Акцент виджета", self._working.widget_accent_color)
        cl.addWidget(self.widgetAccentBtn)

        opacityRow = QHBoxLayout()
        opacityLabel = QLabel("Прозрачность фона виджета")
        self.opacityValue = QLabel(f"{int(self._working.widget_opacity * 100)}%")
        self.opacityValue.setStyleSheet("color: #8ca99d;")
        from PySide6.QtWidgets import QSlider
        self.opacitySlider = QSlider(Qt.Horizontal)
        self.opacitySlider.setRange(40, 100)
        self.opacitySlider.setValue(int(self._working.widget_opacity * 100))
        self.opacitySlider.valueChanged.connect(
            lambda v: self.opacityValue.setText(f"{v}%")
        )
        opacityRow.addWidget(opacityLabel)
        opacityRow.addWidget(self.opacitySlider)
        opacityRow.addWidget(self.opacityValue)
        cl.addLayout(opacityRow)

        cl.addWidget(self._section("НЕСКОЛЬКО ВИДЖЕТОВ"))
        cl.addWidget(QLabel(
            "Создавай отдельные окна с собственным именем, серверами и оформлением."
        ))
        profileRow = QHBoxLayout()
        self.widgetProfileSelector = QComboBox()
        self.widgetProfileSelector.currentIndexChanged.connect(
            self._profile_selection_changed
        )
        profileRow.addWidget(self.widgetProfileSelector, 1)
        addProfile = QPushButton("＋")
        addProfile.setToolTip("Создать виджет")
        addProfile.clicked.connect(self._add_widget_profile)
        profileRow.addWidget(addProfile)
        self.deleteProfileButton = QPushButton("Удалить")
        self.deleteProfileButton.clicked.connect(self._delete_widget_profile)
        profileRow.addWidget(self.deleteProfileButton)
        cl.addLayout(profileRow)

        self.widgetProfileName = QLineEdit()
        self.widgetProfileName.setPlaceholderText("Название виджета")
        cl.addWidget(self.widgetProfileName)
        self.widgetProfileServerArea = QWidget()
        self.widgetProfileServerLayout = QGridLayout(self.widgetProfileServerArea)
        self.widgetProfileServerLayout.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.widgetProfileServerArea)
        self.profileBgBtn = self._colorButton("Фон", self._working.widget_bg_color)
        self.profileAccentBtn = self._colorButton(
            "Акцент", self._working.widget_accent_color
        )
        self.profileBgBtn.clicked.disconnect()
        self.profileAccentBtn.clicked.disconnect()
        self.profileBgBtn.clicked.connect(
            lambda: self._pick_profile_color(self.profileBgBtn)
        )
        self.profileAccentBtn.clicked.connect(
            lambda: self._pick_profile_color(self.profileAccentBtn)
        )
        cl.addWidget(self.profileBgBtn)
        cl.addWidget(self.profileAccentBtn)
        self.profileOpacity = QSlider(Qt.Horizontal)
        self.profileOpacity.setRange(40, 100)
        self.profileOpacityValue = QLabel()
        profileOpacityRow = QHBoxLayout()
        profileOpacityRow.addWidget(QLabel("Прозрачность"))
        profileOpacityRow.addWidget(self.profileOpacity)
        profileOpacityRow.addWidget(self.profileOpacityValue)
        self.profileOpacity.valueChanged.connect(
            lambda value: self.profileOpacityValue.setText(f"{value}%")
        )
        cl.addLayout(profileOpacityRow)
        self._refresh_profile_selector()

        cl.addWidget(self._section("Горячие клавиши (ПК)"))

        hkLabel = QLabel("Показать / скрыть плавающий виджет")
        hkLabel.setStyleSheet("font-weight: 700; font-size: 13px;")
        cl.addWidget(hkLabel)
        hkNote = QLabel("Нажмите клавиши в поле ниже, чтобы переназначить.")
        hkNote.setStyleSheet("color: #8ca99d; font-size: 11px;")
        cl.addWidget(hkNote)

        self.hotkeyEdit = QKeySequenceEdit()
        from PySide6.QtGui import QKeySequence
        self.hotkeyEdit.setKeySequence(QKeySequence(self._working.hotkey_show_widget))
        self.hotkeyEdit.setMinimumWidth(200)
        cl.addWidget(self.hotkeyEdit)

        cl.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.button(QDialogButtonBox.Save).setDefault(True)
        buttons.button(QDialogButtonBox.Save).setObjectName("primaryButton")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _section(self, title: str) -> QLabel:
        label = QLabel(title)
        label.setStyleSheet(
            "color: #8ca99d; font-size: 9px; font-weight: 900; letter-spacing: 1.3px; margin-top: 4px;"
        )
        return label

    def _colorButton(self, label: str, currentHex: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setProperty("currentColor", currentHex)
        btn.setStyleSheet(
            f"QPushButton {{ background: {currentHex}; color: #10231a; border: none; border-radius: 10px; padding: 10px; font-weight: 800; text-align: left; }} "
            f"QPushButton:hover {{ opacity: 0.9; }}"
        )
        btn.clicked.connect(lambda: self._pickWidgetColor(btn))
        return btn

    def _load_from_config(self) -> None:
        accent = QColor(self._working.accent_color)
        self.gradientPreview.setAccent(accent)
        for chip in self.accentChips:
            chip.setSelected(chip.color().name() == self._working.accent_color)

    def _update_dnd_status(self) -> None:
        mode = self.dndMode.currentData()
        if mode == 0:
            text = "Не беспокоить выключен."
        elif mode == -1:
            text = "Не беспокоить будет активно до ручного отключения."
        elif mode == -2 and self._working.notification_dnd_until == -1:
            text = "Не беспокоить активно — до отключения вручную."
        elif mode == -2 and self._working.notification_dnd_until > time.time():
            end_time = time.strftime(
                "%H:%M", time.localtime(self._working.notification_dnd_until)
            )
            text = f"Не беспокоить активно до {end_time}."
        else:
            text = "Не беспокоить будет включён после сохранения."
        self.dndStatus.setText(text)

    def _pickAccent(self, color: QColor) -> None:
        self._working = replace(self._working, accent_color=color.name())
        self.gradientPreview.setAccent(color)
        for chip in self.accentChips:
            chip.setSelected(chip.color().name() == color.name())

    def _pickCustomColor(self) -> None:
        current = QColor(self._working.accent_color)
        color = QColorDialog.getColor(current, self, "Выберите основной цвет")
        if color.isValid():
            self._pickAccent(color)

    def _pickWidgetColor(self, btn: QPushButton) -> None:
        current = QColor(btn.property("currentColor"))
        color = QColorDialog.getColor(current, self, "Выберите цвет")
        if color.isValid():
            btn.setProperty("currentColor", color.name())
            hexName = color.name()
            if btn == self.widgetBgBtn:
                self._working = replace(self._working, widget_bg_color=hexName)
            elif btn == self.widgetAccentBtn:
                self._working = replace(self._working, widget_accent_color=hexName)
            btn.setStyleSheet(
                f"QPushButton {{ background: {hexName}; color: #10231a; border: none; border-radius: 10px; padding: 10px; font-weight: 800; text-align: left; }} "
                f"QPushButton:hover {{ opacity: 0.9; }}"
            )

    def _refresh_profile_selector(self, selected_id: str | None = None) -> None:
        self._loading_profile = True
        self.widgetProfileSelector.clear()
        for profile in self._working.widgets:
            self.widgetProfileSelector.addItem(profile.name, profile.id)
        if selected_id:
            index = self.widgetProfileSelector.findData(selected_id)
            if index >= 0:
                self.widgetProfileSelector.setCurrentIndex(index)
        self._loading_profile = False
        self._load_widget_profile()

    def _profile_selection_changed(self, _index: int) -> None:
        if self._loading_profile:
            return
        self._save_widget_profile()
        self._load_widget_profile()

    def _load_widget_profile(self) -> None:
        profile_id = self.widgetProfileSelector.currentData()
        self._editing_profile_id = profile_id
        profile = next(
            (item for item in self._working.widgets if item.id == profile_id),
            None,
        )
        self._loading_profile = True
        self.widgetProfileName.setEnabled(profile is not None)
        self.widgetProfileName.setText(profile.name if profile else "")
        self.deleteProfileButton.setEnabled(profile is not None)
        self.profileBgBtn.setEnabled(profile is not None)
        self.profileAccentBtn.setEnabled(profile is not None)
        self.profileOpacity.setEnabled(profile is not None)
        while self.widgetProfileServerLayout.count():
            item = self.widgetProfileServerLayout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._profile_checks.clear()
        if profile is not None:
            selected = set(profile.server_addresses)
            for index, server in enumerate(self._working.servers):
                checkbox = QCheckBox(server.name)
                checkbox.setToolTip(server.address)
                checkbox.setChecked(server.address in selected)
                self.widgetProfileServerLayout.addWidget(checkbox, index, 0)
                self._profile_checks.append((server.address, checkbox))
            self._set_profile_color_button(
                self.profileBgBtn, profile.widget_bg_color
            )
            self._set_profile_color_button(
                self.profileAccentBtn, profile.widget_accent_color
            )
            self.profileOpacity.setValue(round(profile.widget_opacity * 100))
        else:
            self._set_profile_color_button(
                self.profileBgBtn, self._working.widget_bg_color
            )
            self._set_profile_color_button(
                self.profileAccentBtn, self._working.widget_accent_color
            )
            self.profileOpacity.setValue(round(self._working.widget_opacity * 100))
        self._loading_profile = False

    def _save_widget_profile(self) -> None:
        if self._loading_profile:
            return
        profile_id = self._editing_profile_id
        if not profile_id:
            return
        name = self.widgetProfileName.text().strip()
        if not name:
            name = "Виджет"
        selected = tuple(
            address for address, checkbox in self._profile_checks
            if checkbox.isChecked()
        )
        background = str(self.profileBgBtn.property("currentColor"))
        accent = str(self.profileAccentBtn.property("currentColor"))
        updated = []
        for profile in self._working.widgets:
            if profile.id == profile_id:
                profile = replace(
                    profile,
                    name=name[:40],
                    server_addresses=selected,
                    widget_bg_color=background,
                    widget_accent_color=accent,
                    widget_opacity=self.profileOpacity.value() / 100,
                )
            updated.append(profile)
        servers = self._working.servers
        if updated and updated[0].id == profile_id:
            selected_set = set(selected)
            servers = [
                replace(server, show_in_widget=server.address in selected_set)
                for server in servers
            ]
        self._working = replace(self._working, widgets=updated, servers=servers)
        index = self.widgetProfileSelector.findData(profile_id)
        if index >= 0:
            self.widgetProfileSelector.setItemText(index, name[:40])

    def _add_widget_profile(self) -> None:
        self._save_widget_profile()
        profile = WidgetProfile(
            id=uuid.uuid4().hex,
            name=f"Виджет {len(self._working.widgets) + 1}",
            server_addresses=tuple(
                server.address for server in self._working.servers
                if server.show_in_widget
            ) if not self._working.widgets else (),
            widget_bg_color=self._working.widget_bg_color,
            widget_accent_color=self._working.widget_accent_color,
            widget_opacity=self._working.widget_opacity,
        )
        self._working = replace(
            self._working, widgets=[*self._working.widgets, profile]
        )
        self._refresh_profile_selector(profile.id)

    def _delete_widget_profile(self) -> None:
        profile_id = self.widgetProfileSelector.currentData()
        if not profile_id:
            return
        widgets = [
            item for item in self._working.widgets if item.id != profile_id
        ]
        servers = self._working.servers
        if not widgets:
            servers = [
                replace(server, show_in_widget=False)
                for server in servers
            ]
        self._working = replace(
            self._working,
            widgets=widgets,
            servers=servers,
        )
        self._refresh_profile_selector()

    def _pick_profile_color(self, button: QPushButton) -> None:
        current = QColor(button.property("currentColor"))
        color = QColorDialog.getColor(current, self, "Выберите цвет")
        if color.isValid():
            self._set_profile_color_button(button, color.name())

    @staticmethod
    def _set_profile_color_button(button: QPushButton, color: str) -> None:
        button.setProperty("currentColor", color)
        button.setStyleSheet(
            f"QPushButton {{ background: {color}; color: #10231a; border: none; "
            "border-radius: 10px; padding: 10px; font-weight: 800; text-align: left; } "
            "QPushButton:hover { opacity: 0.9; }"
        )

    def _save(self) -> None:
        self._save_widget_profile()
        hk = self.hotkeyEdit.keySequence().toString()
        notification_start = (
            self.notificationStart.time().hour() * 60
            + self.notificationStart.time().minute()
        )
        notification_end = (
            self.notificationEnd.time().hour() * 60
            + self.notificationEnd.time().minute()
        )
        if notification_start == 0 and notification_end == 0:
            notification_end = 1440
        self._working = replace(
            self._working,
            hotkey_show_widget=hk,
            widget_opacity=self.opacitySlider.value() / 100,
            notification_start_minute=notification_start,
            notification_end_minute=notification_end,
        )
        dnd_duration = self.dndMode.currentData()
        if dnd_duration == -1:
            dnd_until = -1
        elif dnd_duration == -2:
            dnd_until = self._working.notification_dnd_until
        elif dnd_duration:
            dnd_until = time.time() + dnd_duration
        else:
            dnd_until = 0
        self._working = replace(
            self._working, notification_dnd_until=dnd_until
        )
        self.settingsChanged.emit(self._working)
        self.accept()