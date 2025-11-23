from __future__ import annotations

import pytest

from socketless_http import reset_ipc_state, switch_to_ipc_connection


@pytest.fixture(scope="session", autouse=True)
def _ipc_session():
    cleanup = switch_to_ipc_connection(
        "tests.sample_app:app",
        reset_hook="tests.sample_app:reset_state",
        base_url="http://testserver",
    )
    try:
        yield
    finally:
        cleanup()


@pytest.fixture(autouse=True)
def _reset_each_test():
    reset_ipc_state()
    yield
    reset_ipc_state()


def test_httpx_client_routed_over_ipc():
    import httpx

    client = httpx.Client()
    res = client.get("/ping")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_fastapi_testclient_routes_over_ipc():
    from fastapi.testclient import TestClient
    from tests.sample_app import app

    client = TestClient(app)
    res = client.post("/items/x", json={"value": "ipc"})
    assert res.status_code == 200
    assert res.json() == {"id": "x", "value": "ipc"}

    res2 = client.get("/items/x")
    assert res2.json() == {"id": "x", "value": "ipc"}


def test_reset_hook_clears_state_between_tests():
    import httpx

    client = httpx.Client()
    res = client.get("/items")
    assert res.json() == []
    client.post("/items/a", json={"value": "one"})
    res2 = client.get("/items")
    assert res2.json() == [{"id": "a", "value": "one"}]
