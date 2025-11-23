from __future__ import annotations

import httpx
import pytest

from socketless_http.ipc import IpcProcess
from socketless_http.transport import IpcTransport


@pytest.fixture
def ipc_process():
    proc = IpcProcess(
        "tests.wsgi_app:app",
        reset_hook_path="tests.wsgi_app:reset_state",
        app_kind="wsgi",
    )
    try:
        yield proc
    finally:
        proc.close()


@pytest.fixture(autouse=True)
def _reset_each_test(ipc_process):
    ipc_process.reset()
    yield
    ipc_process.reset()


def test_wsgi_ping(ipc_process):
    client = httpx.Client(transport=IpcTransport(ipc_process), base_url="http://testserver")
    res = client.get("/ping")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_wsgi_create_and_read(ipc_process):
    client = httpx.Client(transport=IpcTransport(ipc_process), base_url="http://testserver")
    create = client.post("/items/abc", json={"value": "flask"})
    assert create.status_code == 200
    read = client.get("/items/abc")
    assert read.status_code == 200
    assert read.json() == {"id": "abc", "value": "flask"}
