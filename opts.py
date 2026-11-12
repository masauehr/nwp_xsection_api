#!/usr/bin/env python
# coding: utf-8
"""断面図の表示要素（色塗り・等値線・風矢羽）の指定。numpy・matplotlib を読み込まない軽いモジュール
（サーバー本体が入力検証のたびに重いライブラリを読み込まないように、xsection.py から分けてある）。"""

# 表示要素（自由選択で選べる）。指定が無ければ従来の見た目（発散の色塗り＋温位・相当温位・断面平行風の線＋前面の風矢羽）
SHADES = {"none": "なし", "div": "発散", "rh": "湿り（相対湿度）", "omega": "上昇流（ω）"}   # 色塗り。1つだけ選ぶ
CONTOURS = {"theta": "温位", "ept": "相当温位", "wind": "断面平行風"}                          # 等値線。複数選べる
BARBS = {"off": "なし", "back": "背景（薄い灰色）", "front": "前面（黒）"}                      # 風矢羽
DEFAULT_OPTS = {"shade": "div", "contours": ("theta", "ept", "wind"), "barbs": "front"}


def normalize_opts(opts=None):
    """表示要素の指定を検証して、揃った形（shade・contours・barbs）にする。不正な値は ValueError。"""
    o = dict(DEFAULT_OPTS)
    o.update({k: v for k, v in (opts or {}).items() if v is not None})
    if o["shade"] not in SHADES:
        raise ValueError(f"色塗りの指定が不正です: {o['shade']}")
    if o["barbs"] not in BARBS:
        raise ValueError(f"風矢羽の指定が不正です: {o['barbs']}")
    cs = [o["contours"]] if isinstance(o["contours"], str) else list(o["contours"])
    bad = [c for c in cs if c not in CONTOURS]
    if bad:
        raise ValueError(f"等値線の指定が不正です: {bad[0]}")
    o["contours"] = tuple(c for c in CONTOURS if c in cs)       # 並びを固定（同じ指定は同じ画像・同じ名前になる）
    return o


def options_text(opts=None):
    """画面に出す短い説明（例: 温位・相当温位／湿り（相対湿度）／風矢羽:背景（薄い灰色））。"""
    o = normalize_opts(opts)
    parts = ["・".join(CONTOURS[c] for c in o["contours"]) or "等値線なし"]
    if o["shade"] != "none":
        parts.append(SHADES[o["shade"]])
    parts.append("風矢羽:" + BARBS[o["barbs"]] if o["barbs"] != "off" else "風矢羽なし")
    return "／".join(parts)
