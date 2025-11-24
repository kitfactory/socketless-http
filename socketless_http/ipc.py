from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple
from collections import deque

import httpx


DEFAULT_STARTUP_TIMEOUT = 5.0
MAX_BODY_BYTES = 5 * 1024 * 1024


def _b64encode(data: bytes | None) -> str | None:
    if data is None:
        return None
    return base64.b64encode(data).decode("ascii")


def _b64decode(data: str | None) -> bytes | None:
    if data is None:
        return None
    return base64.b64decode(data.encode("ascii"))


def _headers_to_list(headers: httpx.Headers | Iterable[Tuple[str, str]]) -> list[list[str]]:
    result: list[list[str]] = []
    for k, v in headers:
        key = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
        val = v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
        result.append([key.lower(), val])
    return result


@dataclass
class IpcResponse:
    status: int
    headers: List[Tuple[str, str]]
    body: bytes
    error: Optional[dict]


class IpcProcess:
    """Manage a socketless worker subprocess and synchronous IPC messaging."""

    def __init__(
        self,
        app_import: str,
        *,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        reset_hook_path: str | None = None,
        app_kind: str = "auto",
        debug: bool = False,
    ):
        self.app_import = app_import
        self.startup_timeout = startup_timeout
        self.reset_hook_path = reset_hook_path
        self.app_kind = app_kind
        self.debug = debug
        self._stderr_buffer: deque[str] = deque(maxlen=50)
        self._stderr_stop = threading.Event()
        self._stderr_thread: threading.Thread | None = None
        self._restart_attempted = False
        self._proc = self._start_process()
        self._start_stderr_reader()
        self._stdout_lock = threading.Lock()
        self._stdin_lock = threading.Lock()
        self._handshake()

    def _log(self, message: str) -> None:
        if not self.debug:
            return
        sys.stderr.write(f"[socketless-http] {message}\n")
        sys.stderr.flush()

    def _start_process(self) -> subprocess.Popen:
        cmd = [sys.executable, "-m", "socketless_http.worker", "--app", self.app_import]
        if self.reset_hook_path:
            cmd.extend(["--reset-hook", self.reset_hook_path])
        if self.app_kind:
            cmd.extend(["--app-kind", self.app_kind])
        if self.debug:
            cmd.append("--debug")
            self._log(f"starting worker: cmd={' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return proc

    def _start_stderr_reader(self) -> None:
        if self._proc.stderr is None:
            return

        def _drain():
            assert self._proc.stderr is not None
            for line in self._proc.stderr:
                if self._stderr_stop.is_set():
                    break
                self._stderr_buffer.append(line.rstrip("\n"))

        self._stderr_stop.clear()
        self._stderr_thread = threading.Thread(target=_drain, name="ipc-stderr-drain", daemon=True)
        self._stderr_thread.start()

    def _stop_stderr_reader(self) -> None:
        self._stderr_stop.set()
        if self._stderr_thread and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=0.2)

    def _stderr_tail(self) -> str:
        return "\n".join(self._stderr_buffer)

    def _handshake(self) -> None:
        start = time.monotonic()
        line: str | None = None
        while True:
            if self._proc.stdout is None:
                raise RuntimeError("worker stdout not available")
            if time.monotonic() - start > self.startup_timeout:
                self._log("handshake timed out")
                raise TimeoutError("worker handshake timed out")
            line = self._proc.stdout.readline()
            if line:
                break
            if self._proc.poll() is not None:
                self._log("worker exited during handshake")
                raise RuntimeError(f"worker exited during handshake. stderr:\n{self._stderr_tail()}")
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid handshake: {line!r}") from exc
        if not isinstance(msg, dict) or msg.get("type") != "handshake" or msg.get("status") != "ok":
            self._log(f"handshake failed with payload: {msg!r}")
            raise RuntimeError(f"handshake failed: {msg!r}\nstderr:\n{self._stderr_tail()}")
        self._log(f"handshake ok (pid={self._proc.pid}, elapsed={time.monotonic() - start:.3f}s)")

    def close(self) -> None:
        self._log("closing worker")
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._stop_stderr_reader()

    def __enter__(self) -> "IpcProcess":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.close()

    def send(self, message: dict) -> IpcResponse:
        data = self._send_raw(message)
        if data.get("error"):
            err = data["error"]
        else:
            err = None
        body_b64 = data.get("body")
        body = _b64decode(body_b64) or b""
        if len(body) > MAX_BODY_BYTES:
            raise RuntimeError("response body exceeded max limit")
        headers = [(k, v) for k, v in data.get("headers", [])]
        return IpcResponse(status=data.get("status", 0), headers=headers, body=body, error=err)

    def _send_raw(self, message: dict) -> dict:
        msg_debug = ""
        if self.debug:
            m = message.get("method")
            url = message.get("url")
            msg_debug = f"{m} {url}" if m and url else "control"
            headers = message.get("headers") or []
            body_b64 = message.get("body")
            body_len = len(base64.b64decode(body_b64)) if body_b64 else 0
            self._log(f"-> send {msg_debug} headers={len(headers)} body_len={body_len}")
        payload = json.dumps(message, separators=(",", ":"))
        if self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError("worker pipes unavailable")
        if self._proc.poll() is not None:
            self._ensure_running(reason="process exited before send")

        with self._stdin_lock:
            self._proc.stdin.write(payload + "\n")
            self._proc.stdin.flush()

        with self._stdout_lock:
            line = self._proc.stdout.readline()
            if not line and self._proc.poll() is not None:
                self._ensure_running(reason="process exited during read")
                line = self._proc.stdout.readline()
                if not line:
                    raise RuntimeError("worker process exited unexpectedly")

        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid response: {line!r}") from exc
        if self.debug:
            status = data.get("status")
            err = data.get("error")
            self._log(f"<- recv {msg_debug} status={status} error={err}")
        return data

    def reset(self) -> None:
        """Send reset request to worker if supported."""
        self._log("sending reset request")
        data = self._send_raw({"type": "reset"})
        if data.get("type") != "reset" or data.get("status") not in {"ok", "noop"}:
            raise RuntimeError(f"reset failed: {data}")
        self._log(f"reset result: {data.get('status')}")

    def _ensure_running(self, *, reason: str | None = None) -> None:
        if self._proc.poll() is None:
            return
        tail = self._stderr_tail()
        self._log(f"worker exited (reason={reason or 'unknown'}, code={self._proc.returncode}); stderr tail:\n{tail}")
        if not self._restart_attempted:
            self._restart_attempted = True
            self._stop_stderr_reader()
            self._proc = self._start_process()
            self._start_stderr_reader()
            self._handshake()
            self._log("worker restarted successfully")
            return
        raise RuntimeError(f"worker process not running; stderr:\n{tail}")


class IpcTransport(httpx.BaseTransport):
    """httpx sync transport that routes requests over IPC to a worker."""

    def __init__(self, process: IpcProcess):
        self.process = process
        self._lock = threading.Lock()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        if isinstance(body, str):
            body = body.encode()
        if body and len(body) > MAX_BODY_BYTES:
            raise httpx.RequestError("request body too large", request=request)

        message = {
            "id": "sync",
            "method": request.method,
            "url": str(request.url),
            "headers": _headers_to_list(request.headers.raw),
            "cookies": [],
            "body": _b64encode(body) if body else None,
        }
        with self._lock:
            resp = self.process.send(message)
        if resp.error:
            raise httpx.TransportError(f"IPC error: {resp.error}")
        return httpx.Response(
            status_code=resp.status,
            headers=resp.headers,
            content=resp.body,
            request=request,
        )


class IpcAsyncTransport(httpx.AsyncBaseTransport):
    """httpx async transport that routes requests over IPC to a worker."""

    def __init__(self, process: IpcProcess):
        self.process = process
        self._lock = threading.Lock()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        if isinstance(body, str):
            body = body.encode()
        if body and len(body) > MAX_BODY_BYTES:
            raise httpx.RequestError("request body too large", request=request)

        message = {
            "id": "async",
            "method": request.method,
            "url": str(request.url),
            "headers": _headers_to_list(request.headers.raw),
            "cookies": [],
            "body": _b64encode(body) if body else None,
        }
        # Use thread lock to serialize IPC access even from async.
        with self._lock:
            resp = self.process.send(message)
        if resp.error:
            raise httpx.TransportError(f"IPC error: {resp.error}")
        return httpx.Response(
            status_code=resp.status,
            headers=resp.headers,
            content=resp.body,
            request=request,
        )
