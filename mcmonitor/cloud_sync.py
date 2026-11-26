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
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+$")


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
    share_followers: bool = False
    share_friends: bool = False
    share_subscriptions: bool = False
    is_followed_by: bool = False

    @property
    def avatar_url(self) -> str | None:
        if self.avatar_path is None:
            return None
        path = urllib.parse.quote(self.avatar_path, safe="/")
        return f"{PROJECT_URL}/storage/v1/object/public/avatars/{path}"


@dataclass(frozen=True)
class ProfileSocialSummary:
    followers_count: int
    subscriptions_count: int
    friends_count: int
    is_following: bool
    is_followed_by: bool
    can_view_servers: bool
    can_view_followers: bool
    can_view_friends: bool
    can_view_subscriptions: bool


@dataclass(frozen=True)
class PublicServer:
    owner_id: str
    username: str
    display_name: str
    name: str
    address: str
    protocol: str


@dataclass(frozen=True)
class CatalogServer:
    server_id: int
    name: str
    address: str
    protocol: str
    votes: int
    subscribers: int
    online_count: int
    has_voted_today: bool
    version: str = ""
    motd: str = ""
    favicon: str = ""


@dataclass(frozen=True)
class CatalogHistorySample:
    sampled_at: str
    online_count: int
    max_count: int
    players: tuple[str, ...]


def validate_credentials(username: str, password: str) -> None:
    if not isinstance(username, str) or not _USERNAME.fullmatch(username.strip()):
        raise CloudError("Никнейм: 3–16 латинских букв, цифр или _.")
    if not isinstance(password, str) or len(password) < 8:
        raise CloudError("Пароль должен содержать минимум 8 символов.")


def validate_email(email: str) -> str:
    clean = email.strip().lower() if isinstance(email, str) else ""
    if len(clean) > 254 or not _EMAIL.fullmatch(clean):
        raise CloudError("Укажи корректный адрес электронной почты.")
    return clean


def validate_login(identifier: str, password: str) -> str:
    clean = identifier.strip() if isinstance(identifier, str) else ""
    if "@" in clean:
        clean = validate_email(clean)
    elif not _USERNAME.fullmatch(clean):
        raise CloudError("Введи корректный никнейм или адрес электронной почты.")
    if not isinstance(password, str) or len(password) < 8:
        raise CloudError("Пароль должен содержать минимум 8 символов.")
    return clean


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
    identifier = validate_login(username, password)
    response = _request_json(
        "/auth/v1/token?grant_type=password",
        "POST",
        {
            "email": identifier if "@" in identifier else synthetic_email(identifier),
            "password": password,
        },
    )
    fallback_username = identifier.split("@", 1)[0] if "@" in identifier else identifier
    return _session_from_response(response, fallback_username)


def request_email_signup_code(email: str) -> None:
    clean_email = validate_email(email)
    _request_json(
        "/auth/v1/otp",
        "POST",
        {"email": clean_email, "create_user": True},
    )


def complete_email_signup(
    email: str,
    code: str,
    username: str,
    password: str,
) -> AccountSession:
    clean_email = validate_email(email)
    validate_credentials(username, password)
    clean_code = code.strip() if isinstance(code, str) else ""
    if not re.fullmatch(r"[0-9]{4,10}", clean_code):
        raise CloudError("Введи код из письма.")
    response = _request_json(
        "/auth/v1/verify",
        "POST",
        {"email": clean_email, "token": clean_code, "type": "signup"},
    )
    session = _session_from_response(response, username.strip())
    _request_with_session(
        "/auth/v1/user",
        "PUT",
        {"password": password, "data": {"username": username.strip()}},
        session,
    )
    _request_with_session(
        "/rest/v1/rpc/complete_email_registration",
        "POST",
        {"target_username": username.strip()},
        session,
    )
    session.username = username.strip()
    return session


def fetch_profile(session: AccountSession) -> PublicProfile:
    user_id = urllib.parse.quote(session.user_id, safe="")
    raw = _request_with_session(
        "/rest/v1/profiles?select=id,username,display_name,bio,avatar_path,share_servers,"
        "share_followers,share_friends,share_subscriptions"
        f"&id=eq.{user_id}",
        "GET",
        None,
        session,
    )
    rows = _decode_rows(raw, "профиль")
    if not rows:
        raise CloudError("Профиль не найден. Проверь, что миграция профилей применена.")
    return _profile_from_row(rows[0], session.user_id)


def fetch_public_profile(session: AccountSession, user_id: str) -> PublicProfile:
    target_id = _validate_user_id(user_id)
    raw = _request_with_session(
        "/rest/v1/profiles?select=id,username,display_name,bio,avatar_path,share_servers,"
        "share_followers,share_friends,share_subscriptions"
        f"&id=eq.{urllib.parse.quote(target_id, safe='')}",
        "GET",
        None,
        session,
    )
    rows = _decode_rows(raw, "публичный профиль")
    if not rows:
        raise CloudError("Этот профиль больше недоступен.")
    return _profile_from_row(rows[0], session.user_id)


def update_profile(
    session: AccountSession,
    display_name: str,
    bio: str,
    share_servers: bool,
    share_followers: bool = False,
    share_friends: bool = False,
    share_subscriptions: bool = False,
) -> PublicProfile:
    display_name = display_name.strip()
    bio = bio.strip()
    if len(display_name) > 32:
        raise CloudError("Отображаемое имя должно быть не длиннее 32 символов.")
    if len(bio) > 160:
        raise CloudError("Описание профиля должно быть не длиннее 160 символов.")
    if any(not isinstance(value, bool) for value in (
        share_servers, share_followers, share_friends, share_subscriptions
    )):
        raise CloudError("Настройка приватности имеет неверный формат.")
    user_id = urllib.parse.quote(session.user_id, safe="")
    raw = _request_with_session(
        f"/rest/v1/profiles?id=eq.{user_id}",
        "PATCH",
        {
            "display_name": display_name,
            "bio": bio,
            "share_servers": share_servers,
            "share_followers": share_followers,
            "share_friends": share_friends,
            "share_subscriptions": share_subscriptions,
        },
        session,
        prefer="return=representation",
    )
    rows = _decode_rows(raw, "профиль")
    if not rows:
        raise CloudError("Не удалось сохранить профиль. Проверь права Supabase.")
    return _profile_from_row(rows[0], session.user_id)


def get_profile_social_summary(
    session: AccountSession, user_id: str
) -> ProfileSocialSummary:
    target_id = _validate_user_id(user_id)
    raw = _request_with_session(
        "/rest/v1/rpc/get_profile_social_summary",
        "POST",
        {"target_user": target_id},
        session,
    )
    rows = _decode_rows(raw, "счётчики профиля")
    if len(rows) != 1:
        raise CloudError("Supabase не вернул данные социальных связей профиля.")
    row = rows[0]
    counts = (
        row.get("followers_count"),
        row.get("subscriptions_count"),
        row.get("friends_count"),
    )
    if any(type(value) is not int or value < 0 for value in counts):
        raise CloudError("Supabase вернул неверные счётчики профиля.")
    booleans = (
        "is_following", "is_followed_by", "can_view_servers",
        "can_view_followers", "can_view_friends", "can_view_subscriptions",
    )
    if any(not isinstance(row.get(key), bool) for key in booleans):
        raise CloudError("Supabase вернул неверные настройки видимости профиля.")
    return ProfileSocialSummary(
        followers_count=counts[0],
        subscriptions_count=counts[1],
        friends_count=counts[2],
        **{key: row[key] for key in booleans},
    )


def list_profile_relationships(
    session: AccountSession, user_id: str, relationship: str, limit: int = 50
) -> list[PublicProfile]:
    target_id = _validate_user_id(user_id)
    if relationship not in {"followers", "friends", "subscriptions"}:
        raise CloudError("Неизвестный тип списка профиля.")
    raw = _request_with_session(
        "/rest/v1/rpc/list_profile_relationships",
        "POST",
        {
            "target_user": target_id,
            "relationship": relationship,
            "result_limit": min(max(int(limit), 1), 100),
        },
        session,
    )
    return [
        _profile_from_row(row, session.user_id)
        for row in _decode_rows(raw, "список профиля")
    ]


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


def add_server_to_catalog(address: str, name: str, protocol: str) -> int:
    clean_address = (address or "").strip().lower()
    clean_name = (name or "").strip()
    clean_protocol = (protocol or "java").strip().lower()
    if (
        not 1 <= len(clean_address) <= 253
        or not re.fullmatch(r"[a-z0-9._:-]+", clean_address)
    ):
        raise CloudError("Укажи корректный адрес сервера.")
    if len(clean_name) > 60:
        raise CloudError("Название сервера не должно превышать 60 символов.")
    if clean_protocol not in {"java", "bedrock"}:
        raise CloudError("Выбран неизвестный протокол сервера.")
    raw = _request_text(
        "/rest/v1/rpc/add_server_to_catalog",
        "POST",
        {
            "server_address": clean_address,
            "server_name": clean_name,
            "server_protocol": clean_protocol,
        },
        None,
        None,
    )
    try:
        server_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise CloudError("Каталог серверов вернул неверный идентификатор.") from exc
    if server_id <= 0:
        raise CloudError("Каталог серверов вернул неверный идентификатор.")
    return server_id


def list_server_catalog(
    query: str = "",
    sort_by: str = "votes",
    limit: int = 50,
    session: AccountSession | None = None,
) -> list[CatalogServer]:
    term = query.strip() if isinstance(query, str) else ""
    if len(term) > 80:
        raise CloudError("Поисковый запрос слишком длинный.")
    if sort_by not in {"votes", "newest", "subscribers", "online"}:
        raise CloudError("Неизвестный порядок каталога серверов.")
    body = {
        "search_term": term,
        "sort_by": sort_by,
        "result_limit": min(max(int(limit), 1), 100),
    }
    if session is None:
        raw = _request_text(
            "/rest/v1/rpc/list_public_server_catalog",
            "POST",
            body,
            None,
            None,
        )
    else:
        raw = _request_with_session(
            "/rest/v1/rpc/list_public_server_catalog",
            "POST",
            body,
            session,
        )
    result: list[CatalogServer] = []
    for row in _decode_rows(raw, "каталог серверов"):
        try:
            server_id = int(row.get("id"))
            address = str(row.get("address") or "").strip()
            name = str(row.get("name") or "").strip()
            protocol = str(row.get("protocol") or "").strip().lower()
            votes = int(row.get("votes") or 0)
            subscribers = int(row.get("subscribers") or 0)
            online_value = row.get("online_count")
            online_count = -1 if online_value is None else int(online_value)
            version = str(row.get("version") or "")
            motd = str(row.get("motd") or "")
            favicon = str(row.get("favicon") or "")
        except (TypeError, ValueError) as exc:
            raise CloudError("В каталоге есть некорректная запись сервера.") from exc
        if (
            server_id <= 0
            or not 1 <= len(address) <= 253
            or not re.fullmatch(r"[A-Za-z0-9._:-]+", address)
            or protocol not in {"java", "bedrock"}
            or votes < 0
            or subscribers < 0
            or online_count < -1
            or len(version) > 100
            or len(motd) > 2000
            or len(favicon) > 60000
            or (favicon and not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", favicon))
        ):
            raise CloudError("В каталоге есть некорректная запись сервера.")
        result.append(
            CatalogServer(
                server_id,
                name,
                address,
                protocol,
                votes,
                subscribers,
                online_count,
                bool(row.get("has_voted_today", False)),
                version,
                motd,
                favicon,
            )
        )
    return result


def vote_for_catalog_server(session: AccountSession, server_id: int) -> bool:
    if isinstance(server_id, bool) or not isinstance(server_id, int) or server_id <= 0:
        raise CloudError("Выбран неверный сервер для голосования.")
    raw = _request_with_session(
        "/rest/v1/rpc/vote_for_catalog_server",
        "POST",
        {"server_id": server_id},
        session,
    )
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CloudError("Голосование вернуло неверный ответ.") from exc
    if not isinstance(result, bool):
        raise CloudError("Голосование вернуло неверный ответ.")
    return result


def report_server_catalog_status(
    session: AccountSession,
    address: str,
    is_online: bool,
    player_count: int,
    max_players: int,
    player_names: list[str],
    version: str = "",
    motd: str = "",
    favicon: str = "",
) -> None:
    clean_address = (address or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9._:-]{1,253}", clean_address):
        raise CloudError("Укажи корректный адрес сервера.")
    if not isinstance(is_online, bool):
        raise CloudError("Статус сервера имеет неверный формат.")
    if (
        isinstance(player_count, bool)
        or not isinstance(player_count, int)
        or player_count < 0
        or isinstance(max_players, bool)
        or not isinstance(max_players, int)
        or max_players < 0
        or (max_players > 0 and player_count > max_players)
    ):
        raise CloudError("Счётчик игроков имеет неверный формат.")
    if (
        not isinstance(player_names, list)
        or len(player_names) > 100
        or any(
            not isinstance(player, str)
            or not player.strip()
            or len(player.strip()) > 64
            for player in player_names
        )
    ):
        raise CloudError("Список игроков имеет неверный формат.")
    if (
        not isinstance(version, str)
        or len(version) > 100
        or not isinstance(motd, str)
        or len(motd) > 2000
        or not isinstance(favicon, str)
        or len(favicon) > 60000
        or (favicon and not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", favicon))
    ):
        raise CloudError("Информация сервера имеет неверный формат.")
    _request_with_session(
        "/rest/v1/rpc/report_server_catalog_status",
        "POST",
        {
            "server_address": clean_address,
            "is_online": is_online,
            "player_count": player_count if is_online else 0,
            "max_players": max_players,
            "player_names": [player.strip() for player in player_names]
            if is_online else [],
            "server_version": version.strip(),
            "server_motd": motd.strip(),
            "server_icon": favicon.strip(),
        },
        session,
    )


def list_server_catalog_history(
    server_id: int,
    session: AccountSession | None = None,
    limit: int = 300,
) -> list[CatalogHistorySample]:
    if isinstance(server_id, bool) or not isinstance(server_id, int) or server_id <= 0:
        raise CloudError("Выбран неверный сервер.")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise CloudError("Выбран неверный размер истории.")
    body = {"target_server_id": server_id, "result_limit": min(max(limit, 1), 500)}
    if session is None:
        raw = _request_text(
            "/rest/v1/rpc/get_server_catalog_history",
            "POST",
            body,
            None,
            None,
        )
    else:
        raw = _request_with_session(
            "/rest/v1/rpc/get_server_catalog_history",
            "POST",
            body,
            session,
        )
    result: list[CatalogHistorySample] = []
    for row in reversed(_decode_rows(raw, "история сервера")):
        sampled_at = str(row.get("sampled_at") or "").strip()
        try:
            online_count = int(row.get("online_count"))
            max_count = int(row.get("max_count"))
        except (TypeError, ValueError) as exc:
            raise CloudError("История сервера содержит неверные счётчики.") from exc
        players = row.get("players")
        if (
            not sampled_at
            or online_count < 0
            or max_count < 0
            or (max_count > 0 and online_count > max_count)
            or not isinstance(players, list)
            or len(players) > 100
            or any(
                not isinstance(player, str)
                or not player.strip()
                or len(player.strip()) > 64
                for player in players
            )
        ):
            raise CloudError("История сервера содержит некорректную запись.")
        result.append(
            CatalogHistorySample(
                sampled_at,
                online_count,
                max_count,
                tuple(player.strip() for player in players),
            )
        )
    return result


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
        share_followers=bool(row.get("share_followers", False)),
        share_friends=bool(row.get("share_friends", False)),
        share_subscriptions=bool(row.get("share_subscriptions", False)),
        is_followed_by=bool(row.get("is_followed_by", False)),
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
                    "pinned": server.pinned,
                    "added_at": server.added_at,
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
                "pinned": server.pinned,
                "added_at": server.added_at,
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
        pinned=server.pinned,
        added_at=server.added_at,
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
        and first.pinned == second.pinned
        and first.added_at == second.added_at
        and first.priority_players == second.priority_players
    )


def _session_from_response(data: dict[str, Any], username: str) -> AccountSession:
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    user = data.get("user")
    if not access or not refresh or not isinstance(user, dict):
        raise CloudError(
            "Supabase не вернул сессию. Проверь подтверждение email и настройку OTP."
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
