from __future__ import annotations

import threading
import time

import httpx
import pytest

from socketless_http import reset_ipc_state, switch_to_ipc_connection


@pytest.fixture(scope="module", autouse=True)
def _ipc_session():
    cleanup = switch_to_ipc_connection(
        "tests.concurrency_app:app",
        reset_hook="tests.concurrency_app:reset_state",
        base_url="http://testserver",
    )
    try:
        yield
    finally:
        cleanup()


@pytest.fixture(autouse=True)
def _reset_between_tests():
    reset_ipc_state()
    yield
    reset_ipc_state()


def test_multithreaded_poll_and_heartbeat():
    # Create a run
    run_id = httpx.Client().post("/runs").json()["id"]

    status_result = {}

    def worker_thread():
        client = httpx.Client()
        # poll every 0.05s until job acquired
        for _ in range(20):
            res = client.get("/workers/poll").json()
            job = res.get("job")
            if job:
                client.post(f"/runs/{job['id']}/heartbeat")
                return
            time.sleep(0.05)

    def client_thread():
        client = httpx.Client()
        for _ in range(100):  # up to ~5s
            res = client.get(f"/runs/{run_id}").json()
            status_result["status"] = res["status"]
            if res["status"] in {"completed", "failed", "cancelled"}:
                return
            time.sleep(0.05)

    t_worker = threading.Thread(target=worker_thread)
    t_client = threading.Thread(target=client_thread)
    t_worker.start()
    t_client.start()
    t_worker.join(timeout=5)
    t_client.join(timeout=5)

    assert not t_worker.is_alive(), "worker thread hung"
    assert not t_client.is_alive(), "client thread hung"
    assert status_result.get("status") == "completed"
