# socketless-http — AIエディタ向け利用ガイド  
*Transparent HTTP Testing Over IPC for Restricted Sandboxed Environments*  

## 📌 この文書の目的  
このドキュメントは、**AIエディタ（Codex CLI / ClaudeCode / Cursor / Gemini CLI など）に対して、`socketless-http` の使命・意図・使い方を教示するための指示書**です。

AIモデルやエディタは、しばしば以下のような「構造的な制限」によって HTTP テストが実行できません。

- **testserver / localhost の名前解決が失敗する**
- **httpx / TestClient が sandbox のネットワーク境界で止められる**
- **モデルが外部通信禁止 → テスト自体が破綻する**
- **uvicorn 起動や socket.open が許可されないことがある**
- **FastAPI のインプロセステスト(TestClient)が内部的にネットワークセマンティクスに依存しており、AIエディタ環境で破綻しがち**

そのため、AIエディタは **HTTP クラサバの疎通テストをそのまま実行できない** という致命的な弱点を持っています。

`socketless-http` は、この弱点を *補完* するために作られています。

---

## 🧠 socketless-http が解決するAIエディタの“苦労点”

## 1. AIエディタは「HTTP テスト」を非常に苦手とする  
AIエディタの内部は一般に「外部インターネット遮断」「ローカルTCPソケット禁止」「名前解決制限」などが敷かれており、次が普通に壊れます：

- `TestClient(app)` が `http://testserver` を名前解決してしまい失敗  
- `httpx.AsyncClient().get("http://localhost:8000")` が禁止される  
- `uvicorn` 起動が許されずルーティングがテスト出来ない  
- sandbox の内部ネットワークモデルが不完全で `Temporary failure in name resolution` が多発  

つまり、**AIエディタ上では “普通の HTTP テスト” がほぼ不可能**。

その結果、  
「AI に HTTPテストを書かせたのに、AI自身が実行して確認できない」  
という **循環的失敗** が発生します。

---

## 2. TestClient/httpx は内部的に“ネットワーク的”であり、sandbox では破綻する  
FastAPI/TestClient の「in-process test」は本質的には HTTP ではなく ASGI 直接呼び出しですが…

- httpx が `testserver` をホスト名として扱うため DNS に触る  
- TestClient が内部イベントループでネットワークライクな名前空間を作る  
- sandbox がその「ネットワークライクな動き」すら禁止し壊す  

結果として：

> **TestClient は HTTP を使っていないのに、  
> AIエディタ環境では“ネットワーク呼び出しっぽく”見えて破綻する。**

`socketless-http` は、これを理解したうえで対処します。

---

## 3. 「HTTP に見える」構造は残しつつ、完全に IPC へ置き換える  
AIエディタの中でも以下は許されていることがほとんど：

- Python/Node の subprocess 実行  
- stdin/stdout のパイプ通信 (IPC)  
- JSON の入出力  
- IPC の上に Request/Response を積むこと  

AIエディタのsandboxは **TCP を閉じる** が、  
**IPC までは閉じるとツールが死んでしまうため、普通は許可されている**。

この「IPC は生きている」構造を利用して、

> **HTTP を TCP で流すのをやめて、IPC (stdin/stdout) 上で流す**  
>  
> ＝ “socketless HTTP”

という仕組みに変換するのが本パッケージの本質です。

---

# 🚀 socketless-http のコアコンセプト  

## ✔ FastAPI/Express アプリを一切変更しない  
アプリ側は普通の ASGI/Express アプリのままで OK。

## ✔ テスト時だけ、ASGI/HTTP クライアントを “IPC トランスポート” にすり替える  
以下を自動的にオーバーライド：

- `fastapi.testclient.TestClient`
- `httpx.Client` / `AsyncClient`
- （JS版では）`fetch` / `axios` など

つまり：

```
[通常]  TestClient → ASGI (in-process)
[AI環境]  TestClient → socketless-http → IPC → ASGI
```

**AIエディタでも HTTP テストが“動作する”ようになる。**

## ✔ API の見え方は変わらない  
- テストコードはこれまでの TestClient 形式のままで OK  
- アプリ側から見ても “普通の HTTP リクエスト” として認識される  
- AIエディタでもローカルでも CI でも同じテストが動く

---

# 🔧 AIエディタに伝えるべき設計思想  

AIエディタには、このパッケージが持つ「動機」と「構造」を理解させてください。

### 1. socketless-http は「AIエディタが HTTP を実行できない問題」を解決する  
AIエディタにこの文脈を教えないと、  
本来 socketless-http が必要な場面で普通の TestClient を生成したり、  
TCP ベースの通信コードを書き始めたりします（非常に多い失敗パターン）。

### 2. AIエディタは、HTTP テスト時に **TestClient / httpx を IPC 経由に切り替えるコードを書く必要がある**  
AIには、  
- monkeypatch  
- custom TestClient  
- custom httpx Transport  
- IPC 用サブプロセス  
- JSON-over-IPC  

などのテクニックの必要性を理解させてください。

### 3. AIエディタは、sandbox の “ネーム解決禁止 / ソケット禁止” を前提に動かなければならない  
AIがこの理解を持っていないと：

- testserver を直に使う  
- localhost の名前解決を試みる  
- uvicorn を起動しようとする  
- 外部APIを httpx で叩こうとする  

…など、意味のないコードを生成してしまいます。

---

# 🔧 AIエディタに対する明示的な指示

以下の指示を AIエディタに教えてください。

---

## 🔹 **AIエディタは「ソケット禁止」を前提にコード生成すること**  
- HTTP/TCP を試みてはいけない  
- 名前解決（DNS lookup）を伴うクライアントも使わない  
- TestClient を素のまま利用しない  
- 本番中の HTTP 実装は保持し、テスト時だけ切り替える  

---

## 🔹 **テスト環境では必ず `socketless-http.switch_to_ipc_connection()` を呼ぶ**  
Python:

```python
from socketless_http import switch_to_ipc_connection

def setup_module():
    switch_to_ipc_connection(app_import="myapp.main:app")
```

TypeScript (Express/Fastify):

```ts
import { switchToIpcConnection } from "socketless-http";

beforeAll(() => switchToIpcConnection("./src/server.ts"));
```

---

## 🔹 **AIエディタは “socketless-http が提供する TestClient/httpx を使う” ことを優先する**  
AIエディタは勝手に TestClient や httpx を書き換えず、  
**socketless-http が monkeypatch したものを使う**べきです。

---

## 🔹 **アプリケーションコードに socketless-http を混入させてはならない**  
- `import socketless_http` をアプリ側に書かない  
- 本番コードに影響させない  
- あくまでテスト補助として dev-only にとどめる

---

# 📘 推奨するプロジェクト構成（Python）

```
myapp/
  app/
    main.py   # FastAPI app
  tests/
    test_xxx.py
    conftest.py
  pyproject.toml
  ...
dev-deps:
  socketless-http
```

---

# 📘 推奨するプロジェクト構成（TypeScript）

```
myapp/
  src/
    server.ts     # Express/Fastify app
  tests/
    setup.ts      # switchToIpcConnection()
    api.test.ts
package.json
devDependencies:
  socketless-http
```

---

# 🧩 最後に — AIエディタへ伝えるべき重要ポイント

**socketless-http は「技術的には IPC HTTP Bridge」であり、  
名前のとおり“socket が使えない AI 環境で、HTTP テストを成立させるためのレイヤ”である。**

AIエディタが理解すべきなのは：

- なぜ普通の HTTP テストが動かないのか  
- TestClient がなぜ sandbox を突破できないのか  
- どうして IPC にすることで全て解決するのか  
- どこに monkeypatch を入れるべきか  
- 何をライブラリに任せるべきで、何をアプリに書かせてはいけないか  

これらを理解して初めて、AIエディタは socketless-http を正しく使えるようになります。
