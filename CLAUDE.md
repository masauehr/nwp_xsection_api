# CLAUDE.md — nwp_xsection_api

## 概要
数値モデル天気図ビューア（nwp_chart_web）の「自由選択の断面図・エマグラム」を、公開サイトから使えるようにする **Render 用の API**。作図は認証なし（回数制限あり）、公開サイトへの掲載だけ合言葉が要る。計画と進捗は [plan.md](plan.md)、設計は [DESIGN.md](DESIGN.md)、デプロイは [DEPLOY.md](DEPLOY.md)、マニュアルは [nwp-xsection-api.md](nwp-xsection-api.md)。

## 実行環境
- Python: `/opt/anaconda3/envs/met_env_310/bin/python`（pygrib・scipy・matplotlib・requests・pillow・metpy。テストは pytest）。`python -I` は使わない。
- テスト: `python -m pytest tests`（ネットワーク不要・約10秒）。起動: `PORT=8800 python app.py`。
- ローカルで試すとき、**8790 は nwp_chart_web のサーバー、8791 は tenkizu_web が使う**。別のポート（API は 8800 など）を使い、既存のサーバーを止めない。

## 規約
- コメント・UIは日本語。インデント: Python 4、JS 2。f-string内で同じ種類の引用符を入れ子にしない（Python 3.10）。
- **nwp_chart_web・tenkizu_web と共通部品化しない**（`xsection.py`・`nwp_data.py`・`engine.py` はコピーを調整したもの。両方を直すときは別々に直す）。

## 守ること（外さない）
- **サーバー本体（`app.py`・`engine.py` の検証部分）で numpy・matplotlib・scipy・metpy を import しない**（メモリ。`tests/test_engine.py` が確認）。MetPy はエマグラム（`emagram.py`）の作図プロセスの中でだけ読み込む（約95MB）。断面図は MetPy を使わない。表示要素の指定は軽い `opts.py` に分けてある。
- **作図は別プロセス（`engine.render_subprocess`）、GRIB2 の展開も別プロセス**（`nwp_data.py pack`）。展開は一時的に約300MBを使う。どちらも終われば全メモリが戻る。
- 秘密（`GITHUB_TOKEN`・`API_KEYS`）はリポジトリに入れない（Render の環境変数だけ）。合言葉は SHA-256 のハッシュで持つ（`tools/make_key.py`）。ログ・応答に合言葉やトークンを出さない。
- `/api/publish`・`/api/unpublish` は合言葉つき・間違いが続いたら一定時間拒否・Origin を許可リストで確認（外さない）。作図 API は認証なしだが、**回数制限・検証（`engine.validate`）・待ち行列の上限を外さない**。
- 想定外の例外の詳細は画面に返さない（ログだけ）。入力・データの問題（`CustomError`）のメッセージだけを返す。
- 出典・ライセンス・免責の表示（`nwp_data.legal_short`）を外さない（ECMWF CC BY 4.0・NOAA の要請）。応答の `legal` を画面に出している。
- GitHub への書き込みは対象リポジトリの `output/custom/` の画像と `list.json` だけ（`ghpub.py`）。画面ファイルやモデルの画像には触れない。形式は nwp_chart_web の `publish.py --custom` と同じにする（`list.json` の項目: kind・mk・ik・ft・label・model・initText・url・ts。`pub`（公開した時刻）・`by` は追加項目）。

## 落とし穴
- **公開用 GitHub トークンの期限通知**（`ops/token_expiry_notify.py`・launchd・`~/.config/nwp_xsection_api/`）は、nwp_chart_web の `ops/token_expiry_notify.py` のコピー（共通部品化しない）。トークンを更新したら **`token_expiry` を書き換える**（忘れると期限切れの通知が毎日届く）。トークンの値は書かない・貼らない。通知文・段階を変えたら `tests/test_token_expiry_notify.py` も直す。
- **状態確認の応答（`/api/job/<id>`）には `job` を必ず含める**。画面（`web/custom.js`）はジョブIDを別の変数に持っているが、応答を丸ごと代入しても壊れないようにしてある（過去に `/api/job/undefined` で「ジョブが見つかりません」になった。`tests/test_app.py::test_job_status_keeps_job_id`）。
- 取得の一時ファイルは `<名前>.<pid>.part`（プロセスごとに別名）。同じファイルを2つのプロセスが同時に取っても、お互いを壊さない。
- **新しい `.py` を足したら Dockerfile の `COPY` にも入れる**（入れ忘れると Render で起動時に落ちる。`tests/test_dockerfile.py` が確認）。依存を足したら `requirements.txt` にも（エマグラムの MetPy は `requirements.txt`）。
- **pygrib の切り出しは必ずコピーする**（`np.array(…)`）。ビューのままだと切り出し前の全球配列が残り、メモリが約300MB増える。
- 高度（gh）は、断面図では 500hPa だけ取る（小地図用）。**エマグラムは全層の高度が要る**（ホドグラフの色分け）ので、別ファイル（`_gh`）で取る（`nwp_data.grib_path(all_gh=True)`）。
- 小地図（断面図・エマグラム）は cartopy を使わず `assets/coast.json`。経度が360→0に折り返す所で線を切らないと横線が走る（`_coast_lines`）。
- 作図結果は Render の `/tmp`（短期）。公開は、キャッシュが残っている間だけできる（無ければ 410 → 作図し直す）。
- Render の無料枠は約15分でスリープする。ジョブ（メモリ上）は再起動で消える（画面は 404 を「もう一度作図」と案内する）。
- 画面側は nwp_chart_web の `web/custom.js`・`web/config.js`（`NWP_XSECTION_API` が空なら閲覧専用のまま）。API の URL・応答形式を変えたら画面側も直す。

## ドキュメント更新ルール
- 変更したら `pc_docs/manuals/automation/nwp-xsection-api.md` と、このリポジトリ内のコピー `nwp-xsection-api.md` の両方、`pc_docs/README.md` の表を更新する。
- `git add` → `git commit` → `git push`（push 前にユーザーの確認を取る）。
