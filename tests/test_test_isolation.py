"""Exercise the isolation boundaries without touching real services or data."""

import asyncio
import importlib
import logging
import os
import socket
from pathlib import Path
from unittest.mock import Mock

import dotenv
import httpx
import pytest
import requests

from _isolation import COPIED_PATH_CONSTANTS, CREDENTIAL_ENV_VARS, find_copied_path_constants
from src.connectors import google_auth, microsoft_auth
from src.core import logging_config
from src.storage import db


def test_real_credential_environment_is_removed():
    assert all(name not in os.environ for name in CREDENTIAL_ENV_VARS)


def test_dotenv_loading_is_disabled_even_when_a_file_exists(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("ISOLATION_PROBE=must-not-load\n", encoding="utf-8")
    monkeypatch.delenv("ISOLATION_PROBE", raising=False)

    assert dotenv.load_dotenv(path, override=True) is False
    assert "ISOLATION_PROBE" not in os.environ


def test_database_is_temporary_even_without_requesting_temp_db(tmp_path):
    assert db.DEFAULT_DB_PATH.is_relative_to(tmp_path)
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_credentials_and_logs_are_written_only_to_test_directory(isolated_files):
    credentials = Mock()
    credentials.to_json.return_value = '{"token": "synthetic"}'
    google_auth.save_credentials_for_account("probe", credentials)
    ms_cache = Mock(has_state_changed=True)
    ms_cache.serialize.return_value = "synthetic-ms-cache"
    microsoft_auth.save_ms_token_cache("probe", ms_cache)
    logging_config.configure_logging()
    logging_config.get_logger("isolation").warning("SYNTHETIC_TEST_LOG")

    assert (isolated_files / "google_token_probe.json").read_text() == '{"token": "synthetic"}'
    assert (isolated_files / "ms_token_probe.json").read_text() == "synthetic-ms-cache"
    assert "SYNTHETIC_TEST_LOG" in (isolated_files / "debug.log").read_text()
    for handler in logging.getLogger("calendar_agent").handlers:
        if isinstance(handler, logging.FileHandler):
            assert handler.baseFilename == str(isolated_files / "debug.log")


def test_google_default_client_path_is_resolved_at_call_time(isolated_files, monkeypatch):
    factory = Mock()
    monkeypatch.setattr(google_auth.InstalledAppFlow, "from_client_secrets_file", factory)
    with pytest.raises(FileNotFoundError) as caught:
        google_auth.get_google_credentials([], "no-test-token")
    assert str(isolated_files / "google_oauth_client.json") in str(caught.value)
    factory.assert_not_called()


def test_google_explicit_client_path_is_still_supported(tmp_path, monkeypatch):
    path = tmp_path / "explicit-client.json"
    path.write_text("{}", encoding="utf-8")
    flow = Mock()
    flow.run_local_server.return_value.to_json.return_value = '{"token":"synthetic"}'
    factory = Mock(return_value=flow)
    monkeypatch.setattr(google_auth.InstalledAppFlow, "from_client_secrets_file", factory)

    google_auth.get_google_credentials([], "explicit", client_secret_path=path)

    factory.assert_called_once_with(str(path), [])
    flow.run_local_server.assert_called_once_with(port=0)


@pytest.mark.parametrize("host", ["198.51.100.1", "127.0.0.1"])
@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_tcp_and_loopback_connections_are_blocked(host, method):
    with socket.socket() as sock:
        with pytest.raises(pytest.fail.Exception, match="Network access is disabled"):
            getattr(sock, method)((host, 443))


def test_dns_is_blocked_before_resolution():
    with pytest.raises(pytest.fail.Exception, match="Network access is disabled"):
        socket.getaddrinfo("example.invalid", 443)


def test_udp_is_blocked_before_send():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with pytest.raises(pytest.fail.Exception, match="Network access is disabled"):
            sock.sendto(b"synthetic", ("198.51.100.1", 53))


@pytest.mark.parametrize("transport", [requests, httpx])
def test_real_http_clients_fail_without_network_access(transport):
    with pytest.raises(pytest.fail.Exception, match="Network access is disabled"):
        transport.get("https://example.invalid/", timeout=1)


def test_socketpair_works_without_allowing_arbitrary_loopback():
    left, right = socket.socketpair()
    with left, right:
        left.settimeout(1)
        right.settimeout(1)
        left.sendall(b"ok")
        assert right.recv(2) == b"ok"
    with socket.socket() as sock:
        with pytest.raises(pytest.fail.Exception, match="Network access is disabled"):
            sock.connect(("127.0.0.1", 8000))


def test_asyncio_still_runs_with_network_blocked():
    async def task():
        await asyncio.sleep(0)
        return "ok"
    assert asyncio.run(task()) == "ok"


def test_native_model_startup_requires_a_fake():
    from src.providers.foundry_local import _get_manager
    with pytest.raises(pytest.fail.Exception, match="Native model startup is disabled"):
        _get_manager("test-isolation")


def test_every_module_copying_a_path_constant_is_patched():
    """Guard against silent drift: a module added later that does
    `from ... import LOG_PATH` keeps a copy of the real path, and patching only
    the defining module would let that copy write outside the test directory."""
    src_root = Path(__file__).resolve().parents[1] / "src"

    assert find_copied_path_constants(src_root) == COPIED_PATH_CONSTANTS, (
        "COPIED_PATH_CONSTANTS in tests/_isolation.py no longer matches the source. "
        "Add the new module/constant so isolated_files patches its copy, or drop "
        "the stale entry."
    )


def test_the_patch_declaration_is_actually_applied(isolated_files, temp_db):
    """The declaration above is only useful if the fixture honours it."""
    for module_name, names in COPIED_PATH_CONSTANTS.items():
        module = importlib.import_module(module_name)
        for name in names:
            patched = getattr(module, name)
            assert patched.is_relative_to(isolated_files.parent), (
                f"{module_name}.{name} still points outside the test directory: {patched}"
            )
