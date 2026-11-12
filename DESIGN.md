# DESIGN.md — 設計の判断と根拠

作成: 2026-11-12

## 全体
```
[GitHub Pages: 数値モデルビューア（静的）]
   └ 自由選択ページ: config.js の URL があれば Render の API を呼ぶ
            ↓ GET /api/xsection → job → ポーリング → /img/…png
[Render: nwp_xsection_api]
   作図（別プロセス）→ /tmp にキャッシュ
   公開（合言葉つき）→ GitHub Contents API → gh-pages の output/custom/ に画像と list.json
```

## 判断の表
| 判断 | 理由 |
|---|---|
| 作図を非同期ジョブ（202＋ポーリング）にする | 1回が1〜2分（取得込み）。Render の応答タイムアウトより長くなりうる |
| 作図ジョブは1本のワーカーで順に処理（同時1件・待ち行列3件） | メモリ512MB。同時に2件走ると超える |
| 作図・GRIB2展開を別プロセスにする | pygrib の展開は一時的に約300MBを使い、作図後も本体が約280MBを保持した。別プロセスなら終わると全て戻る（待機時 約36MB・最大合計 約347MB） |
| 展開結果を `.npz` にして GRIB2 を捨てる | 同じ初期値・予報時間の別の断面を、再取得も再展開もせず作れる。ディスクも小さく済む |
| pygrib の切り出しをコピーする | ビューのままだと全球配列（約4MB×78枚）が残り、メモリが約300MB増えた |
| 高度（gh）は 500hPa だけ取る | 小地図の等高線にしか使わない。メッセージ数と取得量が約15%減り、展開時のメモリも減る |
| 相当温位を numpy（Bolton 1980）にする | MetPy は pint・xarray 等で重い。MetPy と 0.3K 以内で一致することをテストで確認（`test_ept_matches_metpy`） |
| 小地図を cartopy でなく `coast.json` にする | cartopy は重く、海岸線データを実行時に取得しにいく（永続ディスクなしで不安定）。nwp_chart_web の地図用 JSON を再利用 |
| サーバー本体で numpy 等を import しない | 検証のたびに約80MB増えていた。`opts.py` に分け、テストで確認 |
| 作図は認証なし・回数制限あり | 使うのが面倒だと使われない。IP ごと8回/時・全体60回/時・同時1件・待ち行列3件で負荷を抑える。キャッシュに当たる分と、処理中の同じ条件は数えない |
| 公開だけ合言葉 | GitHub への書き込みは悪用されうる。個別に発行・停止でき、誰が公開したか（`by`）を一覧に残せる |
| 合言葉は SHA-256 で持つ | 環境変数が漏れても合言葉は分からない（`secrets.token_urlsafe(24)` ＝約192ビットなので総当たりも非現実的） |
| 合言葉の間違いは10分に5回まで | 総当たり対策。正しい合言葉でも、間違いが続いた相手は一定時間拒否する |
| IP は `X-Forwarded-For` の右から `TRUSTED_HOPS` 番目 | 左側は相手が偽装できる。Render は前段に Cloudflare があり、右端の公開アドレスがそれなので、右から2番目（`TRUSTED_HOPS=2`・実機で確認）。内部アドレス（10.x）は数えずに飛ばす。外れていても全体の上限が効く |
| GitHub へは Contents API（clone しない） | gh-pages は約0.9GBで、Render では clone できない。画像と `list.json` だけ書く |
| コミットが増えるのは許容 | `publish.py` が次の更新で孤立コミットに作り直すので、履歴は残らない。衝突（sha 不一致）は最大3回再試行 |
| 公開は「キャッシュが残っている間」だけ | Render は永続ディスクがない。無ければ 410 を返し、作図し直しを促す |
| 入力検証で配信されないステップを先に弾く | 存在しないステップを配信元に問い合わせない（無駄な通信と待ちを防ぐ） |
| 共通部品化しない | 自己完結（単独クローンで動く）を優先する方針。nwp_chart_web 等とは別々に直す |

## メモリの実測（手元の Mac・GFS 0.25°・13層）
| 段階 | 合計メモリの最大 |
|---|---|
| 素朴な実装（1プロセス・切り出しがビュー） | 約654MB |
| 切り出しをコピー | 約482MB |
| 展開を別プロセス・npz 化 | 約486MB（本体が作図後に約280MBを保持） |
| 作図も別プロセス・検証を軽く | 約440MB |
| 高度は 500hPa だけ | **約347MB**（待機時 約36MB・作図後 約36MB） |

Linux（Render）では値が変わる。デプロイ後に確認する（[DEPLOY.md](DEPLOY.md)）。

## 応答の形（画面との約束）
- `GET /api/xsection` → `{"state":"done","name","url":"/img/…","label","model","initText","ft","legal"}` または `202 {"state":"queued","job","position"}`。
- `GET /api/job/<id>` → `{"state":"queued|running","msg","position"}` / `done`（上と同じ）/ `{"state":"error","error"}`。
- 公開で書く `list.json` の項目: `kind, mk, ik, ft, label, model, initText, url("output/custom/<名前>"), ts, by`。ローカルの `publish.py --custom` と同じ形式（`by` は追加）。
