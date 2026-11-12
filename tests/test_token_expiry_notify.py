"""ops/token_expiry_notify.py の単体テスト（ネットワーク・Slack不使用）"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 設定ディレクトリを一時にしてから読み込む（実際の ~/.config を触らない）
_TMP = tempfile.TemporaryDirectory()
os.environ["NWP_XSEC_CONF_DIR"] = _TMP.name
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ops"))
import token_expiry_notify as n  # noqa: E402

EXP = datetime(2026, 12, 8, 0, 15, 2, tzinfo=timezone.utc)


def at(days_before, hours=0):
    return EXP - timedelta(days=days_before, hours=hours)


class TestParse(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(n.parse_time("2026-12-08T00:15:02Z"), EXP)
        self.assertEqual(n.parse_time("2026-12-08 00:15:02 UTC"), EXP)
        self.assertEqual(n.parse_time("2026-12-08T09:15:02+09:00"), EXP)
        self.assertEqual(n.parse_time(" 2026-12-08T00:15:02Z\n"), EXP)


class TestLevel(unittest.TestCase):
    def test_levels(self):
        self.assertIsNone(n.level_for(at(15), EXP))
        self.assertEqual(n.level_for(at(14), EXP), 14)
        self.assertEqual(n.level_for(at(10), EXP), 14)
        self.assertEqual(n.level_for(at(7), EXP), 7)
        self.assertEqual(n.level_for(at(3), EXP), 3)
        self.assertEqual(n.level_for(at(2), EXP), 2)
        self.assertEqual(n.level_for(at(0, 5), EXP), 1)   # 残り5時間
        self.assertEqual(n.level_for(EXP, EXP), "expired")
        self.assertEqual(n.level_for(EXP + timedelta(days=3), EXP), "expired")


class TestDecide(unittest.TestCase):
    def test_send_once_per_level(self):
        lvl, do = n.decide(at(14), EXP, {})
        self.assertEqual((lvl, do), (14, True))
        state = {"expiry": n.iso_z(EXP), "level": 14, "last_date": "x"}
        self.assertFalse(n.decide(at(13), EXP, state)[1])      # 同じ段階は再送しない
        self.assertEqual(n.decide(at(7), EXP, state), (7, True))  # 段階が進めば送る

    def test_new_expiry_resets_state(self):
        old = {"expiry": "2026-11-01T00:00:00Z", "level": 1}
        self.assertTrue(n.decide(at(1), EXP, old)[1])           # 更新済み(期限が変わった)なら状態を無視

    def test_expired_daily(self):
        now = EXP + timedelta(days=1)
        today = now.astimezone(n.JST).strftime("%Y-%m-%d")
        state = {"expiry": n.iso_z(EXP), "level": "expired", "last_date": today}
        self.assertFalse(n.decide(now, EXP, state)[1])          # 同じ日は1回
        self.assertTrue(n.decide(now + timedelta(days=1), EXP, state)[1])  # 翌日は再送

    def test_too_early(self):
        self.assertEqual(n.decide(at(30), EXP, {}), (None, False))


class TestMessage(unittest.TestCase):
    def test_contents(self):
        m = n.message(at(7), EXP, 7)
        self.assertIn("あと約7日", m)
        self.assertIn("2026-12-08 09:15 JST", m)   # 日本時間で表示
        self.assertIn("DEPLOY.md", m)
        self.assertIn("GITHUB_TOKEN", m)             # Render で差し替える変数名（値ではない）
        self.assertIn("作図は影響を受けません", m)
        self.assertIn("token_expiry", m)
        self.assertNotIn("github_pat", m)           # トークンの値は絶対に含めない
        self.assertIn("nwp-xsection-api-publish", m)
        self.assertIn("期限切れ", n.message(EXP + timedelta(days=1), EXP, "expired"))


class TestRun(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._orig = n.send
        n.send = lambda text: (self.sent.append(text) or True)
        for f in Path(n.CONF).glob("*"):
            f.unlink()

    def tearDown(self):
        n.send = self._orig

    def test_missing_config(self):
        self.assertEqual(n.run(at(1)), 2)
        self.assertEqual(self.sent, [])

    def test_sends_once_then_silent(self):
        (n.CONF / "token_expiry").write_text("2026-12-08T00:15:02Z\n")
        self.assertEqual(n.run(at(7)), 0)
        self.assertEqual(len(self.sent), 1)
        n.run(at(6, 12))                      # 同じ段階(7)なら送らない
        self.assertEqual(len(self.sent), 1)
        n.run(at(3))                          # 段階が進む
        self.assertEqual(len(self.sent), 2)

    def test_dry_run_does_not_send_or_save(self):
        (n.CONF / "token_expiry").write_text("2026-12-08T00:15:02Z\n")
        n.run(at(7), dry_run=True)
        self.assertEqual(self.sent, [])
        self.assertFalse((n.CONF / "notify_state.json").exists())

    def test_failed_send_is_retried(self):
        (n.CONF / "token_expiry").write_text("2026-12-08T00:15:02Z\n")
        n.send = lambda text: False           # 送信失敗
        n.run(at(7))
        self.assertFalse((n.CONF / "notify_state.json").exists())  # 状態を更新しない=次回再送
        n.send = lambda text: (self.sent.append(text) or True)
        n.run(at(7))
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
