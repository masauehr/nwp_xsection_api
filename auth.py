#!/usr/bin/env python
# coding: utf-8
"""合言葉による簡易認証（公開操作用）。

環境変数 API_KEYS に「呼び名:合言葉のSHA-256（16進）」をカンマ区切りで並べる。例: yamada:ab12…,sato:cd34…
合言葉そのものは保存しない（環境変数が漏れても合言葉は分からない）。作図は認証なし・公開（GitHubへの掲載）だけが対象。
合言葉の発行は tools/make_key.py（ランダムな合言葉とその行を表示する）。
"""
import hashlib
import hmac
import re


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def parse_keys(text: str) -> dict:
    """"名前:ハッシュ,名前:ハッシュ" → {ハッシュ: 名前}。形式が不正な項目は無視する（空白は許す）。"""
    out = {}
    for item in (text or "").split(","):
        item = item.strip()
        m = re.fullmatch(r"([^:\s]{1,32}):([0-9a-fA-F]{64})", item)
        if m:
            out[m.group(2).lower()] = m.group(1)
    return out


def check(header: str | None, keys: dict) -> str | None:
    """Authorization ヘッダ（"Bearer 合言葉"）を確認して呼び名を返す。合わなければ None。"""
    if not header or not keys:
        return None
    m = re.fullmatch(r"Bearer\s+(\S{1,200})", header.strip())
    if not m:
        return None
    h = hash_key(m.group(1))
    found = None
    for k, name in keys.items():          # 全件を比較する（一致した時点で止めない）
        if hmac.compare_digest(k, h):
            found = name
    return found
