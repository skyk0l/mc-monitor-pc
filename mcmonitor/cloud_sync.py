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
from uuid import UUID

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


@dataclass(frozen=True)
class PublicProfile:
    user_id: str
    username: str
    display_name: str
    bio: str
    avatar_path: str | None
    share_servers: bool
    is_following: bool

    @property
    def avatar_url(self) -> str | None:
        if self.avatar_path is None:
            return None
        path = urllib.parse.quote(self.avatar_path, safe="/")
        return f"{PROJECT_URL}/storage/v1/object/public/avatars/{path}"


@dataclass(frozen=True)
class PublicServer:
    owner_id: str
    username: str
    display_name: str
    name: str
    address: str
    protocol: str


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


def fetch_profile(session: AccountSession) -> PublicProfile:
    user_id = urllib.parse.quote(session.user_id, safe="")
    raw = _request_with_session(
        "/rest/v1/profiles?select=id,username,display_name,bio,avatar_path,share_servers"
        f"&id=eq.{user_id}",
        "GET",
        None,
        session,
    )
    rows = _decode_rows(raw, "профиль")
    if not rows:
        raise CloudError("Профиль не найден. Проверь, что миграция профилей применена.")
    return _profile_from_row(rows[0], session.user_id)


def update_profile(
    session: AccountSession,
    display_name: str,
    bio: str,
    share_servers: bool,
) -> PublicProfile:
    display_name = display_name.strip()
    bio = bio.strip()
    if len(display_name) > 32:
        raise CloudError("Отображаемое имя должно быть не длиннее 32 символов.")
    if len(bio) > 160:
        raise CloudError("Описание профиля должно быть не длиннее 160 символов.")
    if not isinstance(share_servers, bool):
        raise CloudError("Настройка публикации серверов имеет неверный формат.")
    user_id = urllib.parse.quote(session.user_id, safe="")
    raw = _request_with_session(
        f"/rest/v1/profiles?id=eq.{user_id}",
        "PATCH",
        {
            "display_name": display_name,
            "bio": bio,
            "share_servers": share_servers,
        },
        session,
        prefer="return=representation",
    )
    rows = _decode_rows(raw, "профиль")
    if not rows:
        raise CloudError("Не удалось сохранить профиль. Проверь права Supabase.")
    return _profile_from_row(rows[0], session.user_id)


def search_profiles(
    session: AccountSession, query: str, limit: int = 20
) -> list[PublicProfile]:
    term = _validated_search_term(query)
    raw = _request_with_session(
        "/rest/v1/rpc/search_public_profiles",
        "POST",
        {"search_term": term, "result_limit": min(max(int(limit), 1), 30)},
        session,
    )
    return [
        _profile_from_row(row, session.user_id)
        for row in _decode_rows(raw, "результаты поиска профилей")
    ]


def list_followed_profiles(
    session: AccountSession, limit: int = 50
) -> list[PublicProfile]:
    raw = _request_with_session(
        "/rest/v1/rpc/list_followed_profiles",
        "POST",
        {"result_limit": min(max(int(limit), 1), 100)},
        session,
    )
    return [
        _profile_from_row(row, session.user_id)
        for row in _decode_rows(raw, "список подписок")
    ]


def follow_profile(session: AccountSession, user_id: str) -> None:
    _validate_user_id(user_id, session.user_id)
    _request_with_session(
        "/rest/v1/profile_follows?on_conflict=follower_id,followed_id",
        "POST",
        {"follower_id": session.user_id, "followed_id": user_id},
        session,
        prefer="resolution=ignore-duplicates,return=minimal",
    )


def unfollow_profile(session: AccountSession, user_id: str) -> None:
    _validate_user_id(user_id, session.user_id)
    follower_id = urllib.parse.quote(session.user_id, safe="")
    followed_id = urllib.parse.quote(user_id, safe="")
    _request_with_session(
        "/rest/v1/profile_follows"
        f"?follower_id=eq.{follower_id}&followed_id=eq.{followed_id}",
        "DELETE",
        None,
        session,
    )


def search_public_servers(
    session: AccountSession, query: str, limit: int = 30
) -> list[PublicServer]:
    term = _validated_search_term(query)
    raw = _request_with_session(
        "/rest/v1/rpc/search_public_servers",
        "POST",
        {"search_term": term, "result_limit": min(max(int(limit), 1), 50)},
        session,
    )
    return [_server_from_public_row(row) for row in _decode_rows(raw, "серверы")]


def list_public_servers(
    session: AccountSession, user_id: str, limit: int = 50
) -> list[PublicServer]:
    _validate_user_id(user_id)
    raw = _request_with_session(
        "/rest/v1/rpc/list_public_servers",
        "POST",
        {"target_user": user_id, "result_limit": min(max(int(limit), 1), 100)},
        session,
    )
    return [
        _server_from_public_row(row, owner_id=user_id)
        for row in _decode_rows(raw, "публичные серверы")
    ]


def upload_avatar(session: AccountSession, jpeg: bytes) -> PublicProfile:
    if not isinstance(jpeg, bytes) or not jpeg.startswith(b"\xff\xd8\xff"):
        raise CloudError("Аватар должен быть изображением JPEG.")
    if len(jpeg) > 2 * 1024 * 1024:
        raise CloudError("Размер аватара не должен превышать 2 МБ.")
    user_id = _validate_user_id(session.user_id)
    _request_with_session(
        f"/storage/v1/object/avatars/{user_id}/avatar.jpg",
        "POST",
        jpeg,
        session,
        content_type="image/jpeg",
        headers={"x-upsert": "true"},
    )
    _request_with_session(
        f"/rest/v1/profiles?id=eq.{urllib.parse.quote(user_id, safe='')}",
        "PATCH",
        {"avatar_path": f"{user_id}/avatar.jpg"},
        session,
        prefer="return=minimal",
    )
    return fetch_profile(session)


def download_avatar(profile: PublicProfile) -> bytes | None:
    url = profile.avatar_url
    if url is None:
        return None
    request = urllib.request.Request(url, headers={"Accept": "image/jpeg"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            data = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise CloudError("Не удалось загрузить аватар.", exc.code) from exc
    except urllib.error.URLError as exc:
        raise CloudError(f"Нет соединения с Supabase: {exc.reason}") from exc
    if len(data) > 2 * 1024 * 1024 or not data.startswith(b"\xff\xd8\xff"):
        raise CloudError("Supabase вернул некорректный файл аватара.")
    return data


def _validated_search_term(query: str) -> str:
    term = query.strip()
    if not 2 <= len(term) <= 80:
        raise CloudError("Для поиска введи от 2 до 80 символов.")
    return term


def _validate_user_id(user_id: str, current_user_id: str | None = None) -> str:
    try:
        normalized = str(UUID(user_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise CloudError("У профиля неверный идентификатор.") from exc
    if current_user_id is not None and normalized == current_user_id:
        raise CloudError("Нельзя подписаться на собственный профиль.")
    return normalized


def _decode_rows(raw: str, description: str) -> list[dict[str, Any]]:
    try:
        rows = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise CloudError(f"Supabase вернул неверный формат: {description}.") from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise CloudError(f"Supabase вернул неверный формат: {description}.")
    return rows


def _profile_from_row(
    row: dict[str, Any], current_user_id: str | None = None
) -> PublicProfile:
    user_id = str(row.get("id") or "")
    try:
        user_id = str(UUID(user_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise CloudError("Supabase вернул профиль с неверным идентификатором.") from exc
    avatar_path = row.get("avatar_path")
    if avatar_path is not None and avatar_path != f"{user_id}/avatar.jpg":
        raise CloudError("Supabase вернул небезопасный путь аватара.")
    username = row.get("username")
    if not isinstance(username, str) or not _USERNAME.fullmatch(username):
        raise CloudError("Supabase вернул профиль с неверным никнеймом.")
    return PublicProfile(
        user_id=user_id,
        username=username,
        display_name=str(row.get("display_name") or ""),
        bio=str(row.get("bio") or ""),
        avatar_path=avatar_path,
        share_servers=bool(row.get("share_servers", False)),
        is_following=bool(row.get("is_following", False)),
    )


def _server_from_public_row(
    row: dict[str, Any], owner_id: str | None = None
) -> PublicServer:
    address = row.get("server_address")
    username = row.get("username", "")
    if not isinstance(address, str) or not address.strip():
        raise CloudError("Supabase вернул публичный сервер без адреса.")
    if not isinstance(username, str) or not _USERNAME.fullmatch(username):
        raise CloudError("Supabase вернул сервер с неверным владельцем.")
    protocol = row.get("protocol")
    if protocol not in {"java", "bedrock"}:
        protocol = "java"
    raw_owner_id = row.get("owner_id") or owner_id or ""
    try:
        validated_owner_id = _validate_user_id(str(raw_owner_id))
    except CloudError as exc:
        raise CloudError("Supabase вернул сервер с неверным владельцем.") from exc
    return PublicServer(
        owner_id=validated_owner_id,
        username=username,
        display_name=str(row.get("display_name") or ""),
        name=str(row.get("server_name") or address),
        address=address,
        protocol=protocol,
    )


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
                    "show_in_widget": server.show_in_widget,
                    "group_id": server.group_id,
                    "sort_order": server.sort_order,
                    "priority_players": list(server.priority_players),
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
                "show_in_widget": server.show_in_widget,
                "group_id": server.group_id,
                "sort_order": server.sort_order,
                "priority_players": list(server.priority_players),
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
    try:
        server = ServerConfig.parse(item)
    except ConfigError as exc:
        raise CloudError("В облачном списке есть некорректная запись сервера.") from exc
    return ServerConfig(
        name=server.name,
        host=server.host.lower(),
        port=server.port,
        protocol=server.protocol,
        show_in_widget=server.show_in_widget,
        group_id=server.group_id,
        sort_order=server.sort_order,
        priority_players=server.priority_players,
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
        and first.show_in_widget == second.show_in_widget
        and first.group_id == second.group_id
        and first.sort_order == second.sort_order
        and first.priority_players == second.priority_players
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
    body: dict[str, Any] | bytes | None,
    session: AccountSession,
    prefer: str | None = None,
    content_type: str | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    try:
        return _request_text(
            path, method, body, session.access_token, prefer, content_type, headers
        )
    except CloudError as exc:
        if exc.status_code != 401:
            raise
        _refresh_session(session)
        return _request_text(
            path, method, body, session.access_token, prefer, content_type, headers
        )


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
    body: dict[str, Any] | bytes | None,
    access_token: str | None,
    prefer: str | None,
    content_type: str | None = None,
    extra_headers: dict[str, str] | None = None,
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
    if isinstance(body, bytes):
        headers["Content-Type"] = content_type or "application/octet-stream"
        data = body
    elif body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    if extra_headers:
        headers.update(extra_headers)
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
