from __future__ import annotations

import socket
from contextlib import contextmanager

import httpx
import pytest

from socketless_http.ipc import IpcProcess
from socketless_http.transport import IpcTransport


@contextmanager
def no_socket_allowed():
    real_socket = socket.socket

    def _blocked(*args, **kwargs):
        raise RuntimeError("socket usage is blocked in tests")

    socket.socket = _blocked  # type: ignore
    try:
        yield
    finally:
        socket.socket = real_socket


@pytest.fixture
def ipc_process():
    proc = IpcProcess("tests.sample_app:app")
    try:
        yield proc
    finally:
        proc.close()


def test_ipc_ping(ipc_process):
    with no_socket_allowed():
        client = httpx.Client(transport=IpcTransport(ipc_process), base_url="http://testserver")
        res = client.get("/ping")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_ipc_create_and_read(ipc_process):
    client = httpx.Client(transport=IpcTransport(ipc_process), base_url="http://testserver")
    create = client.post("/items/item1", json={"value": "hello"})
    assert create.status_code == 200

    read = client.get("/items/item1")
    assert read.status_code == 200
    assert read.json() == {"id": "item1", "value": "hello"}


def test_invalid_import_fails_handshake():
    with pytest.raises(RuntimeError):
        with IpcProcess("tests.sample_app:missing"):  # type: ignore[arg-type]
            pass
