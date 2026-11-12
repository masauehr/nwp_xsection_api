# DEPLOY.md — Render へのデプロイ手順

作成: 2026-11-12 ／ **未実施**（Docker ビルドと Render 上の動作は、デプロイして初めて確認できる）

## 0. 事前に決めること
- 公開する人に渡す**呼び名**（例: `tanaka`）。公開サイトの一覧にも載るので、本名でなくニックネームにする。

## 1. GitHub の fine-grained トークンを作る（公開用）
1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token。
2. Repository access: **Only select repositories** → `masauehr/nwp_chart_web` だけ。
3. Permissions: Repository permissions の **Contents: Read and write** だけ（他は付けない）。
4. 期限: 90日など。**期限をメモして、期限の14日前に作り直す**（Slack 通知は nwp_chart_web の仕組みを参考に必要なら追加）。
5. 表示されたトークンをコピーする（この画面でしか見えない）。**チャットやファイルに貼らない**。

## 2. 合言葉を作る
```bash
python tools/make_key.py tanaka
```
- 「合言葉」は相手に渡す（メールやチャットに直接書くより、別経路か使い捨ての方法が安全）。
- 「API_KEYS に足す項目」（`tanaka:<ハッシュ>`）を、複数人ならカンマで続ける。

## 3. GitHub に公開リポジトリを作り push
```bash
cd ~/projects/nwp_xsection_api
git add -A && git commit -m "断面図APIを新規作成"
gh repo create masauehr/nwp_xsection_api --public --source=. --push
```
- push の前に `git status` と `git grep -nE "ghp_|github_pat_|Bearer [A-Za-z0-9]"` で秘密が入っていないことを確認する（`.env` は `.gitignore` 済み）。

## 4. Render で作る
1. Render ダッシュボード → New → **Blueprint** → `nwp_xsection_api` を選ぶ（`render.yaml` の設定で作られる）。
2. 環境変数 `GITHUB_TOKEN`（手順1のトークン）と `API_KEYS`（手順2の項目）を入力する（`sync: false` なので画面で入力）。
3. デプロイが終わるまで待つ（初回ビルドは数分）。ビルドが失敗したら、ログの pygrib の行を確認する（下の「困ったとき」）。

## 5. 動作確認
```bash
URL=https://nwp-xsection-api.onrender.com   # 実際のURLに置き換える
```
1. ブラウザで `$URL/api/health` を開く → `{"ok": true, "ip": "…", "publish": true}`。`ip` が**自分のグローバルIP**なら `TRUSTED_HOPS=1` で合っている（別の機関のIPなら `TRUSTED_HOPS` を調整）。
2. 画面（次の手順）から断面図を1枚作る。Render の Logs で `作図完了 … ○○秒` を確認する。
3. **メモリ**: Render の Metrics でメモリのピークを確認する（目安: 400MB 未満）。足りなければ Starter（2GB）に上げる。
4. **図の文字**: 日本語が豆腐（□）になっていないか確認する。

## 6. 画面側（nwp_chart_web）で有効にする
1. `nwp_chart_web/web/config.js` の `window.NWP_XSECTION_API` に Render の URL（例: `https://nwp-xsection-api.onrender.com`）を入れる。
2. `web/**` を push すると `deploy-web.yml` が数十秒で公開サイトに反映する（画像は作り直さない）。
3. 公開サイトの「自由選択（断面図・エマグラム）」で断面図を作る → 合言葉を入れて「公開サイトに載せる」（2回押し）→ 1〜2分後に一覧へ出ることを確認する。
4. `ALLOWED_ORIGINS` を、使う公開サイトのオリジンに合わせる（`https://masauehr.github.io`）。

## 7. 運用
| 作業 | 方法 |
|---|---|
| 人を追加 | `tools/make_key.py` で作り、`API_KEYS` に足す（Render の Environment を更新→自動で再起動） |
| 人を止める | `API_KEYS` からその項目を消す |
| 合言葉が漏れた | その項目を消して、作り直す |
| トークンの期限 | 期限前に GitHub で作り直し、`GITHUB_TOKEN` を更新する |
| 使われすぎ | `RENDER_PER_HOUR`・`GLOBAL_PER_HOUR` を下げる |
| 無効にしたい | `config.js` の URL を空にして push（公開サイトは閲覧専用に戻る）。Render は停止してよい |

## 困ったとき
- **ビルドで pygrib が入らない**: Dockerfile の apt に `libeccodes-dev gcc` を足し、`pip install pygrib` がソースからビルドできるようにする。
- **「作図に失敗しました」**: Render の Logs に詳細（`作図プロセスが失敗: …`）が出る。メモリ不足（OOM）なら Starter に上げる。
- **「公開サイトへの反映に失敗しました」**: トークンの権限・期限・対象リポジトリを確認。`502` のログに HTTP ステータスが出る。
- **最初の応答が遅い**: 無料枠のスリープ明け（約50秒）。Starter にすると常時起動。
- **429**: 回数制限。待つか、上限を調整する。
