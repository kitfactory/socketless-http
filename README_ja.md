socketless-http
===============

サンドボックス化された AI/エディタ環境でもソケットを開かずに HTTP テストを通すための IPC ベースのトランスポートです。FastAPI/ASGI に加え、Flask/Django など WSGI アプリも包んでほぼそのまま動かせます。慣れた httpx / TestClient の書き心地はそのまま、下層だけを IPC に置き換えます。**いつもの書き方のまま、ソケットを使わずテストできます。**

## 🎯 これが必要な理由
- AI エディタでは `localhost`/`testserver` の名前解決やソケットオープンが禁止され、TestClient/httpx が失敗しがちです。
- socketless-http は HTTP のトランスポートを stdio IPC に差し替え、アプリやテストコードの変更を最小限に抑えます。いつもの書き方のまま、ソケットを使わずテストできます。

## 🚀 クイックスタート
```bash
uv add fastapi httpx
uv add --dev socketless-http pytest pytest-asyncio
```

テスト（例: `conftest.py`）で IPC に切り替えます:
```python
from socketless_http import switch_to_ipc_connection, reset_ipc_state

_cleanup = switch_to_ipc_connection(
    "tests.sample_app:app",                   # ASGI アプリの import パス
    reset_hook="tests.sample_app:reset_state",  # オプション: テスト毎に状態リセット
    base_url="http://testserver",
)

def teardown_module():
    _cleanup()

def setup_function(_):
    reset_ipc_state()  # 各テスト前後でリセット（fixture の自動化も可能）
```

httpx や FastAPI TestClient は通常通り呼び出せます。リクエストは IPC 経由でアプリに届きます:
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

## 📘 チュートリアル

### FastAPI (ASGI)
必要なもの: ASGI アプリの import パス、任意のリセット関数、IPC を切り替える場所（例: conftest.py）です。切り替え後も httpx/TestClient の使い方は同じです。
サーバー（`myapp/main.py`）:
```python
from fastapi import FastAPI
app = FastAPI()
@app.get("/hello")
async def hello(): return {"message": "fastapi"}
def reset_state(): pass
```
クライアント/テスト（FastAPI TestClient は上で定義した `app` をそのまま使います）:
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
    reset_hook="myapp.main:reset_state",  # 任意の per-test 初期化
)

def teardown_module():
    _cleanup()

def setup_function(_):
    reset_ipc_state()
```

### Flask (WSGI)
必要なもの: WSGI アプリの import パス、任意のリセット関数、`app_kind="wsgi"` の指定です（ASGITransport へ渡す前に WsgiToAsgi で包みます）。
サーバー（`myapp/wsgi.py`）:
```python
from flask import Flask, jsonify
app = Flask(__name__)
@app.get("/hello")
def hello(): return jsonify(message="flask")
def reset_state(): pass
```
クライアント/テスト:
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

### Django (ASGI 推奨)
必要なもの: `DJANGO_SETTINGS_MODULE` の設定、ASGI アプリ（`myproject.asgi:application`）、必要なら DB リセット用フックです。切り替え後も httpx/TestClient をそのまま使えます。
サーバー（`myproject/asgi.py` + `urls.py`）:
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

def reset_db(): pass  # 例: テストDBリセット
```
クライアント/テスト:
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
どうしても WSGI を使う場合は `app_kind="wsgi"` を指定して `myproject.wsgi:application` を渡してください。基本的には ASGI を推奨します。

## pytest 用ヘルパー
```python
# conftest.py
from socketless_http.pytest_plugin import ipc_connection_fixture, reset_between_tests_fixture

ipc_connection = ipc_connection_fixture(
    "tests.sample_app:app",
    reset_hook="tests.sample_app:reset_state",
)
reset_between_tests = reset_between_tests_fixture()
```

## 現状の対応範囲（MVP）
- メソッド: GET/POST/PUT/PATCH/DELETE/OPTIONS/HEAD
- ボディ: bytes/text/JSON（5MB まで、ストリーミング未対応）
- ヘッダ/クッキー往復、base_url 上書き、クライアント側で follow_redirects
- テスト毎のリセットフック、セッション使い回しのワーカー
- ワーカーは死亡時に1回だけ自動リスタートし、stderr はエラー時にのみバッファを提示
- WSGI アプリも `app_kind="wsgi"`（または自動判定）で WsgiToAsgi 包装して利用可能

## 未対応（今後）
- WebSocket / SSE / HTTP/2 / ストリーミング・チャンクは未対応です。
- 並列 IPC リクエストは未対応で、現状シリアル処理です。
- TLS オプション (`verify`/`cert`) は無視または非対応です。

## 既知の制約
- 通信は stdio IPC のみで、クライアント・ワーカーとも生ソケットを開きません。
- レスポンスはバッファリング前提（ストリーミングなし）で、リクエスト/レスポンスとも 5MB 上限です。
- ワーカーは 1 プロセスを使い回し、死亡時の自動リスタートは 1 回だけ試行します。
- HTTP/2 や TLS オプションは対象外で、HTTP/1.1 相当のリクエストを想定します。

詳細設計は `docs/spec.md` を参照してください。英語版は README.md にあります。
