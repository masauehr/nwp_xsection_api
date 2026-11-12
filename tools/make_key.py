#!/usr/bin/env python
# coding: utf-8
"""公開用の合言葉を作る。ランダムな合言葉（相手に渡す）と、Render の API_KEYS に足す行（ハッシュ）を表示する。

使い方: python tools/make_key.py <呼び名>     例: python tools/make_key.py tanaka
合言葉は一度だけ表示される（保存されない）。ハッシュの行を環境変数 API_KEYS にカンマ区切りで足す。
呼び名は公開サイトの一覧にも載る（誰が公開したかの目印）ので、本名でなくニックネームにする。
"""
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import auth


def main(argv):
    if len(argv) != 2 or not re.fullmatch(r"[A-Za-z0-9_\-]{1,32}", argv[1]):
        print(__doc__)
        return 2
    key = secrets.token_urlsafe(24)       # 約192ビット
    print(f"合言葉（相手に渡す。この画面にしか出ません）: {key}")
    print(f"API_KEYS に足す項目: {argv[1]}:{auth.hash_key(key)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
