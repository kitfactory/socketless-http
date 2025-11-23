from __future__ import annotations

import httpx
import pytest

from socketless_http.ipc import IpcProcess
from socketless_http.transport import IpcTransport


@pytest.fixture
def ipc_process():
    proc = IpcProcess("tests.sample_app:app")
    try:
        yield proc
    finally:
        proc.close()


def test_auto_restart_once(ipc_process):
    client = httpx.Client(transport=IpcTransport(ipc_process), base_url="http://testserver")
    assert client.get("/ping").status_code == 200

    # Kill the worker to trigger restart
    ipc_process._proc.kill()  # type: ignore[attr-defined]
    ipc_process._proc.wait()

    # First request after death should auto-restart and succeed
    res = client.get("/ping")
    assert res.status_code == 200

    # Kill again; restart should not happen twice
    ipc_process._proc.kill()  # type: ignore[attr-defined]
    ipc_process._proc.wait()

    with pytest.raises(RuntimeError):
        client.get("/ping")
