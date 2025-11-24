socketless-http
===============

Transparent HTTP testing over IPC for sandboxed AI/editor environments. Run your FastAPI/ASGI (and wrapped Flask/Django WSGI) tests without opening sockets or resolving localhost/testserver—keep your test code the same while the transport swaps underneath. **Keep writing with the same httpx/TestClient ergonomics while dropping sockets entirely.**

## 🎯 Why?
- AI editors / sandboxes often block sockets and DNS, so TestClient/httpx fail or hang.
- socketless-http swaps HTTP transport to stdio IPC, keeping your app/test code mostly unchanged. You keep the ergonomics of httpx/TestClient and gain reliable, socket-free execution.

## 🚀 Quickstart
```bash
uv add fastapi httpx
uv add --dev socketless-http pytest pytest-asyncio
```

In your tests (e.g., `conftest.py`):
```python
from socketless_http import switch_to_ipc_connection, reset_ipc_state

# start IPC once per session
_cleanup = switch_to_ipc_connection(
    "tests.sample_app:app",             # ASGI app import path
    reset_hook="tests.sample_app:reset_state",  # optional per-test reset callable
    base_url="http://testserver",
)

def teardown_module():
    _cleanup()

# per-test reset (or use pytest fixtures from socketless_http.pytest_plugin)
def setup_function(_):
    reset_ipc_state()
```

Use httpx or FastAPI TestClient as usual; requests go over IPC, not sockets:
```python
import httpx
from fastapi.testclient import TestClient

def test_ping_with_httpx():
    res = httpx.Client().get("/ping")
    assert res.json() == {"status": "ok"}

def test_ping_with_testclient(app):
    client = TestClient(app)
    assert client.get("/ping").json() == {"status": "ok"}
```

## 📘 Tutorials

### FastAPI (ASGI)
What you need: ASGI app import path, optional reset function, and a place to toggle IPC (e.g., conftest.py). After switching, keep using httpx/TestClient as usual.
Server (`myapp/main.py`):
```python
from fastapi import FastAPI
app = FastAPI()
@app.get("/hello")
async def hello(): return {"message": "fastapi"}
def reset_state(): pass
```
Client/test (FastAPI TestClient uses the same `app` defined above):
```python
from socketless_http import switch_to_ipc_connection, reset_ipc_state
_cleanup = switch_to_ipc_connection("myapp.main:app", reset_hook="myapp.main:reset_state")
def teardown_module(): _cleanup()
def setup_function(_): reset_ipc_state()
def test_hello():
    import httpx
    assert httpx.Client().get("/hello").json() == {"message": "fastapi"}
def test_hello_with_testclient(app=app):  # type: ignore[name-defined]
    from fastapi.testclient import TestClient
    client = TestClient(app)
    assert client.get("/hello").json() == {"message": "fastapi"}
```
```python
# conftest.py
from socketless_http import switch_to_ipc_connection, reset_ipc_state

_cleanup = switch_to_ipc_connection(
    "myapp.main:app",
    reset_hook="myapp.main:reset_state",  # optional per-test cleanup
)

def teardown_module():
    _cleanup()

def setup_function(_):
    reset_ipc_state()
```

### Flask (WSGI)
What you need: WSGI app import path, optional reset, and `app_kind="wsgi"` so it is wrapped via WsgiToAsgi before ASGITransport.
Server (`myapp/wsgi.py`):
```python
from flask import Flask, jsonify
app = Flask(__name__)
@app.get("/hello")
def hello(): return jsonify(message="flask")
def reset_state(): pass
```
Client/test:
```python
from socketless_http import switch_to_ipc_connection, reset_ipc_state
_cleanup = switch_to_ipc_connection("myapp.wsgi:app", reset_hook="myapp.wsgi:reset_state", app_kind="wsgi")
def teardown_module(): _cleanup()
def setup_function(_): reset_ipc_state()
def test_hello():
    import httpx
    assert httpx.Client().get("/hello").json() == {"message": "flask"}
```
```python
from socketless_http import switch_to_ipc_connection, reset_ipc_state

_cleanup = switch_to_ipc_connection(
    "myapp.wsgi:app",
    reset_hook="myapp.wsgi:reset_state",
    app_kind="wsgi",
)

def teardown_module():
    _cleanup()

def setup_function(_):
    reset_ipc_state()
```

### Django (ASGI recommended)
What you need: set `DJANGO_SETTINGS_MODULE`, pass the ASGI app (`myproject.asgi:application`), and provide a DB reset hook if needed. Continue to use httpx/TestClient as normal.
Server (`myproject/asgi.py` + `urls.py`):
```python
# asgi.py
import os
from django.core.asgi import get_asgi_application
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
application = get_asgi_application()

# urls.py
from django.http import JsonResponse
from django.urls import path
urlpatterns = [path("hello/", lambda request: JsonResponse({"message": "django"}))]

def reset_db(): pass  # e.g., flush test DB
```
Client/test:
```python
import os
from socketless_http import switch_to_ipc_connection, reset_ipc_state
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
_cleanup = switch_to_ipc_connection("myproject.asgi:application", reset_hook="myproject.asgi:reset_db")
def teardown_module(): _cleanup()
def setup_function(_): reset_ipc_state()
def test_hello():
    import httpx
    assert httpx.Client().get("/hello/").json() == {"message": "django"}
```
If you must run Django in WSGI mode, set `app_kind="wsgi"` and pass `myproject.wsgi:application`, but ASGI is preferred.

## pytest helpers
```python
# conftest.py
from socketless_http.pytest_plugin import ipc_connection_fixture, reset_between_tests_fixture

ipc_connection = ipc_connection_fixture(
    "tests.sample_app:app",
    reset_hook="tests.sample_app:reset_state",
)
reset_between_tests = reset_between_tests_fixture()

## 🔍 Enable debug logging
Pass `switch_to_ipc_connection(..., debug=True)` when you need to trace what the worker is doing. It prints to stderr: worker startup/handshake status, method/URL/headers count sent from the parent, what the worker received and the returned status/body length, reset_hook calls, and restart attempts with stderr from the worker.
```

## What’s supported (MVP)
- Methods: GET/POST/PUT/PATCH/DELETE/OPTIONS/HEAD
- Bodies: bytes/text/JSON up to 5MB (no streaming yet)
- Headers/cookies round-trip; base_url override; follow_redirects client-side
- Reset hook per test; session-scoped worker reuse
- Worker auto-restart once if it dies; stderr is buffered and surfaced on errors
- WSGI apps supported via auto-detect or `app_kind="wsgi"` (WsgiToAsgi wrapping)

## Not yet
- WebSocket, SSE, HTTP/2, streaming/chunked bodies
- Parallel IPC requests (currently serialized)
- TLS options (`verify`/`cert`) ignored/unsupported

## Known constraints
- Transport is stdio IPC; no raw sockets are opened by clients or worker.
- Responses are buffered (no streaming); 5MB body limit per request/response.
- One worker process is reused; only one auto-restart attempt is made if it dies.
- TLS and HTTP/2 semantics are out of scope; keep to HTTP/1.1-style requests.
- FastAPI apps run in-process via ASGITransport: define routes as `async def` and offload blocking work with `anyio.to_thread.run_sync` to avoid known hangs in some FastAPI/Starlette/httpx/anyio versions when using sync (`def`) endpoints.

See `docs/spec.md` for full design notes. README_ja.md provides the same info in Japanese.
