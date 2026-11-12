# Dockerfile が、アプリに必要なファイルを全部コピーしているかの確認（コピー漏れは Render で起動時に落ちて初めて分かるため）
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def copied_files():
    """COPY の左側（コピー元）に書かれたファイル・フォルダ名。"""
    names = set()
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if line.startswith("COPY "):
            names.update(line.split()[1:-1])        # 最後の語はコピー先
    return names


def test_all_top_level_modules_are_copied():
    """リポジトリ直下の .py（テスト・tools は別）は、すべてイメージに入っている。"""
    on_disk = {p.name for p in ROOT.glob("*.py")}
    assert on_disk <= copied_files(), f"Dockerfile の COPY に無い: {sorted(on_disk - copied_files())}"


def test_assets_are_copied():
    assert "assets" in copied_files() and (ROOT / "assets" / "coast.json").exists()
