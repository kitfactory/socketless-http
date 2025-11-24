from __future__ import annotations

import argparse
import asyncio
import base64
import faulthandler
import importlib
import json
import signal
import sys
import traceback
import time
from typing import Any, Callable, Optional
import inspect
import anyio

import httpx
from httpx import ASGITransport
from asgiref.wsgi import WsgiToAsgi

MAX_BODY_BYTES = 5 * 1024 * 1024


def _import_from_string(path: str) -> Any:
    module_path, _, attr = path.partition(":")
    if not module_path or not attr:
        raise ValueError(f"invalid app import path: {path}")
    module = importlib.import_module(module_path)
    target: Any = module
    for part in attr.split("."):
        target = getattr(target, part)
    return target


def _is_asgi_app(app: Any) -> bool:
    """Rudimentary check for ASGI callable (scope, receive, send)."""
    if not callable(app):
        return False
    try:
        sig = inspect.signature(app)
    except (TypeError, ValueError):
        return False
    params = list(sig.parameters.values())
    return len(params) >= 3


async def _build_client(app, *, app_kind: str) -> tuple[httpx.AsyncClient | httpx.Client, bool]:
    if app_kind == "wsgi":
        transport = httpx.WSGITransport(app=app)
        return httpx.Client(transport=transport, base_url="http://testserver"), True
    transport = ASGITransport(app=app, raise_app_exceptions=True)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver"), False


async def _handle_message(
    client: httpx.AsyncClient | httpx.Client,
    message: dict,
    log: Callable[[str], None],
    *,
    client_is_sync: bool = False,
) -> dict:
    method = message["method"]
    url = message["url"]
    headers = message.get("headers") or []
    body_b64 = message.get("body")
    body = base64.b64decode(body_b64.encode()) if body_b64 else None
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    start = time.monotonic()

    def _watchdog():
        if task and not task.done():
            log("watchdog: request still running; dumping stack")
            task.print_stack(file=sys.stderr)
            for other in asyncio.all_tasks(loop):
                if other is task:
                    continue
                print(f"[socketless-http worker] other task: {other}", file=sys.stderr, flush=True)
                other.print_stack(file=sys.stderr)

    watchdog_handle = loop.call_later(2.0, _watchdog)
    if body and len(body) > MAX_BODY_BYTES:
        log(f"request rejected (body too large): {method} {url}")
        return {
            "id": message.get("id"),
            "status": 413,
            "headers": [],
            "body": None,
            "error": {"type": "body_too_large", "message": "request body too large"},
        }
    log(f"request: {method} {url} headers={len(headers)} body_len={len(body) if body else 0}")

    try:
        log(f"before client.request t+{time.monotonic() - start:.3f}s")
        if client_is_sync:
            response = await anyio.to_thread.run_sync(
                lambda: client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    content=body,
                ),
                cancellable=True,
            )
        else:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                content=body,
            )
        log(f"after client.request t+{time.monotonic() - start:.3f}s")
        log(
            f"response received: status={response.status_code} headers={len(response.headers)} "
            f"t+{time.monotonic() - start:.3f}s"
        )
        if client_is_sync:
            resp_body = response.content
        else:
            resp_body = await response.aread()
        log(f"response body read: len={len(resp_body)} t+{time.monotonic() - start:.3f}s")
        if len(resp_body) > MAX_BODY_BYTES:
            log(f"response too large: {method} {url} len={len(resp_body)}")
            return {
                "id": message.get("id"),
                "status": 599,
                "headers": [],
                "body": None,
                "error": {"type": "response_too_large", "message": "response body too large"},
            }
        body_out = base64.b64encode(resp_body).decode() if resp_body else None
        header_list = []
        for k, v in response.headers.raw:
            key = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
            val = v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
            header_list.append([key.lower(), val])
        return {
            "id": message.get("id"),
            "status": response.status_code,
            "headers": header_list,
            "body": body_out,
            "error": None,
        }
        log(f"response serialization done t+{time.monotonic() - start:.3f}s")
    except Exception as exc:  # pragma: no cover - surfaced in parent tests
        log(f"handler error: {exc.__class__.__name__}: {exc}")
        return {
            "id": message.get("id"),
            "status": 599,
            "headers": [],
            "body": None,
            "error": {"type": exc.__class__.__name__, "message": str(exc)},
        }
    finally:
        watchdog_handle.cancel()


async def _run(app_import: str, reset_hook: Optional[str], app_kind: str, debug: bool) -> None:
    # Allow on-demand stack dump via SIGUSR1 while debugging hangs.
    try:
        faulthandler.register(signal.SIGUSR1, chain=True)
    except Exception:
        # Non-fatal if signal not available (e.g., Windows).
        pass
    loop = asyncio.get_running_loop()

    def _dump_tasks(signum, frame):  # noqa: ANN001
        print(f"[socketless-http worker] SIGUSR2 received; dumping {len(asyncio.all_tasks(loop))} tasks", file=sys.stderr, flush=True)
        for task in asyncio.all_tasks(loop):
            print(f"[socketless-http worker] task: {task}", file=sys.stderr, flush=True)
            for frame in task.get_stack():
                traceback.print_stack(frame, file=sys.stderr)

    try:
        signal.signal(signal.SIGUSR2, _dump_tasks)
    except Exception:
        pass

    def _log(message: str) -> None:
        if debug:
            print(f"[socketless-http worker] {message}", file=sys.stderr, flush=True)

    _log(f"importing app {app_import} (kind={app_kind})")
    app = _import_from_string(app_import)
    if app_kind == "wsgi":
        client, client_is_sync = await _build_client(app, app_kind="wsgi")
    elif app_kind == "auto" and not _is_asgi_app(app):
        _log("auto-detected WSGI app; wrapping with WsgiToAsgi for ASGI compatibility")
        asgi_wrapped = WsgiToAsgi(app)  # type: ignore[assignment]
        client, client_is_sync = await _build_client(asgi_wrapped, app_kind="wsgi")
    else:
        client, client_is_sync = await _build_client(app, app_kind="asgi")
    reset_callable: Callable[[], None] | None = None
    if reset_hook:
        target = _import_from_string(reset_hook)
        if not callable(target):
            raise TypeError(f"reset_hook {reset_hook} is not callable")
        reset_callable = target  # type: ignore[assignment]
    # Handshake
    _log("handshake ready")
    print(json.dumps({"type": "handshake", "status": "ok"}), flush=True)

    async def _process_line(line: str) -> dict:
        message = json.loads(line)
        if message.get("type") == "reset":
            if reset_callable:
                _log("reset hook invoked")
                reset_callable()
                _log("reset hook completed")
                return {"type": "reset", "status": "ok"}
            _log("reset requested but no hook configured")
            return {"type": "reset", "status": "noop"}
        result = await _handle_message(client, message, _log, client_is_sync=client_is_sync)
        _log(f"response: status={result.get('status')} error={result.get('error')}")
        return result

    loop = asyncio.get_running_loop()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        result = await _process_line(line)
        sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, help="ASGI app import path, e.g., tests.sample_app:app")
    parser.add_argument("--reset-hook", required=False, help="Optional callable for reset, e.g., tests.sample_app:reset_state")
    parser.add_argument("--app-kind", choices=["auto", "asgi", "wsgi"], default="auto", help="Treat app as ASGI (default auto-detect) or force WSGI")
    parser.add_argument("--debug", action="store_true", help="Enable verbose worker logging to stderr")
    args = parser.parse_args(argv)
    asyncio.run(_run(args.app, reset_hook=args.reset_hook, app_kind=args.app_kind, debug=args.debug))


if __name__ == "__main__":
    main()
