# socketless-http: 技術仕様（初版ドラフト）

この仕様は、AIエディタなどのネットワーク・ソケットが制限された環境でも HTTP テストを成立させるための **Python 向け開発者用パッケージ**を定義する。`docs/socketless-http-ai-editor-guide.md` に記された狙い（「HTTP を IPC に置き換える」）を実装レベルに落とし込み、今後の開発の足場とする。

## 目的と非目的
- 目的: TCP/UDP ソケットや DNS が禁止された環境で、既存の ASGI アプリのテストコードを **最小変更**で動かす IPC ベースの HTTP ブリッジを提供する。
- 非目的: 本番リクエストのプロキシ、WebSocket/Server-Sent Events/HTTP2 のサポート、長時間ストリーミング、アプリ実装への常駐コード注入。

## 想定環境と制約
- Python 3.10+ / ASGI アプリ（FastAPI/Starlette/純 ASGI を対象）
- sandbox では `localhost`/`testserver` の名前解決やポートオープンが禁止される前提
- IPC チャネルとして **stdin/stdout**（必要に応じて `multiprocessing.Pipe`）を利用し、外部ソケットは一切使用しない
- 依存は最小限（標準ライブラリ中心）。pytest などは dev 依存に限定。

## 提供 API（ドラフト）
- `switch_to_ipc_connection(app_import: str | ASGIApp, *, serializer="json", startup_timeout=5.0, env: dict | None = None) -> Callable[[], None]`
  - 指定のアプリをサブプロセスで起動し、`httpx.Client/AsyncClient` と `fastapi.testclient.TestClient` を IPC トランスポートに monkeypatch。戻り値でクリーンアップ関数を返す。
- `ipc_httpx_client()` / `ipc_async_client()`（context manager）
  - `httpx` 互換のクライアントを IPC 経由で提供。monkeypatch を使いたくない場合の明示的 API。
- pytest 連携: `conftest.py` で利用する `ipc_connection` fixture（module / session スコープ）を用意。

### API 互換性の扱い（推奨）
- `base_url`/`cookies`/`headers`/`params`/`json`/`data`/`files` は httpx と同等に受ける。
- `verify`/`cert`/`http2` は非対応（無視または即エラー）を明記。
- `timeout` は httpx の構造体を受け入れるが「IPC リクエストの総合タイムアウト」として扱う。
- `follow_redirects` はクライアント側でサポート（ループ上限 5）。
- `base_url` はデフォルト `http://testserver`、引数で上書き可。`switch_to_ipc_connection` の有無を 1 行で切り替えるだけで実環境用の base_url に戻せる設計を維持。
- `app_kind` で `auto`/`asgi`/`wsgi` を指定可能。`wsgi` の場合は WsgiToAsgi でラップしてから ASGITransport へ渡す。

## アーキテクチャ概要
- **IPC ブリッジサブプロセス**: `app_import` を `importlib` でロードし ASGI アプリをホスト。標準入力からの JSON リクエストを ASGI に渡し、レスポンスを JSON で標準出力へ返す。単一プロセス内で複数リクエストを直列処理（初期版）。
- **トランスポートレイヤ**: クライアント側で httpx の `Transport` を差し替え、HTTP リクエストを JSON メッセージに変換して IPC に送信。レスポンスを `httpx.Response` として再構築する。
- **互換インターフェース**: FastAPI の `TestClient` 生成時に同トランスポートを差し込むラッパーを提供し、既存テストの API 形を維持。
- **ハンドシェイク**: サブプロセス起動時にプロトコルバージョンとアプリ import 成否を通知。クライアント側は `startup_timeout` で待機。
- **エラーハンドリング**: サブプロセスが異常終了した場合は `RuntimeError` を上げ、stderr を含むデバッグ情報を返す。
- **ログ出力**: サブプロセス stderr は通常非表示とし、エラー発生時のみバッファをテスト出力へ添付（ノイズ削減）。

### プロセス管理・ライフサイクル（推奨）
- 起動時に ASGI lifespan を一度だけ実行し、テストセッション中はサブプロセスを使い回す（module/session fixture で管理）。
- `switch_to_ipc_connection` はクリーンアップ関数を返し、atexit でも呼ぶ二重ガードを入れる。
- サブプロセス死亡時は一度だけ自動リスタートを試み、失敗時に詳細付き例外で落とす。
- シャットダウン時は `shutdown` イベントを送り、残キューを破棄して確実に終了。
- クリーン状態維持: 再起動を避けつつクリーンを保つため、オプションで「状態リセットコマンド」を用意し、テスト毎にアプリ側のリセット関数（DB 初期化/メモリクリアなど）を呼べるようにする。再起動が必要な場合のみ fixture 側で明示的に再起動する。

### リセットフックの仕様（決定）
- `switch_to_ipc_connection(..., reset_hook: str | None = None)` で import パスを受け付け、各テスト開始前に一回呼ぶ。
- reset 失敗時はそのテストを失敗扱いとする（成功した場合は通常継続）。reset を呼べる状況であればテストを落とさない運用を前提。
- reset は「状態初期化のみ」でプロセス再起動は行わない。

## 対応範囲（MVP）
- メソッド: GET/POST/PUT/PATCH/DELETE/OPTIONS/HEAD
- ボディ: bytes / text / JSON
- ヘッダ・クッキー: httpx 相当の基本機能をサポート
- ステータスコード・レスポンスボディの完全往復
- 並列性: まずは直列処理（1リクエストずつ）。将来、ID 付きメッセージで並列化を検討。
- ASGI に加え、`app_kind="wsgi"` 指定で WsgiToAsgi でラップした WSGI アプリも扱える。

## 非対応（今後検討）
- WebSocket, SSE, HTTP/2, ストリーミングレスポンス
- Chunked エンコーディング
- Keep-alive を跨いだコネクション共有（IPC 上では都度独立処理）

## プロトコルスケッチ（JSON over stdio）
```json
// request message
{
  "id": "uuid4",
  "method": "GET",
  "url": "http://testserver/api/items?limit=10",
  "headers": [["content-type", "application/json"]],
  "cookies": [["session", "abc"]],
  "body": "base64..."  // bytes を base64 化。なしの場合は null
}

// response message
{
  "id": "uuid4",
  "status": 200,
  "headers": [["content-type", "application/json"]],
  "body": "base64...",
  "error": null
}
```
※ 実装では 1 行 1 メッセージの NDJSON を想定。

### プロトコル詳細（推奨決定事項）
- フレーミング: `\n` 区切り NDJSON。一行一メッセージ。本文に改行が入る場合でも JSON エンコード側で扱うため追加エスケープは不要。
- サイズ上限: MVP は 5MB/リクエストをハードリミット（環境変数で調整可能）。超過時は 413 とエラー返却。
- エラー表現: `error` は `{ "type": "...", "message": "...", "traceback": "...?" }`（traceback はデバッグ用文字列、省略可）。`status` は ASGI から得たものを優先し、IPC エラー時のみ 599 を予約。
- ヘッダ表現: `[[lower-name, value], ...]` に正規化。重複は順序を保持したまま複数要素で表現。クッキーはヘッダ側に統合して返却。
- ボディ表現: bytes は base64。テキスト/JSON はクライアント側で自動復元。大きなボディは現状全読み込みでストリーミング未対応。

## テスト方針
- CI / ローカルとも sandbox 前提で `httpx` や `TestClient` がソケットを開かないことを確認するユニットテストを用意。
- FastAPI の簡易アプリで CRUD エンドポイントを作り、従来 `TestClient` と IPC 版で同一レスポンスになるか比較。
- サブプロセスが落ちた場合のリトライ/エラー伝播のテスト。
- 依存ライブラリは `uv add pytest` などで dev にのみ追加。

### 優先度調整（AIエディタ前提）
- ソケット非依存の動作確認、httpx/TestClient 互換性、ASGI lifespan 実行確認を最優先。
- 並列・負荷・長大ボディなど統合/性能寄りの観点は後回し（MVP ではシリアル動作とサイズ上限のみ確認）。

## 開発スコープとマイルストン（案）
1. IPC トランスポートとサブプロセスブリッジの骨格実装（json/stdio、直列処理）
2. httpx/fastapi TestClient への差し替えラッパー + pytest fixture
3. エラーハンドリング・デバッグログ強化（stderr 抽出、タイムアウト）
4. 並列リクエスト対応、プロトコルバージョン管理、型ヒント/ドキュメント整備
