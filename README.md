# nwp_xsection_api — 数値モデルの断面図API（Render 用）

詳しくは [nwp-xsection-api.md](nwp-xsection-api.md) を参照。計画・進捗は [plan.md](plan.md)、設計は [DESIGN.md](DESIGN.md)、デプロイ手順は [DEPLOY.md](DEPLOY.md)。

ECMWF（IFS・AIFS）オープンデータと NOAA GFS（0.25°）から、**任意の断面線の鉛直断面図**を作って返す小さな API です。
[nwp_chart_web](https://github.com/masauehr/nwp_chart_web)（数値モデル天気図ビューア）の公開サイトから呼ばれ、作った図を公開サイト（GitHub Pages）に載せる処理も代行します。

- 作図は**認証なし**（回数制限あり）。公開サイトに載せるときだけ**合言葉**が要ります（GitHub への登録は不要）。
- 標準ライブラリの HTTP サーバー＋ numpy / scipy / matplotlib / pygrib。cartopy・metpy は使いません（軽量化）。
- Render の無料枠（メモリ512MB）に収める設計です（実測の合計メモリの最大 約347MB・待機時 約36MB）。

## API
| 呼び方 | 認証 | 内容 |
|---|---|---|
| `GET /api/health` | なし | 動作確認。`ip` は回数制限が見ている相手のアドレス |
| `GET /api/xsection?model=gfs&init=YYYYMMDDHH&ft=12&lat1=45&lon1=130&lat2=25&lon2=130[&shade=&contours=&barbs=]` | なし | 同じ条件の画像があれば `200 {"state":"done",…}`、無ければジョブを作って `202 {"state":"queued","job":…}` |
| `GET /api/job/<id>` | なし | `queued` / `running` / `done`（`url`・`label`・`legal` つき）/ `error` |
| `GET /img/<名前>.png` | なし | 作った画像（`/tmp` の短期キャッシュ。時間が経つと消える） |
| `POST /api/publish` `{"name":"xsection_….png"}` | `Authorization: Bearer <合言葉>` | 公開サイトの `output/custom/` に載せる（直近10件） |
| `POST /api/unpublish` `{"name":…}` | 同上 | 公開サイトから外す |

- `model`: `ecmwf` / `gfs` / `aifs`。`init`: UTC・00/06/12/18時・10日以内。`ft`: 3時間刻み（AIFS は6時間刻み・ECMWF の06/18UTC は90hまで）。
- 断面線: 緯度 −15〜82・経度 50〜185 の中で、1〜60度。
- 公開すると、`list.json` の項目に `ts`（作図した時刻）・`pub`（公開した時刻＝登録日時）・`by`（呼び名）が入る（時刻は UNIX 秒）。
- `shade`（`none`/`div`/`rh`/`omega`）・`contours`（`theta,ept,wind` をカンマ区切り）・`barbs`（`off`/`back`/`front`）。省略すると従来の見た目。
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
$PY -m pytest tests                       # 72件・約10秒（ネットワーク不要）
PORT=8800 $PY app.py                      # 公開（GitHub への掲載）は GITHUB_TOKEN と API_KEYS が無いと無効（作図は使える）
$PY tools/make_key.py tanaka              # 合言葉を作る（合言葉は相手に渡し、ハッシュの行を API_KEYS に足す）
```
画面側（nwp_chart_web の `web/config.js` の `NWP_XSECTION_API`）に URL を入れると、公開サイトの「自由選択」ページで断面図を作れます。

## ファイル構成
| ファイル | 内容 |
|---|---|
| `app.py` | HTTP サーバー・作図ジョブ・回数制限・CORS・公開 API |
| `engine.py` | 入力の検証・画像名・キャッシュ・取得データの展開（別プロセス）・作図（別プロセス） |
| `nwp_data.py` | 取得（索引のバイト範囲だけ）・GRIB2 の読み込み・npz 変換・出典文言 |
| `xsection.py` | 断面図の計算と描画（nwp_chart_web のコピーを軽量化） |
| `opts.py` | 表示要素の指定（軽いモジュール） |
| `auth.py` / `limits.py` / `ghpub.py` | 合言葉の確認 / 回数制限 / GitHub の Contents API |
| `tools/make_key.py` | 合言葉の発行 |
| `assets/coast.json` | 小地図の海岸線（Natural Earth 50m・パブリックドメイン） |
| `Dockerfile` / `render.yaml` | Render 用 |
| `tests/` | 単体テスト（検証・描画・認証・GitHub・サーバー全体） |

## データの出典・利用条件
- ECMWF オープンデータ（IFS・AIFS）: CC BY 4.0（出典明記）。
- NOAA GFS: NOAA の成果物（出典明記を推奨）。
- 画像・応答の `legal` に出典・改変・免責の文言を入れています。画面に出してください。規約は変わり得るので、公開を続ける間は配信元で定期的に確認してください。
