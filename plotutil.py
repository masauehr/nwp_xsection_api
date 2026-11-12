#!/usr/bin/env python
# coding: utf-8
"""断面図・エマグラムが共有する描画の補助（matplotlib の設定・日本語フォント・海岸線・見出し・PNG の減色）。"""
import json
import os
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")      # スレッド競合を避ける（numpy を import する前に設定）
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import matplotlib
matplotlib.use("Agg")
import numpy as np

DPI = 100
KT = 1.94384
FONTS = ["Hiragino Sans", "Noto Sans CJK JP", "IPAGothic", "IPAexGothic", "Arial Unicode MS", "DejaVu Sans"]   # 日本語が出る順（Mac→Linux）
COAST_PATH = Path(__file__).resolve().parent / "assets" / "coast.json"


_COAST = None


def _coast_lines():
    """assets/coast.json の海岸線を [(経度の配列, 緯度の配列), ...] で返す（初回だけ読む。経度は0〜360）。"""
    global _COAST
    if _COAST is None:
        try:
            lines = json.loads(COAST_PATH.read_text(encoding="utf-8"))["lines"]
        except (OSError, ValueError, KeyError):
            lines = []
        _COAST = []
        for l in lines:
            lon, lat = np.array(l[0::2]), np.array(l[1::2])
            cut = np.where(np.abs(np.diff(lon)) > 180.0)[0] + 1       # 経度が360→0に折り返す所で線を切る（横線を防ぐ）
            for a, b in zip(np.split(lon, cut), np.split(lat, cut)):
                if len(a) >= 2:
                    _COAST.append((a, b))
    return _COAST


def _caption(fig, parts, y, fw, fh=None):
    """色の違う文字を横に並べる（実際の描画幅で位置を決める）。fw: 図の幅[インチ]。"""
    r = fig.canvas.get_renderer()
    x = 4 / (fw * DPI)
    for text, color, fs in parts:
        t = fig.text(x, y, text, color=color, fontsize=fs, family="serif", va="center", ha="left")
        bb = t.get_window_extent(r)
        x += bb.width / (fw * DPI) + 8 / (fw * DPI)


def _quantize_png(path):
    """PNGを256色パレットへ減色して容量を約1/3にする（線画・塗り主体なので見た目はほぼ変わらない）。"""
    try:
        from PIL import Image
    except ImportError:
        return
    im = Image.open(path).convert("RGB")
    im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)
