"""Офлайн-проверки без интернета и без GUI: python tests/test_offline.py"""
from __future__ import annotations

import sys
import tempfile
import os
import ctypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcmonitor import api  # noqa: E402
from mcmonitor.auto_update import (  # noqa: E402
    UpdateError,
    _release_info,
    _version_key,
)
from mcmonitor import cloud_sync  # noqa: E402
from mcmonitor.config import (  # noqa: E402
    AppConfig,
    ConfigError,
    ServerConfig,
    load_config,
    save_config,
)
from mcmonitor.icon import make_icon, save_ico  # noqa: E402
from mcmonitor.hotkeys import _native_message_address, parse_hotkey  # noqa: E402
from mcmonitor.models import ServerStatus  # noqa: E402
from mcmonitor.notify import change_notice, new_players  # noqa: E402
from mcmonitor.ui_state import WindowState, load_state, save_state  # noqa: E402

MCSRVSTAT = {
    "online": True,
    "version": "1.21.4",
    "players": {
        "online": 2,
        "max": 20,
        "list": [{"name": "Steve", "uuid": "1"}, {"name": "Alex", "uuid": "2"}],
    },
    "motd": {"clean": ["§aHello", "world"], "raw": "..."},
}

MCSTATUS = {
    "online": True,
    "version": {"name_clean": "Paper 1.21", "name_raw": "§aPaper"},
    "players": {"online": 1, "max": 10, "list": [{"name_clean": "Notch"}]},
    "motd": {"clean": "Hi there"},
}


def test_global_hotkey_parsing() -> None:
    modifiers, key = parse_hotkey("Ctrl+Shift+M")
    assert modifiers == 0x0002 | 0x0004
    assert key == ord("M")
    assert parse_hotkey("Alt+F12") == (0x0001, 0x7B)
    for invalid in ("M", "Ctrl+Unknown+M", "Ctrl+Shift+M+N"):
        try:
            parse_hotkey(invalid)
        except ValueError:
            continue
        raise AssertionError(f"invalid hotkey accepted: {invalid}")


def test_native_hotkey_message_pointer() -> None:
    assert _native_message_address(123) == 123
    assert _native_message_address(ctypes.c_void_p(123)) == 123
    assert _native_message_address(object()) is None


def test_update_release_metadata() -> None:
    digest = "a" * 64
    release = _release_info(
        {
            "tag_name": "v1.2.0",
            "body": "Bug fixes",
            "html_url": "https://github.com/skyk0l/mc-monitor/releases/tag/v1.2.0",
            "assets": [
                {
                    "name": "MC-Monitor-Setup-1.2.0.exe",
                    "browser_download_url": (
                        "https://github.com/skyk0l/mc-monitor/releases/download/"
                        "v1.2.0/MC-Monitor-Setup-1.2.0.exe"
                    ),
                    "size": 20,
                    "digest": "sha256:" + digest,
                }
            ],
        },
        "skyk0l/mc-monitor",
    )
    assert release["version"] == "1.2.0"
    assert release["installer_name"] == "MC-Monitor-Setup-1.2.0.exe"
    assert release["sha256"] == digest
    assert _version_key("v1.2.0") > _version_key("1.1.9")


def test_update_rejects_untrusted_asset_url() -> None:
    try:
        _release_info(
            {
                "tag_name": "v1.2.0",
                "assets": [
                    {
                        "name": "MC-Monitor-Setup-1.2.0.exe",
                        "browser_download_url": "https://example.com/MC-Monitor-Setup-1.2.0.exe",
                        "size": 20,
                    }
                ],
            },
            "skyk0l/mc-monitor",
        )
    except UpdateError:
        return
    raise AssertionError("untrusted release URLs must be rejected")


def test_parse_mcsrvstat() -> None:
    status = api.parse_mcsrvstat(MCSRVSTAT)
    assert status.online
    assert status.online_count == 2 and status.max_players == 20
    assert status.players == ["Steve", "Alex"]
    assert status.version == "1.21.4"
    assert status.motd == "Hello\nworld"
    assert status.players_label == "2/20"
    assert status.first_motd_line == "Hello"


def test_parse_mcstatus() -> None:
    status = api.parse_mcstatus(MCSTATUS)
    assert status.online
    assert status.players == ["Notch"]
    assert status.version == "Paper 1.21"
    assert status.players_label == "1/10"


def test_player_list_helpers() -> None:
    status = ServerStatus(
        online=True,
        online_count=3,
        players=["Steve", "alex", "Steve", "Notch"],
    )
    assert status.unique_players == ["alex", "Notch", "Steve"]
    assert not status.hides_player_list


def test_hidden_player_list() -> None:
    status = ServerStatus(online=True, online_count=12, players=[])
    assert status.hides_player_list
    offline = ServerStatus(online=False, online_count=0, players=[])
    assert not offline.hides_player_list
    empty = ServerStatus(online=True, online_count=0, players=[])
    assert not empty.hides_player_list
    assert empty.unique_players == []


def test_offline_provider() -> None:
    status = api.parse_mcsrvstat({"online": False})
    assert not status.online
    assert status.players_label == "офлайн"
    assert status.players == []


def test_fallback_provider() -> None:
    def opener(url: str, timeout: float):
        assert timeout <= 5
        if "mcsrvstat" in url:
            raise TimeoutError("нет связи")
        return MCSTATUS

    status = api.fetch_status(ServerConfig("SMP", "example.org"), opener=opener)
    assert status.online
    assert status.players == ["Notch"]


def test_provider_offline_is_final() -> None:
    calls: list[str] = []

    def opener(url: str, timeout: float):
        calls.append(url)
        return {"online": False}

    status = api.fetch_status(ServerConfig("SMP", "example.org"), opener=opener)
    assert not status.online
    assert len(calls) == 1, "если сервер реально офлайн, второй API не нужен"


def test_all_providers_dead() -> None:
    def opener(url: str, timeout: float):
        raise OSError("offline")

    status = api.fetch_status(ServerConfig("SMP", "example.org"), opener=opener)
    assert not status.online
    assert status.error


def test_urls() -> None:
    plain = ServerConfig("S", "play.example.org")
    with_port = ServerConfig("S", "play.example.org", 25566)
    assert api.mcsrvstat_url(with_port).endswith("/play.example.org:25566")
    assert api.mcstatus_url(plain).endswith("/play.example.org")
    assert api.mcstatus_url(with_port).endswith("/play.example.org?port=25566")

    bedrock = ServerConfig("B", "play.example.org", 19132, "bedrock")
    assert api.mcsrvstat_bedrock_url(bedrock).endswith(
        "/bedrock/3/play.example.org:19132"
    )
    assert api.mcstatus_bedrock_url(bedrock).endswith(
        "/bedrock/play.example.org?port=19132"
    )


def test_bedrock_provider() -> None:
    calls: list[str] = []

    def opener(url: str, timeout: float):
        calls.append(url)
        if "mcsrvstat" in url:
            raise TimeoutError("нет связи")
        return MCSTATUS

    server = ServerConfig("Bedrock", "play.example.org", 19132, "bedrock")
    status = api.fetch_status(server, opener=opener)
    assert status.online and status.players == ["Notch"]
    assert "/bedrock/3/" in calls[0]
    assert "/status/bedrock/" in calls[1]


def test_config_parse() -> None:
    server = ServerConfig.parse("localhost:25566")
    assert (server.host, server.port, server.address) == ("localhost", 25566, "localhost:25566")
    assert server.protocol == "java"

    server = ServerConfig.parse({"name": "SMP", "address": "play.example.org"})
    assert server.name == "SMP" and server.port is None

    server = ServerConfig.parse({"name": "S", "host": "1.2.3.4", "port": 25565})
    assert server.address == "1.2.3.4:25565"

    bedrock = ServerConfig.parse({
        "name": "Bedrock SMP",
        "address": "play.example.org:19132",
        "protocol": "BEDROCK",
        "show_in_widget": True,
        "group_id": "Friends",
        "priority_players": ["Notch", " notch ", "Jeb_"],
    })
    assert bedrock.protocol == "bedrock"
    assert bedrock.show_in_widget and bedrock.group_id == "Friends"
    assert bedrock.priority_players == ("Notch", "Jeb_")

    for broken in ({}, {"name": "S"}, {"address": ""}, {
        "address": "play.example.org",
        "protocol": "invalid",
    }):
        try:
            ServerConfig.parse(broken)
        except ConfigError:
            pass
        else:
            raise AssertionError(f"должно падать: {broken}")


def test_config_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "servers.json"
        config = load_config(path)  # новая установка начинается с пустого списка
        assert path.exists() and config.servers == []

        config.servers.extend([
            ServerConfig(
                "SMP",
                "localhost",
                25565,
                show_in_widget=True,
                group_id="Friends",
                priority_players=("Notch",),
            ),
            ServerConfig("Bedrock", "localhost", 19132, "bedrock"),
        ])
        config.poll_interval = 30
        save_config(path, config)

        again = load_config(path)
        assert again.poll_interval == 30
        assert again.servers[0].address == "localhost:25565"
        assert again.servers[0].protocol == "java"
        assert again.servers[0].show_in_widget
        assert again.servers[0].group_id == "Friends"
        assert again.servers[0].priority_players == ("Notch",)
        assert again.servers[1].protocol == "bedrock"
        assert again.notify_joins is True


def test_config_interval_floor() -> None:
    config = AppConfig.from_dict({"poll_interval": 1, "servers": []})
    assert config.poll_interval == 15
    config = AppConfig.from_dict({"poll_interval": "abc", "servers": []})
    assert config.poll_interval == 15
    config = AppConfig.from_dict({"poll_interval": 60, "servers": []})
    assert config.poll_interval == 15


def test_widget_reload_preserves_status_cache() -> None:
    from types import SimpleNamespace

    from mcmonitor.widget import WidgetApp

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "servers.json"
        save_config(
            path,
            AppConfig(servers=[ServerConfig("SMP", "keep.example")]),
        )
        kept_status = ServerStatus(online=True, online_count=1, players=["Notch"])
        statuses = {
            "keep.example": kept_status,
            "removed.example": ServerStatus(online=True, online_count=2),
        }
        controller = object.__new__(WidgetApp)
        controller.config_path = path
        controller.statuses = statuses
        controller.widget = SimpleNamespace(
            statuses=statuses,
            display={},
            expanded=None,
            _hovered_row=None,
            resize_to_content=lambda: None,
            update=lambda: None,
        )
        controller.timer = SimpleNamespace(setInterval=lambda _interval: None)
        controller.set_hotkey = lambda _sequence: None
        controller.main_window = SimpleNamespace(refresh_servers=lambda: None)
        controller.show_toast = lambda _message: None
        controller.refresh = lambda: None

        WidgetApp.reload_config(controller)

        assert controller.statuses is statuses
        assert controller.widget.statuses is statuses
        assert statuses == {"keep.example": kept_status}


def test_priority_alert_uses_windows_notification_when_widget_is_visible() -> None:
    from types import SimpleNamespace

    from mcmonitor.widget import WidgetApp

    server = ServerConfig("SMP", "keep.example", priority_players=("Notch",))
    old = ServerStatus(online=True, online_count=1, players=[])
    new = ServerStatus(online=True, online_count=2, players=["Notch"])
    sent: list[tuple[str, str]] = []
    toasts: list[str] = []
    controller = object.__new__(WidgetApp)
    controller.config = AppConfig(servers=[server])
    controller.statuses = {server.address: old}
    controller.tray = SimpleNamespace(
        show_message=lambda title, message: sent.append((title, message)) or True
    )
    controller.widget = SimpleNamespace(
        isVisible=lambda: True,
        status_changed=lambda _address: None,
        mark_loading=lambda _loading: None,
        mark_updated=lambda: None,
    )
    controller.main_window = SimpleNamespace(refresh_statuses=lambda: None)
    controller.show_toast = toasts.append
    controller.pending = 1
    controller._refresh_again = False

    WidgetApp._on_status(controller, server, new)

    assert sent and "Notch" in sent[0][1]
    assert not toasts


def test_config_broken_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "servers.json"
        path.write_text("{не json", encoding="utf-8")
        try:
            load_config(path)
        except ConfigError:
            pass
        else:
            raise AssertionError("битый JSON должен давать ConfigError")


def test_installed_config_migrates_legacy_data() -> None:
    from unittest.mock import patch

    from mcmonitor import config

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        legacy = root / "portable"
        appdata = root / "roaming"
        legacy.mkdir()
        for name in (
            "servers.json",
            "window.json",
            "account.session",
            "sync_state.json",
            "mcmonitor.log",
        ):
            (legacy / name).write_text(name, encoding="utf-8")
        destination = appdata / "MC Monitor"
        destination.mkdir(parents=True)
        (destination / "account.session").write_text("new session", encoding="utf-8")

        with (
            patch.object(config, "app_dir", return_value=legacy),
            patch.object(config.sys, "frozen", True, create=True),
            patch.dict(os.environ, {"APPDATA": str(appdata)}),
        ):
            path = config.default_config_path()
            assert path == destination / "servers.json"
            assert path.read_text(encoding="utf-8") == "servers.json"
            assert (destination / "window.json").read_text(encoding="utf-8") == "window.json"
            assert (destination / "sync_state.json").read_text(encoding="utf-8") == "sync_state.json"
            assert (destination / "mcmonitor.log").read_text(encoding="utf-8") == "mcmonitor.log"
            assert (destination / "account.session").read_text(encoding="utf-8") == "new session"
            assert not config.is_installed_by_setup()
            (legacy / "installed.marker").write_text("1.3.6", encoding="utf-8")
            assert config.is_installed_by_setup()


def test_cloud_account_identifiers() -> None:
    cloud_sync.validate_credentials("rinav_123", "strong-pass-123")
    assert cloud_sync.synthetic_email("Rinav_123") == "rinav_123@users.mcmonitor.invalid"
    for username, password in (("x", "strong-pass-123"), ("bad name", "strong-pass-123"),
                                ("valid_name", "short")):
        try:
            cloud_sync.validate_credentials(username, password)
        except cloud_sync.CloudError:
            pass
        else:
            raise AssertionError("invalid cloud credentials should be rejected")


def test_cloud_server_merge() -> None:
    base = [
        ServerConfig("Old name", "one.example"),
        ServerConfig("Keep", "two.example"),
    ]
    local = [
        ServerConfig("Local rename", "one.example"),
        ServerConfig("Local add", "three.example"),
    ]
    remote = [
        ServerConfig("Remote rename", "one.example"),
        ServerConfig("Keep", "two.example"),
        ServerConfig("Remote add", "four.example"),
    ]
    merged = cloud_sync.merge_server_lists(base, local, remote)
    assert [(server.address, server.name) for server in merged] == [
        ("one.example", "Local rename"),
        ("three.example", "Local add"),
        ("four.example", "Remote add"),
    ]

    first_sync = cloud_sync.merge_server_lists(
        None,
        [ServerConfig("Local", "one.example")],
        [ServerConfig("Remote", "two.example")],
    )
    assert [server.address for server in first_sync] == ["one.example", "two.example"]

    edits_during_sync = cloud_sync.merge_server_lists(
        [ServerConfig("Before", "one.example")],
        [
            ServerConfig("Changed while syncing", "one.example"),
            ServerConfig("Added while syncing", "three.example"),
        ],
        [ServerConfig("Cloud rename", "one.example"), ServerConfig("Cloud add", "two.example")],
    )
    assert [(server.address, server.name) for server in edits_during_sync] == [
        ("one.example", "Changed while syncing"),
        ("three.example", "Added while syncing"),
        ("two.example", "Cloud add"),
    ]


def test_cloud_protocol_contract() -> None:
    session = cloud_sync.AccountSession("access", "refresh", "user", "nickname")
    bedrock = ServerConfig("Bedrock", "play.example.org", 19132, "bedrock")
    calls: list[dict] = []
    original_request = cloud_sync._request_with_session

    def capture_request(path, method, body, request_session, prefer=None):
        calls.append(body)
        return ""

    cloud_sync._request_with_session = capture_request
    try:
        cloud_sync.save_servers(session, [bedrock])
    finally:
        cloud_sync._request_with_session = original_request

    assert calls[0]["servers"] == [{
        "address": "play.example.org:19132",
        "name": "Bedrock",
        "protocol": "bedrock",
        "show_in_widget": False,
        "group_id": None,
        "sort_order": 0,
        "priority_players": [],
    }]
    assert cloud_sync._server_from_cloud({
        "address": "play.example.org",
        "name": "Legacy Java",
    }).protocol == "java"
    assert cloud_sync._server_from_cloud(calls[0]["servers"][0]).protocol == "bedrock"

    merged = cloud_sync.merge_server_lists(
        [ServerConfig("Server", "play.example.org")],
        [ServerConfig("Server", "play.example.org", protocol="bedrock")],
        [ServerConfig("Server", "play.example.org")],
    )
    assert merged[0].protocol == "bedrock"


def test_public_profile_validation_and_avatar_url() -> None:
    user_id = "123e4567-e89b-12d3-a456-426614174000"
    profile = cloud_sync._profile_from_row(
        {
            "id": user_id,
            "username": "BlockBuilder",
            "display_name": "Builder",
            "bio": "Hello",
            "avatar_path": user_id + "/avatar.jpg",
            "share_servers": False,
            "is_following": True,
        }
    )
    assert profile.is_following and not profile.share_servers
    assert profile.avatar_url.endswith("/avatars/" + user_id + "/avatar.jpg")
    try:
        cloud_sync._profile_from_row(
            {
                "id": user_id,
                "username": "BlockBuilder",
                "avatar_path": "../other/avatar.jpg",
            }
        )
    except cloud_sync.CloudError:
        pass
    else:
        raise AssertionError("an untrusted avatar path must be rejected")


def test_public_profile_requests_are_scoped_and_opt_in() -> None:
    user_id = "123e4567-e89b-12d3-a456-426614174000"
    session = cloud_sync.AccountSession("access", "refresh", user_id, "Current")
    calls: list[tuple[str, str, dict | bytes | None, dict]] = []
    original_request = cloud_sync._request_with_session

    def capture_request(path, method, body, request_session, prefer=None, **kwargs):
        calls.append((path, method, body, {"prefer": prefer, **kwargs}))
        if path.endswith("search_public_profiles"):
            return '[{"id":"' + user_id + '","username":"BlockBuilder","is_following":false}]'
        if path.startswith("/rest/v1/profiles?id="):
            return (
                '[{"id":"' + user_id + '","username":"Current",'
                '"display_name":"Builder","bio":"Hi","share_servers":false}]'
            )
        return "[]"

    cloud_sync._request_with_session = capture_request
    try:
        profile = cloud_sync.update_profile(session, " Builder ", " Hi ", False)
        matches = cloud_sync.search_profiles(session, "  block ")
    finally:
        cloud_sync._request_with_session = original_request

    assert profile.display_name == "Builder" and not profile.share_servers
    assert calls[0][0] == f"/rest/v1/profiles?id=eq.{user_id}"
    assert calls[0][2] == {
        "display_name": "Builder",
        "bio": "Hi",
        "share_servers": False,
    }
    assert calls[1][2] == {"search_term": "block", "result_limit": 20}
    assert matches[0].username == "BlockBuilder"


def test_public_profile_mutations_reject_self_follow_and_invalid_images() -> None:
    user_id = "123e4567-e89b-12d3-a456-426614174000"
    session = cloud_sync.AccountSession("access", "refresh", user_id, "Current")
    for action in (
        lambda: cloud_sync.follow_profile(session, user_id),
        lambda: cloud_sync.upload_avatar(session, b"not a jpeg"),
        lambda: cloud_sync.search_profiles(session, "x"),
    ):
        try:
            action()
        except cloud_sync.CloudError:
            pass
        else:
            raise AssertionError("invalid social input must be rejected")


def test_public_avatar_upload_is_owner_scoped_jpeg() -> None:
    user_id = "123e4567-e89b-12d3-a456-426614174000"
    session = cloud_sync.AccountSession("access", "refresh", user_id, "Current")
    calls: list[tuple[str, str, dict | bytes | None, dict]] = []
    original_request = cloud_sync._request_with_session

    def capture_request(path, method, body, request_session, prefer=None, **kwargs):
        calls.append((path, method, body, {"prefer": prefer, **kwargs}))
        if path.startswith("/rest/v1/profiles?select="):
            return (
                '[{"id":"' + user_id + '","username":"Current",'
                '"avatar_path":"' + user_id + '/avatar.jpg"}]'
            )
        return ""

    cloud_sync._request_with_session = capture_request
    try:
        profile = cloud_sync.upload_avatar(session, b"\xff\xd8\xffjpeg-data")
    finally:
        cloud_sync._request_with_session = original_request

    assert profile.avatar_path == user_id + "/avatar.jpg"
    assert calls[0][0] == f"/storage/v1/object/avatars/{user_id}/avatar.jpg"
    assert calls[0][2] == b"\xff\xd8\xffjpeg-data"
    assert calls[0][3]["content_type"] == "image/jpeg"
    assert calls[0][3]["headers"] == {"x-upsert": "true"}


def test_cloud_sync_state_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "servers.json"
        servers = [ServerConfig("SMP", "example.org", protocol="bedrock")]
        cloud_sync.write_sync_state(path, "user-id", servers)
        state = cloud_sync.read_sync_state(path)
        assert state["user_id"] == "user-id"
        assert cloud_sync.servers_from_state(state) == servers
        assert state["servers"][0]["protocol"] == "bedrock"


def test_cloud_session_protection() -> None:
    if os.name != "nt":
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "servers.json"
        session = cloud_sync.AccountSession("access", "refresh", "user", "nickname")
        cloud_sync.save_session(path, session)
        restored = cloud_sync.load_session(path)
        assert restored == session
        cloud_sync.clear_session(path)
        assert cloud_sync.load_session(path) is None


def test_notify_join_by_name() -> None:
    config = AppConfig(servers=[])
    old = ServerStatus(online=True, online_count=1, max_players=20, players=["Steve"])
    new = ServerStatus(online=True, online_count=2, max_players=20, players=["Steve", "Alex"])
    assert new_players(old, new) == ["Alex"]
    assert change_notice("SMP", old, new, config) is None
    notice = change_notice("SMP", old, new, config, ["alex"])
    assert notice and "Alex" in notice.message and "2/20" in notice.message


def test_notify_only_priority_players() -> None:
    config = AppConfig(servers=[])
    old = ServerStatus(online=True, online_count=5, max_players=100)
    new = ServerStatus(online=True, online_count=6, max_players=100)
    assert change_notice("SMP", old, new, config, ["Notch"]) is None
    old.players = ["Notch", "Alex"]
    new.players = ["Alex"]
    notice = change_notice("SMP", old, new, config, ["Notch"])
    assert notice and "вышел Notch" in notice.message


def test_notify_offline_online() -> None:
    config = AppConfig(servers=[])
    online = ServerStatus(online=True, online_count=1, max_players=10, players=["A"])
    offline = ServerStatus(online=False, error="timeout")
    assert change_notice("SMP", None, online, config) is None, "первый опрос — без спама"
    assert "недоступен" in change_notice("SMP", online, offline, config).message
    assert "снова онлайн" in change_notice("SMP", offline, online, config).message

    config.notify_joins = False
    online2 = ServerStatus(online=True, online_count=2, max_players=10, players=["A", "B"])
    assert change_notice("SMP", online, online2, config) is None


def test_icon() -> None:
    image = make_icon(True)
    assert image.size == (64, 64)
    assert make_icon(False).size == (64, 64)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "icon.ico"
        save_ico(path)
        assert path.exists() and path.stat().st_size > 0


def test_window_state_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "window.json"
        state = load_state(path)
        assert state.opacity == 0.93 and state.always_on_top is True

        state.x, state.y = 120, 340
        state.opacity = 0.75
        state.always_on_top = False
        state.widget_visible = False
        save_state(path, state)

        again = load_state(path)
        assert (again.x, again.y) == (120, 340)
        assert again.opacity == 0.75
        assert again.always_on_top is False
        assert again.widget_visible is False


def test_window_state_clamp() -> None:
    assert WindowState(opacity=0.1).clamped().opacity == 0.5
    assert WindowState(opacity=5.0).clamped().opacity == 1.0
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "window.json"
        path.write_text('{"opacity": 0.01, "x": "abc"}', encoding="utf-8")
        state = load_state(path)
        assert state.opacity == 0.5
        assert state.x is None


def main() -> None:
    tests = [
        test_global_hotkey_parsing,
        test_native_hotkey_message_pointer,
        test_parse_mcsrvstat,
        test_parse_mcstatus,
        test_player_list_helpers,
        test_hidden_player_list,
        test_offline_provider,
        test_fallback_provider,
        test_provider_offline_is_final,
        test_all_providers_dead,
        test_urls,
        test_bedrock_provider,
        test_update_release_metadata,
        test_update_rejects_untrusted_asset_url,
        test_config_parse,
        test_config_roundtrip,
        test_config_interval_floor,
        test_widget_reload_preserves_status_cache,
        test_priority_alert_uses_windows_notification_when_widget_is_visible,
        test_config_broken_json,
        test_installed_config_migrates_legacy_data,
        test_cloud_account_identifiers,
        test_cloud_server_merge,
        test_cloud_protocol_contract,
        test_public_profile_validation_and_avatar_url,
        test_public_profile_requests_are_scoped_and_opt_in,
        test_public_profile_mutations_reject_self_follow_and_invalid_images,
        test_public_avatar_upload_is_owner_scoped_jpeg,
        test_cloud_sync_state_roundtrip,
        test_cloud_session_protection,
        test_notify_join_by_name,
        test_notify_only_priority_players,
        test_notify_offline_online,
        test_icon,
        test_window_state_roundtrip,
        test_window_state_clamp,
    ]
    for test in tests:
        test()
        print(f"ok · {test.__name__}")
    print("OK: всё работает")


if __name__ == "__main__":
    main()
