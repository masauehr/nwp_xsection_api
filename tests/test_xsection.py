# 断面図の描画・相当温位・表示要素のテスト（合成データで実際に描画する）
from datetime import datetime

import numpy as np
import pytest

import xsection as xs


def synthetic_F():
    lats = np.arange(0.0, 60.01, 0.5)
    lons = np.arange(100.0, 160.01, 0.5)
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    p = {}
    for lv, t0 in ((1000, 20.0), (850, 10.0), (700, 0.0), (500, -20.0), (300, -45.0), (200, -55.0), (100, -60.0)):
        p[lv] = dict(t=t0 - 0.2 * (la - 30), u=20.0 + 0 * la, v=5.0 * np.sin(np.deg2rad(lo * 6)), r=np.clip(70 + 25 * np.sin(np.deg2rad(la * 8)), 5, 100),
                     w=-15.0 * np.sin(np.deg2rad(la * 6)) * np.cos(np.deg2rad(lo * 5)), gh=5400.0 + 20 * (la - 30))
    return dict(lats=lats, lons=lons, sp=np.full(la.shape, 1000.0), p=p)


def test_defaults_keep_the_original_look():
    assert xs.normalize_opts() == {"shade": "div", "contours": ("theta", "ept", "wind"), "barbs": "front", "ptop": 100}


def test_normalize_orders_contours_and_validates():
    assert xs.normalize_opts({"contours": ["ept", "theta"]})["contours"] == ("theta", "ept")
    for bad in ({"shade": "x"}, {"barbs": "x"}, {"contours": ["theta", "foo"]}):
        with pytest.raises(ValueError):
            xs.normalize_opts(bad)


def test_ept_matches_metpy():
    """numpy で実装した相当温位が MetPy と 0.3K 以内で一致する（MetPy が無い環境では飛ばす）。"""
    mpcalc = pytest.importorskip("metpy.calc")
    from metpy.units import units
    p = np.array([1000.0, 925.0, 850.0, 700.0, 500.0, 400.0, 300.0])
    t = np.array([28.0, 22.0, 15.0, 5.0, -15.0, -28.0, -42.0])
    rh = np.array([85.0, 70.0, 60.0, 50.0, 40.0, 30.0, 20.0])
    td = xs._dewpoint(t, rh)
    ref = mpcalc.equivalent_potential_temperature(p * units.hPa, t * units.degC, np.minimum(td, t) * units.degC).to("K").m
    assert np.abs(xs._ept(p, t, td) - ref).max() < 0.3


def test_ept_is_sane():
    """乾燥より湿った空気のほうが相当温位が高く、値は 250〜450K の現実的な範囲。"""
    dry = xs._ept(850.0, 10.0, xs._dewpoint(10.0, 20.0))
    wet = xs._ept(850.0, 10.0, xs._dewpoint(10.0, 95.0))
    assert wet > dry and 250 < dry < 450 and 250 < wet < 450


@pytest.mark.parametrize("opts", [
    None,
    dict(shade="rh", contours=["theta", "ept"], barbs="back"),
    dict(shade="omega", contours=["ept"], barbs="back"),
    dict(shade="none", contours=[], barbs="off"),
    dict(shade="div", contours=["wind"], barbs="front"),
])
def test_every_combination_renders(tmp_path, opts):
    out = tmp_path / "xs.png"
    xs.render_xsection(synthetic_F(), datetime(2026, 11, 10, 18), 12, (45.0, 130.0), (25.0, 130.0), str(out),
                       model="TEST", name="断面テスト", opts=opts)
    assert out.exists() and out.stat().st_size > 10_000


def test_east_west_section_renders(tmp_path):
    out = tmp_path / "ew.png"
    xs.render_xsection(synthetic_F(), datetime(2026, 11, 10, 18), 0, (35.0, 120.0), (35.0, 150.0), str(out), model="TEST")
    assert out.exists()


def test_section_has_humidity_and_omega_masked_above_300():
    S = xs.compute_section(synthetic_F(), (45.0, 130.0), (25.0, 130.0))
    lev = list(S["levels"])
    assert np.isfinite(S["rh"][lev.index(500.0)]).all()
    assert np.isnan(S["rh"][lev.index(200.0)]).all()
    assert np.isfinite(S["w"]).all()


def test_coast_lines_do_not_wrap_around_the_map():
    """経度が360→0に折り返す海岸線は、そこで切る（小地図に横線が走らない）。"""
    lines = xs._coast_lines()
    assert lines, "assets/coast.json が読めていません"
    assert max(np.abs(np.diff(lon)).max() for lon, _ in lines if len(lon) > 1) <= 180.0


def test_parse_ptop():
    """高度設定（上端の気圧）: 未指定は100、選択肢以外は拒否する。"""
    assert xs.parse_ptop(None) == 100 and xs.parse_ptop("") == 100 and xs.parse_ptop("300") == 300 and xs.parse_ptop(500.0) == 500
    for bad in ("abc", "50", "1000", "250.5x", "-100", "nan"):
        with pytest.raises(ValueError):
            xs.parse_ptop(bad)
    assert xs.options_text({"ptop": 100}) == xs.options_text()                       # 既定なら説明は増えない
    assert xs.options_text({"ptop": 300}).endswith("／上端300hPa")


@pytest.mark.parametrize("ptop", [200, 300, 500, 700])
def test_section_is_limited_to_ptop(tmp_path, ptop):
    """上端までの層だけで計算し、縦軸も上端まで。描画も通る。"""
    S = xs.compute_section(synthetic_F(), (45.0, 130.0), (25.0, 130.0), ptop)
    assert S["levels"].min() == ptop and S["levels"].max() == 1000.0
    assert S["fine"]["levels"].min() == pytest.approx(ptop)                           # 細かい層も上端まで（範囲内の解像度が上がる）
    out = tmp_path / "xs.png"
    xs.render_xsection(synthetic_F(), datetime(2026, 11, 10, 18), 12, (45.0, 130.0), (25.0, 130.0), str(out),
                       model="TEST", opts=dict(ptop=ptop, shade="rh"))
    assert out.exists() and out.stat().st_size > 10_000


def test_section_needs_two_levels():
    F = synthetic_F()
    F["p"] = {k: v for k, v in F["p"].items() if k in (1000, 100)}
    with pytest.raises(ValueError):
        xs.compute_section(F, (45.0, 130.0), (25.0, 130.0), 500)                      # 500hPa 以下の層が1つしかない
