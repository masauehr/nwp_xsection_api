# テスト共通の設定（作業フォルダをテストごとの一時フォルダにする）
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("WORK_DIR", tempfile.mkdtemp(prefix="nwp_xsection_test_"))
