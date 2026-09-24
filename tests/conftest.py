"""Suite-wide offline guarantee (fixes A-04).

Every test runs with:
1. GROQ_API_KEY (and every other setting) removed from the environment, so the
   host cannot leak configuration or a real key into a test.
2. A socket guard that refuses DNS lookups and connections to any
   non-loopback address. Loopback and Unix sockets stay allowed.

Attempts are recorded as well as refused, and the test fails at teardown if
any were made. Raising alone is not enough: app/main.py catches every
exception and turns it into `internal_error`, so a swallowed network attempt
would otherwise pass silently. That is exactly how A-04 went unnoticed.
"""

import socket

import pytest

from app.config import load_settings
from tests.fakes import NetworkBlockedError

# Every variable app/config.py reads. Removed for each test so the host
# environment (Colab sets GROQ_MODEL, a dev shell may set DB_PATH) cannot leak in.
SETTINGS_ENV_VARS = (
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "DB_PATH",
    "MAX_ROWS",
    "QUERY_TIMEOUT_S",
    "MAX_HISTORY_TURNS",
)

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "", None}


def _is_loopback(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode()
    return host in _LOOPBACK_HOSTS or (isinstance(host, str) and host.startswith("127."))


@pytest.fixture(autouse=True)
def offline_guard(monkeypatch):
    """Yields the list of blocked attempts; a test that expects one clears it."""
    for name in SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    attempts: list[str] = []

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def check(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address[0]):
            attempts.append(f"connect {address!r}")
            raise NetworkBlockedError(f"network access blocked in tests: {address!r}")

    def guarded_connect(self, address):
        check(self, address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        check(self, address)
        return real_connect_ex(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_loopback(host):
            attempts.append(f"dns {host!r}")
            raise NetworkBlockedError(f"DNS lookup blocked in tests: {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)

    yield attempts

    assert not attempts, f"test attempted network access: {attempts}"


@pytest.fixture
def test_settings():
    """Valid settings for tests. The key is a dummy; the network guard ensures
    it can never be sent anywhere. `.env` is ignored."""
    return load_settings(
        env_file=None, groq_api_key="test-key-not-real", groq_model="fake/test-model"
    )
