#!/opt/homebrew/bin/python3
"""断面図API（Render）の公開用 GitHub トークンの期限が近づいたら Slack DM で知らせる。

Bluesky自動投稿（automated-data-collector）・nwp_chart_web・taiwan_radar_frames と同じ方式（部品は共有せずコピー）:
  launchd が毎日このスクリプトを起動し、`openclaw message send` 経由で Slack DM に送る。

設定ファイル（~/.config/nwp_xsection_api/ ・git管理外）:
  token_expiry   トークンの期限（1行。例: 2026-12-08T00:15:02Z）。トークンを更新したら書き換える
  slack_to       通知先（1行。例: slack:Uxxxxxxxx）。環境変数 NWP_XSEC_NOTIFY_TO があればそれを優先
  notify_state.json  通知済みの段階（自動で作られる。期限が変わると自動でリセット）

通知する段階: 期限の14日前・7日前・3日前・2日前・1日前、期限切れ後は毎日（更新して token_expiry を書き換えるまで）。
通知の失敗でスクリプトを止めない（送れなかった段階は次回の実行で再送する）。

使い方:
  token_expiry_notify.py                  通常実行（launchdから）
  token_expiry_notify.py --dry-run --now 2026-12-02T00:00:00Z   通知文を表示するだけ（送らない・状態も更新しない）
  token_expiry_notify.py --test           テスト通知を送る
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONF = Path(os.environ.get("NWP_XSEC_CONF_DIR") or (Path.home() / ".config" / "nwp_xsection_api"))
JST = timezone(timedelta(hours=9))
THRESHOLDS = [1, 2, 3, 7, 14]  # 残り日数がこの値以下になった最初の実行で通知
GUIDE = "~/projects/nwp_xsection_api/DEPLOY.md の「トークンの更新」"
TOKEN_NAME = "nwp-xsection-api-publish"


def parse_time(text):
    """'2026-12-08T00:15:02Z' や '2026-12-08 00:15:02 UTC' → UTCのdatetime"""
    s = text.strip().replace(" UTC", "Z").replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def iso_z(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def level_for(now, expiry):
    """通知の段階。None=まだ早い / 14,7,3,2,1=残り日数の段階 / 'expired'=期限切れ"""
    remaining = expiry - now
    if remaining <= timedelta(0):
        return "expired"
    days = math.ceil(remaining / timedelta(days=1))
    for t in THRESHOLDS:
        if days <= t:
            return t
    return None


def decide(now, expiry, state):
    """(段階, 送るか)。期限が変わった（=トークン更新済み）なら状態を無視する"""
    lvl = level_for(now, expiry)
    if lvl is None:
        return None, False
    if state.get("expiry") != iso_z(expiry):
        state = {}
    if lvl == "expired":
        today = now.astimezone(JST).strftime("%Y-%m-%d")
        return lvl, state.get("last_date") != today  # 期限切れは毎日
    return lvl, state.get("level") != lvl


def message(now, expiry, lvl):
    exp_jst = expiry.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")
    head = "【nwp_xsection_api】"
    steps = ("更新手順: " + GUIDE + "\n"
             "（GitHubで同じ権限のトークン " + TOKEN_NAME + " を作り直す → RenderのEnvironmentの GITHUB_TOKEN を差し替える）\n"
             "更新後は " + str(CONF / "token_expiry") + " を新しい期限に書き換えてください（通知が止まります）。")
    if lvl == "expired":
        return (head + "❌ 公開用のGitHubトークン（" + TOKEN_NAME + "）が**期限切れ**です（期限 " + exp_jst + "）\n"
                "公開サイトに断面図・エマグラムを載せる／外す操作が失敗します（作図は影響を受けません）。\n"
                "今すぐトークンを更新してください。\n" + steps)
    days = math.ceil((expiry - now) / timedelta(days=1))
    mark = "⚠️" if lvl <= 3 else "🔔"
    return (head + mark + " 公開用のGitHubトークン（" + TOKEN_NAME + "）の期限まで、あと約" + str(days) + "日（" + exp_jst + "）\n"
            "期限が切れると、公開サイトに断面図・エマグラムを載せる／外す操作が失敗します（作図は影響を受けません）。\n" + steps)


def _target():
    t = os.environ.get("NWP_XSEC_NOTIFY_TO", "").strip()
    f = CONF / "slack_to"
    if not t and f.exists():
        t = f.read_text().strip()
    return t


def send(text):
    """Slack DM へ送る。成功したら True（失敗しても例外は出さない）"""
    to = _target()
    exe = shutil.which("openclaw") or "/opt/homebrew/bin/openclaw"
    if not to:
        print("通知先が未設定のため通知しない（" + str(CONF / "slack_to") + "）", file=sys.stderr)
        return False
    try:
        r = subprocess.run([exe, "message", "send", "--channel", "slack", "--target", to, "-m", text[:3500]],
                           capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            print("Slack通知に失敗:", (r.stderr or r.stdout).strip()[:200], file=sys.stderr)
        return r.returncode == 0
    except Exception as e:  # 通知の失敗で本体を止めない
        print("Slack通知で例外:", e, file=sys.stderr)
        return False


def load_state():
    try:
        return json.loads((CONF / "notify_state.json").read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    CONF.mkdir(parents=True, exist_ok=True)
    (CONF / "notify_state.json").write_text(json.dumps(state, ensure_ascii=False))


def run(now, dry_run=False):
    """戻り値: 終了コード（0=正常 / 2=設定不足）"""
    f = CONF / "token_expiry"
    if not f.exists():
        print("設定がありません: " + str(f) + "（トークンの期限を1行で書く。例: 2026-12-08T00:15:02Z）", file=sys.stderr)
        return 2
    expiry = parse_time(f.read_text())
    state = load_state()
    lvl, do_send = decide(now, expiry, state)
    left = (expiry - now).total_seconds() / 86400
    print("期限 %s ／ 残り %.1f日 ／ 段階 %s ／ 送信 %s" % (iso_z(expiry), left, lvl, "する" if do_send else "しない"))
    if not do_send:
        return 0
    text = message(now, expiry, lvl)
    if dry_run:
        print("--- 通知文（dry-run・送信しない）---\n" + text)
        return 0
    if send(text):
        state = {"expiry": iso_z(expiry), "level": lvl, "last_date": now.astimezone(JST).strftime("%Y-%m-%d")}
        save_state(state)
        print("通知しました")
    else:
        print("通知できなかったため、次回の実行で再送します", file=sys.stderr)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="通知文を表示するだけ（送らない・状態も更新しない）")
    p.add_argument("--now", help="現在時刻の代わり（テスト用。例: 2026-12-02T00:00:00Z）")
    p.add_argument("--test", action="store_true", help="テスト通知を送る")
    args = p.parse_args(argv)
    if args.test:
        ok = send("【nwp_xsection_api】トークン期限通知のテストです（このメッセージが届けば設定は正常）")
        return 0 if ok else 1
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    return run(now, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
