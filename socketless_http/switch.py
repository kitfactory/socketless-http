from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Optional

import httpx

from .ipc import IpcAsyncTransport, IpcProcess, IpcTransport

try:  # Optional fastapi/starlette
    import fastapi.testclient as fastapi_testclient
    import starlette.testclient as starlette_testclient
except Exception:  # pragma: no cover
    fastapi_testclient = None  # type: ignore
    starlette_testclient = None  # type: ignore


Originals = dict[str, object]


@dataclass
class _Session:
    process: IpcProcess
    reset_hook: Optional[Callable[[], None]]
    base_url: str
    originals: Originals


_active_session: _Session | None = None


def _import_callable(path: str) -> Callable[[], None]:
    module_path, _, attr = path.partition(":")
    if not module_path or not attr:
        raise ValueError(f"invalid callable import path: {path}")
    module = importlib.import_module(module_path)
    target = module
    for part in attr.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise TypeError(f"reset_hook {path} is not callable")
    return target  # type: ignore[return-value]


def reset_ipc_state() -> None:
    """Call reset_hook if configured (no-op if none)."""
    if _active_session:
        try:
            _active_session.process.reset()
        except Exception:
            # Reset failures should surface in tests; let them raise.
            raise
        if _active_session.reset_hook:
            _active_session.reset_hook()


def _monkeypatch(session: _Session) -> None:
    originals: Originals = {
        "httpx.Client": httpx.Client,
        "httpx.AsyncClient": httpx.AsyncClient,
    }

    class PatchedClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", IpcTransport(session.process))
            kwargs.setdefault("base_url", session.base_url)
            super().__init__(*args, **kwargs)

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", IpcAsyncTransport(session.process))
            kwargs.setdefault("base_url", session.base_url)
            super().__init__(*args, **kwargs)

    httpx.Client = PatchedClient  # type: ignore[assignment]
    httpx.AsyncClient = PatchedAsyncClient  # type: ignore[assignment]

    if fastapi_testclient and hasattr(fastapi_testclient, "TestClient"):
        originals["fastapi.TestClient"] = fastapi_testclient.TestClient

        class IpcTestClient:
            def __init__(self, app, base_url: str = session.base_url, **kwargs):  # noqa: D401
                self._client = httpx.Client(base_url=base_url)

            def __getattr__(self, name):
                return getattr(self._client, name)

            def __enter__(self):
                self._client.__enter__()
                return self

            def __exit__(self, exc_type, exc, tb):
                return self._client.__exit__(exc_type, exc, tb)

            def close(self) -> None:
                self._client.close()

        fastapi_testclient.TestClient = IpcTestClient  # type: ignore[assignment]

    if starlette_testclient and hasattr(starlette_testclient, "TestClient"):
        originals["starlette.TestClient"] = starlette_testclient.TestClient
        starlette_testclient.TestClient = fastapi_testclient.TestClient  # type: ignore[assignment]

    session.originals = originals


def _restore(session: _Session) -> None:
    for key, original in session.originals.items():
        if key == "httpx.Client":
            httpx.Client = original  # type: ignore[assignment]
        elif key == "httpx.AsyncClient":
            httpx.AsyncClient = original  # type: ignore[assignment]
        elif key == "fastapi.TestClient" and fastapi_testclient:
            fastapi_testclient.TestClient = original  # type: ignore[assignment]
        elif key == "starlette.TestClient" and starlette_testclient:
            starlette_testclient.TestClient = original  # type: ignore[assignment]


def switch_to_ipc_connection(
    app_import: str,
    *,
    reset_hook: str | Callable[[], None] | None = None,
    base_url: str = "http://testserver",
    startup_timeout: float = 5.0,
    app_kind: str = "auto",
) -> Callable[[], None]:
    """Switch httpx/TestClient to IPC transports. Returns cleanup function."""
    global _active_session
    if _active_session:
        raise RuntimeError("IPC session already active")

    reset_hook_path = reset_hook if isinstance(reset_hook, str) else None
    process = IpcProcess(
        app_import=app_import,
        startup_timeout=startup_timeout,
        reset_hook_path=reset_hook_path,
        app_kind=app_kind,
    )

    reset_callable = None
    if reset_hook and not isinstance(reset_hook, str):
        reset_callable = reset_hook

    session = _Session(process=process, reset_hook=reset_callable, base_url=base_url, originals={})
    _active_session = session
    try:
        _monkeypatch(session)
    except Exception:
        process.close()
        _active_session = None
        raise

    def cleanup() -> None:
        global _active_session
        if _active_session is None:
            return
        _restore(session)
        process.close()
        _active_session = None

    return cleanup
