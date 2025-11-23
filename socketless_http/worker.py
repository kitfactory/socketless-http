from __future__ import annotations

import argparse
import asyncio
import base64
import importlib
import json
import sys
from typing import Any, Awaitable, Callable, Optional
import inspect

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


async def _build_client(app) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=True)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _handle_message(client: httpx.AsyncClient, message: dict) -> dict:
    method = message["method"]
    url = message["url"]
    headers = message.get("headers") or []
    body_b64 = message.get("body")
    body = base64.b64decode(body_b64.encode()) if body_b64 else None
    if body and len(body) > MAX_BODY_BYTES:
        return {
            "id": message.get("id"),
            "status": 413,
            "headers": [],
            "body": None,
            "error": {"type": "body_too_large", "message": "request body too large"},
        }

    try:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            content=body,
        )
        resp_body = await response.aread()
        if len(resp_body) > MAX_BODY_BYTES:
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
    except Exception as exc:  # pragma: no cover - surfaced in parent tests
        return {
            "id": message.get("id"),
            "status": 599,
            "headers": [],
            "body": None,
            "error": {"type": exc.__class__.__name__, "message": str(exc)},
        }


async def _run(app_import: str, reset_hook: Optional[str], app_kind: str) -> None:
    app = _import_from_string(app_import)
    if app_kind == "wsgi" or (app_kind == "auto" and not _is_asgi_app(app)):
        app = WsgiToAsgi(app)  # type: ignore[assignment]
    client = await _build_client(app)
    reset_callable: Callable[[], None] | None = None
    if reset_hook:
        target = _import_from_string(reset_hook)
        if not callable(target):
            raise TypeError(f"reset_hook {reset_hook} is not callable")
        reset_callable = target  # type: ignore[assignment]
    # Handshake
    print(json.dumps({"type": "handshake", "status": "ok"}), flush=True)

    async def _process_line(line: str) -> dict:
        message = json.loads(line)
        if message.get("type") == "reset":
            if reset_callable:
                reset_callable()
                return {"type": "reset", "status": "ok"}
            return {"type": "reset", "status": "noop"}
        return await _handle_message(client, message)

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
    args = parser.parse_args(argv)
    asyncio.run(_run(args.app, reset_hook=args.reset_hook, app_kind=args.app_kind))


if __name__ == "__main__":
    main()
