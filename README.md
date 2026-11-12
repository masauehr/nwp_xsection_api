# nwp_xsection_api — 数値モデルの断面図API（Render 用）

詳しくは [nwp-xsection-api.md](nwp-xsection-api.md) を参照。計画・進捗は [plan.md](plan.md)、設計は [DESIGN.md](DESIGN.md)、デプロイ手順は [DEPLOY.md](DEPLOY.md)。

ECMWF（IFS・AIFS）オープンデータと NOAA GFS（0.25°）から、**任意の断面線の鉛直断面図**と**任意の地点のエマグラム**を作って返す小さな API です。
[nwp_chart_web](https://github.com/masauehr/nwp_chart_web)（数値モデル天気図ビューア）の公開サイトから呼ばれ、作った図を公開サイト（GitHub Pages）に載せる処理も代行します。
エマグラムは気温・露点・持ち上げ気塊・CAPE/CIN・ホドグラフ・地点の小地図つきです。

- 作図は**認証なし**（回数制限あり）。公開サイトに載せるときだけ**合言葉**が要ります（GitHub への登録は不要）。
- 標準ライブラリの HTTP サーバー＋ numpy / scipy / matplotlib / pygrib。cartopy は使いません（軽量化）。MetPy はエマグラム（SkewT・CAPE）の作図の別プロセスの中でだけ読み込みます（サーバー本体・断面図には読み込みません）。
- Render の無料枠（メモリ512MB）に収める設計です。**Render の実測**: 作図・展開プロセスの最大メモリは断面図 197MB（72〜93秒）・エマグラム 276MB（114秒）、待機時は約36MB（手元の Mac では断面図の合計 約347MB）。

## API
| 呼び方 | 認証 | 内容 |
|---|---|---|
| `GET /api/health` | なし | 動作確認。`ip` は回数制限が見ている相手のアドレス |
| `GET /api/xsection?model=gfs&init=YYYYMMDDHH&ft=12&lat1=45&lon1=130&lat2=25&lon2=130[&shade=&contours=&barbs=]` | なし | 同じ条件の画像があれば `200 {"state":"done",…}`、無ければジョブを作って `202 {"state":"queued","job":…}` |
| `GET /api/emagram?model=gfs&init=YYYYMMDDHH&ft=12&lat=26.2&lon=127.7` | なし | エマグラム。受付・応答は断面図と同じ（`202`→ジョブ→画像）。地点は緯度 −15〜82・経度 50〜185 の中 |
| `GET /api/job/<id>` | なし | `queued` / `running`（`job`・`msg`・`position`）/ `done`（`url`・`label`・`legal` つき）/ `error`。サーバーが再起動して記録が消えていると `404`（画面は同じ条件を自動で再送する） |
| `GET /img/<名前>.png` | なし | 作った画像（`/tmp` の短期キャッシュ。時間が経つと消える） |
| `POST /api/publish` `{"name":"xsection_….png"}` | `Authorization: Bearer <合言葉>` | 公開サイトの `output/custom/` に載せる（直近20件） |
| `POST /api/unpublish` `{"name":…}` | 同上 | 公開サイトから外す |

- `model`: `ecmwf` / `gfs` / `aifs`。`init`: UTC・00/06/12/18時・10日以内。`ft`: 3時間刻み（AIFS は6時間刻み・ECMWF の06/18UTC は90hまで）。
- 断面線: 緯度 −15〜82・経度 50〜185 の中で、1〜60度。
- 公開すると、`list.json` の項目に `ts`（作図した時刻）・`pub`（公開した時刻＝登録日時）・`by`（呼び名）が入る（時刻は UNIX 秒）。
- `shade`（`none`/`div`/`rh`/`omega`）・`contours`（`theta,ept,wind` をカンマ区切り）・`barbs`（`off`/`back`/`front`）。省略すると従来の見た目。
- `ptop`（断面図・エマグラム共通）: **高度設定**＝表示する高度の範囲の上端の気圧[hPa]。`100`（既定・全層）/`150`/`200`/`250`/`300`/`400`/`500`/`600`/`700`。数字が小さいほど高い所まで。省略か `100` なら従来の見た目で、画像名も従来と同じ。断面図は上端までの層だけで計算し直す（範囲内の解像度が上がる）。エマグラムは縦軸とホドグラフを上端までにする（CAPE・CIN は全層で計算するので変わらない）。選択肢以外は 400。
- 1つの図は1回に1〜2分かかるため、すぐ返してポーリングする方式です（Render のタイムアウト対策）。

## 環境変数
| 名前 | 既定 | 内容 |
|---|---|---|
| `PORT` | 8800 | 待ち受けポート（Render が設定する） |
| `ALLOWED_ORIGINS` | `https://masauehr.github.io`＋ローカル | ブラウザからの呼び出しを許可するサイト（カンマ区切り） |
| `GITHUB_TOKEN` | なし | 公開用。対象リポジトリの Contents 書き込み権限だけの fine-grained トークン（**秘密**） |
| `API_KEYS` | なし | 公開用の合言葉。`呼び名:SHA-256` のカンマ区切り（`tools/make_key.py` で作る・**秘密**） |
| `GITHUB_REPO` / `GITHUB_BRANCH` / `PUBLISH_DIR` | `masauehr/nwp_chart_web` / `gh-pages` / `output/custom` | 公開先 |
| `PUBLIC_SITE_URL` | `https://masauehr.github.io/nwp_chart_web/` | 公開後に画面へ返すURL |
| `RENDER_PER_HOUR` / `GLOBAL_PER_HOUR` / `QUEUE_MAX` | 8 / 60 / 3 | IPごとの1時間の作図回数・全体の回数・待ち行列の上限 |
| `TRUSTED_HOPS` | 2 | `X-Forwarded-For` の右から、内部アドレス（10.x など）を除いて何番目の公開アドレスを相手のIPとみなすか（Render は前段の Cloudflare が右端なので **2**。`/api/health` の `ip` で確認） |
| `DEBUG_IP` | なし | `1` のとき `/api/health` に受け取った `X-Forwarded-For` を出す（IPの診断用。確認後は外す） |
| `WORK_DIR` | `/tmp/nwp_xsection` | 画像・取得データの置き場 |
| `CACHE_MAX_FILES` / `GRIB_MAX_MB` / `RENDER_TIMEOUT` | 40 / 300 / 600 | 画像の保持件数・取得データの保持容量・作図の制限時間（秒） |

## ローカルで動かす・テスト
```bash
PY=/opt/anaconda3/envs/met_env_310/bin/python
$PY -m pytest tests                       # 111件・約17秒（ネットワーク不要。エマグラムのテストは MetPy が要る）
PORT=8800 $PY app.py                      # 公開（GitHub への掲載）は GITHUB_TOKEN と API_KEYS が無いと無効（作図は使える）
$PY tools/make_key.py tanaka              # 合言葉を作る（合言葉は相手に渡し、ハッシュの行を API_KEYS に足す）
```
画面側（nwp_chart_web の `web/config.js` の `NWP_XSECTION_API`）に URL を入れると、公開サイトの「断面図・エマグラム作成」ページで断面図・エマグラムを作れます（2026-11-12 に有効化済み）。

## ファイル構成
| ファイル | 内容 |
|---|---|
| `app.py` | HTTP サーバー・作図ジョブ・回数制限・CORS・公開 API |
| `emagram.py` | エマグラムの計算と描画（nwp_chart_web のコピーを調整。MetPy の SkewT・CAPE を使う） |
| `plotutil.py` | 断面図・エマグラムが共有する描画の補助（フォント・海岸線・見出し・PNG の減色） |
| `engine.py` | 入力の検証・画像名・キャッシュ・取得データの展開（別プロセス）・作図（別プロセス） |
| `nwp_data.py` | 取得（索引のバイト範囲だけ）・GRIB2 の読み込み・npz 変換・出典文言 |
| `xsection.py` | 断面図の計算と描画（nwp_chart_web のコピーを軽量化。MetPy 不使用） |
| `opts.py` | 表示要素の指定（軽いモジュール） |
| `auth.py` / `limits.py` / `ghpub.py` | 合言葉の確認 / 回数制限 / GitHub の Contents API |
| `tools/make_key.py` | 合言葉の発行 |
| `ops/token_expiry_notify.py`・`launchd/` | 公開用 GitHub トークンの期限を Slack DM で通知（期限の14/7/3/2/1日前・期限切れ後は毎日。手順は DEPLOY.md） |
| `assets/coast.json` | 小地図の海岸線（Natural Earth 50m・パブリックドメイン） |
| `Dockerfile` / `render.yaml` | Render 用 |
| `tests/` | 単体テスト（検証・描画・認証・GitHub・サーバー全体） |

## データの出典・利用条件
- ECMWF オープンデータ（IFS・AIFS）: CC BY 4.0（出典明記）。
- NOAA GFS: NOAA の成果物（出典明記を推奨）。
- 画像・応答の `legal` に出典・改変・免責の文言を入れています。画面に出してください。規約は変わり得るので、公開を続ける間は配信元で定期的に確認してください。
