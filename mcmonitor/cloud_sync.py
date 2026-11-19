"""Supabase authentication and per-account server-list synchronization."""
from __future__ import annotations

import ctypes
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ConfigError, ServerConfig

PROJECT_URL = "https://yjxacgziailzkzghmxgi.supabase.co"
PUBLISHABLE_KEY = "sb_publishable_-6kGEzd6QYBe0B7ww1kODw_HC6wg789"
_TIMEOUT_SECONDS = 12
_USERNAME = re.compile(r"^[A-Za-z0-9_]{3,16}$")


class CloudError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class AccountSession:
    access_token: str
    refresh_token: str
    user_id: str
    username: str


def validate_credentials(username: str, password: str) -> None:
    if not _USERNAME.fullmatch(username.strip()):
        raise CloudError("Никнейм: 3–16 латинских букв, цифр или _.")
    if len(password) < 8:
        raise CloudError("Пароль должен содержать минимум 8 символов.")


def synthetic_email(username: str) -> str:
    return f"{username.strip().lower()}@users.mcmonitor.invalid"


def sign_up(username: str, password: str) -> AccountSession:
    validate_credentials(username, password)
    response = _request_json(
        "/auth/v1/signup",
        "POST",
        {
            "email": synthetic_email(username),
            "password": password,
            "data": {"username": username.strip()},
        },
    )
    return _session_from_response(response, username.strip())


def sign_in(username: str, password: str) -> AccountSession:
    validate_credentials(username, password)
    response = _request_json(
        "/auth/v1/token?grant_type=password",
        "POST",
        {"email": synthetic_email(username), "password": password},
    )
    return _session_from_response(response, username.strip())


def synchronize(
    session: AccountSession,
    local_servers: list[ServerConfig],
    base_servers: list[ServerConfig] | None,
    previous_user_id: str | None,
) -> list[ServerConfig]:
    remote_servers = fetch_servers(session)
    if previous_user_id and previous_user_id != session.user_id:
        merged = remote_servers
    else:
        merged = merge_server_lists(base_servers, local_servers, remote_servers)
    save_servers(session, merged)
    return merged


def fetch_servers(session: AccountSession) -> list[ServerConfig]:
    user_id = urllib.parse.quote(session.user_id, safe="")
    raw = _request_with_session(
        f"/rest/v1/server_lists?select=servers&user_id=eq.{user_id}",
        "GET",
        None,
        session,
    )
    try:
        rows = json.loads(raw or "[]")
        if not isinstance(rows, list) or not rows:
            return []
        records = rows[0].get("servers", [])
        if not isinstance(records, list):
            raise CloudError("Облачный список серверов имеет неверный формат.")
        return [_server_from_cloud(item) for item in records if isinstance(item, dict)]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CloudError("Не удалось прочитать список серверов из облака.") from exc


def save_servers(session: AccountSession, servers: list[ServerConfig]) -> None:
    _request_with_session(
        "/rest/v1/server_lists?on_conflict=user_id",
        "POST",
        {
            "user_id": session.user_id,
            "servers": [
                {
                    "address": _canonical_address(server),
                    "name": server.name.strip(),
                    "protocol": server.protocol,
                }
                for server in servers
            ],
        },
        session,
        prefer="resolution=merge-duplicates,return=minimal",
    )


def merge_server_lists(
    base: list[ServerConfig] | None,
    local: list[ServerConfig],
    remote: list[ServerConfig],
) -> list[ServerConfig]:
    local_map = {_canonical_address(server): server for server in local}
    remote_map = {_canonical_address(server): server for server in remote}
    if base is None:
        merged = dict(local_map)
        for address, server in remote_map.items():
            merged.setdefault(address, server)
        return list(merged.values())

    base_map = {_canonical_address(server): server for server in base}
    addresses = dict.fromkeys((*base_map, *local_map, *remote_map))
    result: list[ServerConfig] = []
    for address in addresses:
        base_server = base_map.get(address)
        local_server = local_map.get(address)
        remote_server = remote_map.get(address)
        local_changed = not _same_server(base_server, local_server)
        remote_changed = not _same_server(base_server, remote_server)
        selected = (
            local_server
            if local_changed
            else remote_server
            if remote_changed
            else base_server
        )
        if selected is not None:
            result.append(selected)
    return result


def save_session(config_path: Path, session: AccountSession) -> None:
    payload = json.dumps(
        {
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            "user_id": session.user_id,
            "username": session.username,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    protected = _protect_windows_data(payload)
    path = _session_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(protected)


def load_session(config_path: Path) -> AccountSession | None:
    path = _session_path(config_path)
    if not path.exists():
        return None
    try:
        payload = _unprotect_windows_data(path.read_bytes())
        data = json.loads(payload.decode("utf-8"))
        return AccountSession(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            user_id=data["user_id"],
            username=data["username"],
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CloudError(
            "Не удалось открыть сохранённую сессию. Войди в аккаунт заново."
        ) from exc


def clear_session(config_path: Path) -> None:
    path = _session_path(config_path)
    if path.exists():
        path.unlink()


def read_sync_state(config_path: Path) -> dict[str, Any]:
    path = _state_path(config_path)
    if not path.exists():
        return {"user_id": None, "servers": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state must be an object")
        return data
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise CloudError("Не удалось прочитать локальное состояние синхронизации.") from exc


def write_sync_state(
    config_path: Path,
    user_id: str,
    servers: list[ServerConfig],
) -> None:
    path = _state_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "user_id": user_id,
        "servers": [
            {
                "address": _canonical_address(server),
                "name": server.name,
                "protocol": server.protocol,
            }
            for server in servers
        ],
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def servers_from_state(state: dict[str, Any]) -> list[ServerConfig] | None:
    raw = state.get("servers")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise CloudError("Локальная копия серверов имеет неверный формат.")
    result: list[ServerConfig] = []
    try:
        for item in raw:
            if isinstance(item, dict):
                result.append(_server_from_cloud(item))
    except (ConfigError, TypeError) as exc:
        raise CloudError("Не удалось прочитать локальную копию серверов.") from exc
    return result


def _server_from_cloud(item: dict[str, Any]) -> ServerConfig:
    address = str(item.get("address") or "").strip()
    if not address:
        raise CloudError("В облачном списке есть сервер без адреса.")
    name = str(item.get("name") or address).strip()
    server = ServerConfig.from_address(
        address, name, protocol=str(item.get("protocol") or "java")
    )
    return ServerConfig(
        name=server.name,
        host=server.host.lower(),
        port=server.port,
        protocol=server.protocol,
    )


def _canonical_address(server: ServerConfig) -> str:
    return server.address.lower()


def _same_server(first: ServerConfig | None, second: ServerConfig | None) -> bool:
    if first is None or second is None:
        return first is second
    return (
        _canonical_address(first) == _canonical_address(second)
        and first.name == second.name
        and first.protocol == second.protocol
    )


def _session_from_response(data: dict[str, Any], username: str) -> AccountSession:
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    user = data.get("user")
    if not access or not refresh or not isinstance(user, dict):
        raise CloudError(
            "Для входа по никнейму отключи подтверждение email в "
            "Supabase → Authentication → Providers → Email."
        )
    metadata = user.get("user_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    user_id = str(user.get("id") or "")
    if not user_id:
        raise CloudError("Supabase не вернул идентификатор аккаунта.")
    return AccountSession(
        access_token=str(access),
        refresh_token=str(refresh),
        user_id=user_id,
        username=str(metadata.get("username") or username),
    )


def _refresh_session(session: AccountSession) -> None:
    data = _request_json(
        "/auth/v1/token?grant_type=refresh_token",
        "POST",
        {"refresh_token": session.refresh_token},
    )
    refreshed = _session_from_response(data, session.username)
    session.access_token = refreshed.access_token
    session.refresh_token = refreshed.refresh_token
    session.user_id = refreshed.user_id
    session.username = refreshed.username


def _request_with_session(
    path: str,
    method: str,
    body: dict[str, Any] | None,
    session: AccountSession,
    prefer: str | None = None,
) -> str:
    try:
        return _request_text(path, method, body, session.access_token, prefer)
    except CloudError as exc:
        if exc.status_code != 401:
            raise
        _refresh_session(session)
        return _request_text(path, method, body, session.access_token, prefer)


def _request_json(path: str, method: str, body: dict[str, Any]) -> dict[str, Any]:
    raw = _request_text(path, method, body, None, None)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CloudError("Supabase вернул ответ в неверном формате.") from exc
    if not isinstance(data, dict):
        raise CloudError("Supabase вернул ответ в неверном формате.")
    return data


def _request_text(
    path: str,
    method: str,
    body: dict[str, Any] | None,
    access_token: str | None,
    prefer: str | None,
) -> str:
    headers = {
        "apikey": PUBLISHABLE_KEY,
        "Accept": "application/json",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if prefer:
        headers["Prefer"] = prefer
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        PROJECT_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise CloudError(_response_error(payload), exc.code) from exc
    except urllib.error.URLError as exc:
        raise CloudError(f"Нет соединения с Supabase: {exc.reason}") from exc
    except TimeoutError as exc:
        raise CloudError("Превышено время ожидания ответа Supabase.") from exc


def _response_error(payload: str) -> str:
    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            for field in ("msg", "message", "error_description", "error"):
                message = data.get(field)
                if message:
                    return str(message)
    except json.JSONDecodeError:
        pass
    return payload or "Ошибка запроса к Supabase."


def _session_path(config_path: Path) -> Path:
    return config_path.parent / "account.session"


def _state_path(config_path: Path) -> Path:
    return config_path.parent / "sync_state.json"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _protect_windows_data(data: bytes) -> bytes:
    return _transform_windows_data(data, protect=True)


def _unprotect_windows_data(data: bytes) -> bytes:
    return _transform_windows_data(data, protect=False)


def _transform_windows_data(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise OSError("Защищённое хранение сессии доступно только в Windows.")
    from ctypes import wintypes

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source_buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(
        len(data),
        ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output = _DataBlob()
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    if protect:
        function = crypt32.CryptProtectData
        function.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        function.restype = wintypes.BOOL
        success = function(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)
        )
    else:
        description = wintypes.LPWSTR()
        function = crypt32.CryptUnprotectData
        function.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        function.restype = wintypes.BOOL
        success = function(
            ctypes.byref(source),
            ctypes.byref(description),
            None,
            None,
            None,
            1,
            ctypes.byref(output),
        )
        if description:
            local_free(ctypes.cast(description, ctypes.c_void_p))

    if not success:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        local_free(ctypes.cast(output.pbData, ctypes.c_void_p))
