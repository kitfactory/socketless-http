from __future__ import annotations

import itertools
import os
import threading
import sys

import anyio
from fastapi import FastAPI, HTTPException

app = FastAPI()

_debug = os.getenv("SOCKETLESS_CONCURRENCY_APP_DEBUG") == "1"
_runs_lock = threading.Lock()
_runs: dict[int, dict[str, object]] = {}
_run_id_gen = itertools.count(1)


def _log(msg: str) -> None:
    if _debug:
        print(f"[concurrency_app] {msg}", file=sys.stderr, flush=True)


def _create_run_sync() -> dict[str, object]:
    run_id = next(_run_id_gen)
    _log(f"create_run -> {run_id}")
    with _runs_lock:
        _runs[run_id] = {"id": run_id, "status": "queued", "heartbeats": 0}
    result = _runs[run_id]
    _log(f"create_run returning {result}")
    return result


def _poll_job_sync() -> dict[str, object]:
    with _runs_lock:
        queued = [r for r in _runs.values() if r["status"] == "queued"]
        if not queued:
            _log("poll_job -> no job")
            return {"job": None}
        job = queued[0]
        job["status"] = "running"
        _log(f"poll_job -> job {job['id']}")
        return {"job": {"id": job["id"]}}


def _heartbeat_sync(run_id: int) -> dict[str, object]:
    with _runs_lock:
        if run_id not in _runs:
            raise HTTPException(status_code=404, detail="run not found")
        run = _runs[run_id]
        run["heartbeats"] += 1
        run["status"] = "completed"
        _log(f"heartbeat -> {run_id}")
        return {"status": "ok"}


def _get_run_sync(run_id: int) -> dict[str, object]:
    with _runs_lock:
        run = _runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        _log(f"get_run -> {run_id} status={run['status']}")
        return dict(run)


def _cancel_run_sync(run_id: int) -> dict[str, object]:
    with _runs_lock:
        run = _runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        run["status"] = "cancelled"
        _log(f"cancel_run -> {run_id}")
        return {"status": "cancelled"}


@app.post("/runs")
async def create_run() -> dict[str, object]:
    return await anyio.to_thread.run_sync(_create_run_sync)


@app.get("/workers/poll")
async def poll_job() -> dict[str, object]:
    return await anyio.to_thread.run_sync(_poll_job_sync)


@app.post("/runs/{run_id}/heartbeat")
async def heartbeat(run_id: int) -> dict[str, object]:
    return await anyio.to_thread.run_sync(_heartbeat_sync, run_id)


@app.get("/runs/{run_id}")
async def get_run(run_id: int) -> dict[str, object]:
    return await anyio.to_thread.run_sync(_get_run_sync, run_id)


@app.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int) -> dict[str, object]:
    return await anyio.to_thread.run_sync(_cancel_run_sync, run_id)


def reset_state() -> None:
    with _runs_lock:
        _runs.clear()
    _log("reset_state")
