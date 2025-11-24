from __future__ import annotations

import asyncio

import httpx

from tests.concurrency_app import app, reset_state


async def _call_runs_via_asgi() -> dict:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=True),
        base_url="http://testserver",
    ) as client:
        resp = await asyncio.wait_for(client.post("/runs"), timeout=5)
        assert resp.status_code == 200
        return resp.json()


def test_runs_via_asgi_transport() -> None:
    reset_state()
    data = asyncio.run(_call_runs_via_asgi())
    assert data["status"] == "queued"
    assert data["heartbeats"] == 0
