from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

import pytest

from mcmonitor import cloud_sync
from mcmonitor.cloud_sync import (
    CloudError,
    PublicProfile,
    _response_error,
)


def test_missing_profile_privacy_column_points_to_migration() -> None:
    message = _response_error(json.dumps({
        "code": "42703",
        "message": 'column profiles.share_followers does not exist',
    }))

    assert "supabase_profile_social_details.sql" in message
    assert "share_followers" not in message


def test_unknown_cloud_error_does_not_expose_host_or_response() -> None:
    private_host = "hidden-project.supabase.co"
    message = _response_error(json.dumps({
        "message": f"connection failed at https://{private_host}/rest/v1",
    }))

    assert private_host not in message
    assert "https://" not in message
    assert message == "Облачный сервис временно недоступен. Повторите попытку позже."


def test_offline_error_does_not_expose_host() -> None:
    private_host = "hidden-project.supabase.co"
    with patch.object(
        cloud_sync.urllib.request,
        "urlopen",
        side_effect=urllib.error.URLError(private_host),
    ):
        with pytest.raises(CloudError) as error:
            cloud_sync._request_text("/rest/v1/profiles", "GET", None, None, None)

    assert private_host not in str(error.value)
    assert "https://" not in str(error.value)


def test_list_public_servers_uses_profile_owner_metadata() -> None:
    profile = PublicProfile(
        user_id="0e86f87b-0102-4e22-8175-2a56d122dcd1",
        username="test123",
        display_name="Test Player",
        bio="",
        avatar_path=None,
        share_servers=True,
        is_following=False,
    )

    with patch.object(
        cloud_sync,
        "_request_with_session",
        return_value=json.dumps([{
            "server_name": "Survival",
            "server_address": "play.example.net",
            "protocol": "java",
        }]),
    ) as request:
        servers = cloud_sync.list_public_servers(None, profile)

    assert request.call_args.args[0] == "/rest/v1/rpc/list_public_servers"
    assert request.call_args.args[2]["target_user"] == profile.user_id
    assert len(servers) == 1
    server = servers[0]
    assert server.owner_id == profile.user_id
    assert server.username == profile.username
    assert server.display_name == profile.display_name
    assert server.address == "play.example.net"
