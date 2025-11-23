from __future__ import annotations

import pytest

from .switch import reset_ipc_state, switch_to_ipc_connection


def ipc_connection_fixture(app_import: str, *, reset_hook: str | None = None, base_url: str = "http://testserver"):
    """Factory returning a session-scoped fixture that enables IPC connection."""

    @pytest.fixture(scope="session")
    def _ipc_connection():
        cleanup = switch_to_ipc_connection(app_import, reset_hook=reset_hook, base_url=base_url)
        try:
            yield
        finally:
            cleanup()

    return _ipc_connection


def reset_between_tests_fixture():
    """Factory returning an autouse fixture to reset state before/after each test."""

    @pytest.fixture(autouse=True)
    def _reset():
        reset_ipc_state()
        yield
        reset_ipc_state()

    return _reset
