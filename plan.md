# socketless-http 開発プラン（逐次テスト実行ルール）

進め方: 各ステップで実装 → 直後に該当テストを実行しパスを確認。まとめて大量の実装やテストは行わず、小刻みに進める。

## ステップ1: 基盤セットアップ
- [x] `uv add httpx pytest pytest-asyncio fastapi` で依存追加（開発用。FastAPI は TestClient 互換確認のため）。
- [x] テスト用の最小 ASGI アプリ（FastAPI か Starlette、必要なら純 ASGI 版も）とユーティリティを用意。
- [x] 依存導入確認とサンプルアプリの smoke テスト（シンプルな ASGI 呼び出しが動くか）。

## ステップ2: IPC ブリッジ・トランスポート骨格
- [x] サブプロセスで ASGI アプリをロードし、stdio/NDJSON でリクエスト・レスポンスを中継する骨格を実装（直列処理、5MB 上限、エラーフォーマット）。
- [x] httpx のカスタム Transport を実装し、リクエスト→IPC→レスポンス再構築を行う。
- [x] 単体テストで IPC プロトコル往復が成立し、ソケットを開かないことを確認（monkeypatchでソケット禁止を検出）。

## ステップ3: httpx/TestClient 互換層 & リセットフック
- [x] `switch_to_ipc_connection` を実装し、TestClient/httpx を IPC トランスポートへ monkeypatch。`reset_hook` の呼び出しと base_url 切替をサポート。
- [x] pytest fixture (`ipc_connection`/reset) ファクトリを追加し、セッション使い回し＋テスト毎 reset 呼び出しを確認。
- [x] CRUD 相当のエンドポイントに対し、リセットが毎テスト前に呼ばれること、base_url 切替で挙動が変わらないことを検証（FastAPI/TestClient を用いた動作確認を含む）。

## ステップ4: ログ/エラーハンドリング磨き & ドキュメント
- [x] stderr バッファをエラー時のみ吐き出す処理を追加。自動リスタート1回までのポリシーを実装。
- [x] README/spec への使い方・制約更新。
- [x] サブプロセス強制終了時のリスタート挙動、エラー時に stderr が出力されることを確認。全テストスイート実行で確認。
- [x] 完了時に README.md と README_ja.md を作成・整備し、利用方法をまとめる。

## ルール
- [x] 各ステップの完了時に関連テストのみ実行してパスを確認する（まとめて走らせない）。
- [x] 未完了ステップに手を広げず、順に進める。
