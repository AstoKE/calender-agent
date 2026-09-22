"""Test-process guards; no production configuration is changed."""

import ast
from contextvars import ContextVar
from pathlib import Path
import socket

import pytest


CREDENTIAL_ENV_VARS = (
    "LLM_PROVIDER", "GOOGLE_API_KEY", "GEMINI_API_KEY", "MS_CLIENT_ID",
    "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "TOKEN_ENCRYPTION_KEY",
    "COOKIE_SECURE",
)

# Path constants the isolated_files fixture redirects into the test directory.
ISOLATED_PATH_CONSTANTS = (
    "DATA_DIR", "DEFAULT_CLIENT_SECRET_PATH", "DEFAULT_DB_PATH", "LOG_PATH",
)

# Modules that copy one of those constants into their own namespace with
# `from ... import CONST`. Such a copy is bound at import time, so patching the
# module that DEFINES the constant leaves the copy pointing at the real path.
# test_test_isolation.py compares this declaration against the source tree, so a
# newly added copy fails a test instead of silently escaping isolation.
COPIED_PATH_CONSTANTS = {
    "src.ui.oauth_routes": ("DEFAULT_CLIENT_SECRET_PATH",),
    "src.ui.routes": ("DEFAULT_DB_PATH", "LOG_PATH"),
}


def find_copied_path_constants(src_root: Path) -> dict[str, tuple[str, ...]]:
    """Return {module: bound names} for every by-value import of an isolated
    path constant found in the source tree."""
    found: dict[str, tuple[str, ...]] = {}
    for path in sorted(src_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = sorted(
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name in ISOLATED_PATH_CONSTANTS
        )
        if names:
            module = ".".join(path.relative_to(src_root.parent).with_suffix("").parts)
            found[module] = tuple(names)
    return found


def block_network(monkeypatch):
    """Block Python socket traffic, including DNS and loopback model servers.

    Windows implements socketpair with a private loopback connection. Permit
    only that synchronous stdlib operation so asyncio/TestClient can run. The
    allowance is context-local and never opens loopback access to other threads.
    """
    in_socketpair = ContextVar("test_socketpair", default=False)
    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair

    def denied(*args, **kwargs):
        pytest.fail("Network access is disabled in tests; mock the provider or transport.", pytrace=False)

    def connect(sock, address):
        if in_socketpair.get() and isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        denied()

    def socketpair(*args, **kwargs):
        token = in_socketpair.set(True)
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            in_socketpair.reset(token)

    monkeypatch.setattr(socket, "socketpair", socketpair)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket.socket, "sendto", denied)
    if hasattr(socket.socket, "sendmsg"):
        monkeypatch.setattr(socket.socket, "sendmsg", denied)
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo"):
        monkeypatch.setattr(socket, name, denied)


def block_native_model(*args, **kwargs):
    pytest.fail("Native model startup is disabled in tests; use a fake provider.", pytrace=False)
