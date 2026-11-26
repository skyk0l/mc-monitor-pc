"""Check GitHub Releases and download verified Windows builds."""
from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
)

from . import __version__
from .config import is_installed_by_setup, resource_path
from .i18n import tr

log = logging.getLogger(__name__)
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_VERSION_PATTERN = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$"
)
_MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
_DOWNLOAD_TIMEOUT_SECONDS = 45


class UpdateError(RuntimeError):
    """The release metadata or downloaded file is not usable."""


def _configured_repository() -> str:
    repository = os.environ.get("MCMONITOR_GITHUB_REPOSITORY", "").strip()
    if not repository:
        manifest_path = resource_path("version.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ""
        except (OSError, json.JSONDecodeError) as exc:
            raise UpdateError("Не удалось прочитать конфигурацию обновлений.") from exc
        if isinstance(manifest, dict):
            repository = str(manifest.get("repository") or "").strip()
    if repository and not _REPOSITORY_PATTERN.fullmatch(repository):
        raise UpdateError("В настройке обновлений указан неверный GitHub-репозиторий.")
    return repository


def _version_key(value: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.fullmatch(value.strip())
    if not match:
        raise UpdateError(f"Некорректный номер версии: {value!r}")
    return tuple(int(part) for part in match.groups())


def _release_info(payload: Any, repository: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UpdateError("GitHub вернул некорректные данные о релизе.")
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("У релиза не указан номер версии.")
    version = tag[1:] if tag.lower().startswith("v") else tag
    _version_key(version)

    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("У релиза нет списка файлов.")
    installer_name = f"MC-Monitor-Setup-{version}.exe"
    asset = next(
        (
            item
            for item in assets
            if isinstance(item, dict) and item.get("name") == installer_name
        ),
        None,
    )
    if asset is None:
        raise UpdateError(f"В релизе нет установщика {installer_name}.")

    url = str(asset.get("browser_download_url") or "")
    parsed_url = urllib.parse.urlparse(url)
    expected_prefix = f"/{repository}/releases/download/"
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "github.com"
        or not parsed_url.path.startswith(expected_prefix)
    ):
        raise UpdateError("Ссылка на обновление ведёт не на ожидаемый GitHub-релиз.")

    try:
        size = int(asset.get("size", 0))
    except (TypeError, ValueError) as exc:
        raise UpdateError("У файла обновления некорректный размер.") from exc
    if size <= 0 or size > _MAX_DOWNLOAD_BYTES:
        raise UpdateError("Размер файла обновления выходит за допустимые пределы.")

    digest = str(asset.get("digest") or "").strip().lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise UpdateError("В GitHub-релизе отсутствует корректная SHA-256 сумма.")

    return {
        "version": version,
        "installer_name": installer_name,
        "notes": str(payload.get("body") or ""),
        "url": url,
        "size": size,
        "sha256": digest.removeprefix("sha256:"),
        "release_url": str(payload.get("html_url") or ""),
    }


class UpdateChecker(QThread):
    """Fetch the latest stable GitHub release outside the UI thread."""

    found = Signal(object)
    error = Signal(str)

    def __init__(
        self, repository: str, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._repository = repository

    def run(self) -> None:
        try:
            url = f"https://api.github.com/repos/{self._repository}/releases/latest"
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "MC-Monitor-Updater/1.0",
                },
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.found.emit(_release_info(payload, self._repository))
        except (
            OSError,
            http.client.HTTPException,
            UnicodeError,
            json.JSONDecodeError,
            UpdateError,
        ) as exc:
            self.error.emit(str(exc))


class DownloadThread(QThread):
    """Download a release installer and verify its published digest."""

    progress = Signal(int)
    downloaded = Signal(str)
    error = Signal(str)

    def __init__(
        self, release: dict[str, Any], parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._release = release

    def run(self) -> None:
        temporary_path: Path | None = None
        try:
            downloads = Path.home() / "Downloads"
            downloads.mkdir(parents=True, exist_ok=True)
            version = str(self._release["version"])
            destination = downloads / str(self._release["installer_name"])

            request = urllib.request.Request(
                str(self._release["url"]),
                headers={"User-Agent": "MC-Monitor-Updater/1.0"},
            )
            digest = hashlib.sha256()
            size = 0
            with urllib.request.urlopen(
                request, timeout=_DOWNLOAD_TIMEOUT_SECONDS
            ) as response:
                content_length = response.headers.get("Content-Length")
                if content_length:
                    expected_size = int(content_length)
                    if expected_size <= 0 or expected_size > _MAX_DOWNLOAD_BYTES:
                        raise UpdateError("Размер файла обновления выходит за допустимые пределы.")
                else:
                    expected_size = int(self._release["size"])

                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix="mc-monitor-",
                    suffix=".part",
                    dir=downloads,
                    delete=False,
                ) as output:
                    temporary_path = Path(output.name)
                    while True:
                        if self.isInterruptionRequested():
                            raise UpdateError("Скачивание отменено.")
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > _MAX_DOWNLOAD_BYTES:
                            raise UpdateError("Размер файла обновления превышает лимит.")
                        output.write(chunk)
                        digest.update(chunk)
                        if expected_size:
                            self.progress.emit(min(100, size * 100 // expected_size))

            if size != int(self._release["size"]):
                raise UpdateError("Размер скачанного файла не совпал с размером релиза.")
            expected_digest = str(self._release.get("sha256") or "")
            if expected_digest and digest.hexdigest() != expected_digest:
                raise UpdateError("SHA-256 скачанного файла не совпал с релизом.")

            temporary_path.replace(destination)
            temporary_path = None
            self.downloaded.emit(str(destination))
        except (
            OSError,
            http.client.HTTPException,
            urllib.error.URLError,
            KeyError,
            ValueError,
            UpdateError,
        ) as exc:
            self.error.emit(str(exc))
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    log.exception("Не удалось удалить временный файл обновления")


class UpdateDialog(QDialog):
    """Download a verified installer for the running desktop release."""

    def __init__(
        self, release: dict[str, Any], parent=None, *, install_only: bool = False
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            tr("Установка MC Monitor")
            if install_only else tr("Доступно обновление")
        )
        self.setMinimumWidth(420)
        self._release = release
        self._downloaded_path: Path | None = None
        self._download_thread: DownloadThread | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        notes = str(release.get("notes") or "").strip()
        description = (
            tr("Версия {version} готова к установке.").format(
                version=release["version"]
            )
            if install_only
            else tr("Доступна версия {version}.").format(
                version=release["version"]
            )
        )
        layout.addWidget(
            QLabel(
                description + (f"\n\n{notes}" if notes else "")
            )
        )

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status = QLabel(
            tr("Проверенный установщик откроет мастер: можно выбрать папку, "
               "а после установки запустить MC Monitor.")
        )
        layout.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.download_button = buttons.addButton(
            tr("Открыть установщик")
            if install_only else tr("Скачать и установить"),
            QDialogButtonBox.AcceptRole,
        )
        self.close_button = buttons.addButton(
            tr("Закрыть"), QDialogButtonBox.RejectRole
        )
        self.download_button.clicked.connect(self._download_or_open)
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _download_or_open(self) -> None:
        if self._downloaded_path is not None:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self._downloaded_path.parent))
            )
            return
        if self._download_thread is not None:
            return

        self.download_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.status.setText(tr("Скачивание и проверка файла…"))
        thread = DownloadThread(self._release, self)
        self._download_thread = thread
        thread.progress.connect(self.progress.setValue)
        thread.downloaded.connect(self._on_downloaded)
        thread.error.connect(self._on_error)
        thread.finished.connect(lambda: setattr(self, "_download_thread", None))
        thread.start()

    def _on_downloaded(self, path: str) -> None:
        self._downloaded_path = Path(path)
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            self.status.setText(
                tr("Файл скачан и проверен:\n{path}").format(path=path)
            )
            self.download_button.setText(tr("Открыть папку"))
            self.download_button.setEnabled(True)
            self.close_button.setEnabled(True)
            return

        try:
            target = Path(sys.executable).resolve()
            os.startfile(
                str(self._downloaded_path.resolve()),
                "runas",
                f'/DIR="{target.parent}"',
            )
        except (OSError, ValueError) as exc:
            log.exception("Не удалось запустить установщик")
            self.status.setText(
                tr("Файл проверен, но установщик не запустился: {error}")
                .format(error=tr(str(exc)))
            )
            self.download_button.setText(tr("Открыть папку"))
            self.download_button.setEnabled(True)
            self.close_button.setEnabled(True)
            return

        self.status.setText(tr("Открываю мастер установки…"))
        self.download_button.setEnabled(False)
        self.close_button.setEnabled(False)
        QApplication.instance().quit()

    def _on_error(self, message: str) -> None:
        self.status.setText(tr("Ошибка: ") + tr(message))
        self.download_button.setEnabled(True)
        self.close_button.setEnabled(True)

    def reject(self) -> None:
        if self._download_thread is not None and self._download_thread.isRunning():
            return
        super().reject()


def check_for_updates(parent=None, *, silent: bool = False) -> None:
    """Check for a newer release; an unset repository disables automatic checks."""
    try:
        repository = _configured_repository()
    except UpdateError as exc:
        if silent:
            log.warning("Проверка обновлений отключена: %s", exc)
        else:
            QMessageBox.warning(parent, tr("Обновления"), tr(str(exc)))
        return

    if not repository:
        if not silent:
            QMessageBox.information(
                parent,
                tr("Обновления"),
                tr("Автообновление будет доступно после настройки GitHub-репозитория."),
            )
        return

    checker = UpdateChecker(repository, parent)

    def on_found(release: dict[str, Any]) -> None:
        try:
            available = _version_key(release["version"]) > _version_key(__version__)
        except (KeyError, UpdateError) as exc:
            on_error(str(exc))
            return
        if not available:
            needs_install = (
                sys.platform == "win32"
                and getattr(sys, "frozen", False)
                and not is_installed_by_setup()
            )
            if needs_install:
                UpdateDialog(release, parent, install_only=True).exec()
            elif not silent:
                QMessageBox.information(
                    parent, tr("Обновления"), tr("Установлена последняя версия.")
                )
            return
        dialog = UpdateDialog(release, parent)
        dialog.exec()

    def on_error(message: str) -> None:
        log.warning("Не удалось проверить обновления: %s", message)
        if not silent:
            QMessageBox.warning(
                parent, tr("Не удалось проверить обновления"), tr(message)
            )

    checker.found.connect(on_found)
    checker.error.connect(on_error)
    if parent is not None:
        setattr(parent, "_update_checker", checker)
    checker.start()
