# DEPLOY.md — Render へのデプロイ手順

作成: 2026-11-12 ／ **実施済み**（2026-11-12 に Render へデプロイし、作図・公開・トークン期限の通知まで実機で確認した。以下は手順と、再作成するときの記録）

## 0. 事前に決めること
- 公開する人に渡す**呼び名**（例: `tanaka`）。公開サイトの一覧にも載るので、本名でなくニックネームにする。

## 1. GitHub の fine-grained トークンを作る（公開用）
1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token。
2. Repository access: **Only select repositories** → `masauehr/nwp_chart_web` と `masauehr/tenkizu_web` の2つだけ（tenkizu_web の公開サイトからも載せる・外すため。2026-11-12 追加）。
3. Permissions: Repository permissions の **Contents: Read and write** だけ（他は付けない）。
4. 期限: 90日など。名前は `nwp-xsection-api-publish`。**期限は Slack DM で通知される**（期限の14日前から。下の「トークン期限の Slack 通知」）。通知用に期限を `~/.config/nwp_xsection_api/token_expiry` に書く。
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
1. ブラウザで `$URL/api/health` を開く → `{"ok": true, "ip": "…", "publish": true}`。`ip` が**自分のグローバルIPで、リロードしても変わらない**ことを確認する。違う場合は、環境変数 `DEBUG_IP`=`1` を足して `debug.xff` を見る（確認後は `DEBUG_IP` を削除）。Render では前段に Cloudflare があるため `TRUSTED_HOPS=2`（実機で確認済み）。内部アドレス（10.x）は自動で飛ばす。
2. 画面（次の手順）から断面図とエマグラムを1枚ずつ作る。Render の Logs で `作図完了 … ○○秒／作図・展開プロセスの最大メモリ ○○○MB` を確認する。
3. **メモリ**: 無料枠の Metrics にはメモリが出ないので、**Logs の「最大メモリ」の行**で確認する（実測: 断面図 197MB・エマグラム 276MB。目安は 400MB 未満）。超えるなら Starter（2GB）に上げる。
4. **図の文字**: 日本語が豆腐（□）になっていないか確認する。

## 6. 画面側（nwp_chart_web）で有効にする
1. `nwp_chart_web/web/config.js` の `window.NWP_XSECTION_API` に Render の URL（例: `https://nwp-xsection-api.onrender.com`）を入れる。
2. `web/**` を push すると `deploy-web.yml` が数十秒で公開サイトに反映する（画像は作り直さない）。
3. 公開サイトの「断面図・エマグラム作成」で図を作る → 合言葉を入れて「公開サイトに載せる」（2回押し）→ 1〜2分後に（Cmd+Shift+R で再読み込みして）一覧へ出ることを確認する。画面が古いときは GitHub Pages のキャッシュ（約10分）か、公開ブランチの画面ファイルが古い可能性がある。
4. `ALLOWED_ORIGINS` を、使う公開サイトのオリジンに合わせる（`https://masauehr.github.io`）。

## 7. 運用
| 作業 | 方法 |
|---|---|
| 人を追加 | `tools/make_key.py` で作り、`API_KEYS` に足す（Render の Environment を更新→自動で再起動） |
| 人を止める | `API_KEYS` からその項目を消す |
| 合言葉が漏れた | その項目を消して、作り直す |
| トークンの期限 | 期限の14日前から Slack DM で通知が届く。「トークンの更新」の手順で作り直す |
| 使われすぎ | `RENDER_PER_HOUR`・`GLOBAL_PER_HOUR` を下げる |
| 無効にしたい | `config.js` の URL を空にして push（公開サイトは閲覧専用に戻る）。Render は停止してよい |
| Render が再デプロイされる | `main` に push するたびに自動で再デプロイされる（文書だけの push でも）。その間（数分）は作図が使えず、作図中のジョブは消える（画面は自動でやり直す）。避けたいなら `render.yaml` の `autoDeploy: false`（手動デプロイ） |
| Render のインフラ保守 | 告知（例: 2026-11-18 11:00 JST）の間は止まりうる。当日はデプロイを避け、後で動作確認する |

## トークンの更新（期限が近づいたとき・Slack の通知が届いたとき）
1. GitHub → Settings → Developer settings → Fine-grained tokens で、**同じ設定**（`nwp_chart_web` と `tenkizu_web` の2つだけ・Contents: Read and write だけ・名前 `nwp-xsection-api-publish`）の新しいトークンを作る（古いトークンは、更新後に Delete してよい）。
2. Render の `nwp-xsection-api` → Environment で `GITHUB_TOKEN` を新しい値に差し替えて保存する（自動で再起動する）。トークンの値はチャットやファイルに貼らない。
3. 公開サイトで1件「公開」して、反映されることを確認する（確認後に外してよい）。
4. **`~/.config/nwp_xsection_api/token_expiry` を新しい期限（UTC・1行）に書き換える**（例: `2027-02-10T00:00:00Z`。GitHub の画面の期限は日本時間のことがあるので、9時間引く）。書き換えると通知が止まる。忘れると、期限切れの通知が毎日届く。

## トークン期限の Slack 通知
Bluesky 自動投稿・nwp_chart_web と同じ方式（launchd が毎日起動し、`openclaw message send` で Slack DM に送る。部品は共有せずコピー）。

| 項目 | 内容 |
|---|---|
| スクリプト | `ops/token_expiry_notify.py`（標準ライブラリのみ） |
| 実行 | launchd `com.masahiro.nwp_xsection_api_token_notify`（`launchd/` に雛形。毎日 9:10 JST。Mac がスリープ中なら復帰後に1回） |
| 設定（git 管理外） | `~/.config/nwp_xsection_api/token_expiry`（期限・UTC・1行）・`slack_to`（通知先・`slack:Uxxxx`）・`notify_state.json`（自動）・`notify.log` |
| 通知する時 | 期限の **14日前・7日前・3日前・2日前・1日前**。期限切れ後は毎日（`token_expiry` を書き換えるまで）。送れなかった分は次の実行で再送 |
| 内容 | 残り日数・期限（日本時間）・更新手順の場所。トークンの値は含めない |

登録（初回だけ）:
```bash
mkdir -p ~/.config/nwp_xsection_api
echo "2027-02-10T00:00:00Z" > ~/.config/nwp_xsection_api/token_expiry     # 実際の期限（UTC）に
cp ~/.config/nwp_chart_web/slack_to ~/.config/nwp_xsection_api/slack_to     # 通知先は nwp_chart_web と同じでよい
cp launchd/com.masahiro.nwp_xsection_api_token_notify.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.masahiro.nwp_xsection_api_token_notify.plist
/opt/homebrew/bin/python3 ops/token_expiry_notify.py --test                 # テスト通知（届けば正常）
/opt/homebrew/bin/python3 ops/token_expiry_notify.py --dry-run --now 2027-01-27T00:00:00Z   # 通知文の確認（送らない）
```

## 困ったとき
- **ビルドで pygrib が入らない**: Dockerfile の apt に `libeccodes-dev gcc` を足し、`pip install pygrib` がソースからビルドできるようにする。
- **「作図に失敗しました」**: Render の Logs に詳細（`作図プロセスが失敗: …`）が出る。メモリ不足（OOM）なら Starter に上げる。
- **「公開サイトへの反映に失敗しました」**: トークンの権限・期限・対象リポジトリを確認。`502` のログに HTTP ステータスが出る。
- **最初の応答が遅い**: 無料枠のスリープ明け（約50秒）。画面は「サーバーを起動しています」と待つ。Starter にすると常時起動。
- **「作図の記録が見つかりません」**: サーバーが再起動して、ジョブの記録（メモリ上）が消えた。画面が同じ条件を自動で再送するので、通常は何もしなくてよい。（過去の「ジョブが見つかりません」は画面のバグで、2026-11-12 に修正済み。）
- **Render が Failed deploy になる**: Logs を見る。起動時の `ModuleNotFoundError` は、新しい `.py` を Dockerfile の `COPY` に入れ忘れている（`tests/test_dockerfile.py` が検出する）。
- **429**: 回数制限。待つか、上限を調整する。
- **Slack の通知が届かない**: `~/.config/nwp_xsection_api/notify.log` を見る。`slack_to`・`token_expiry` の有無、`openclaw` が使えるか（`openclaw message send` が通るか）を確認する。
