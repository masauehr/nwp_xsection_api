# エマグラムのテスト（合成データで実際に描画する。MetPy が要る）
from datetime import datetime, timezone

import numpy as np
import pytest

import engine
import ghpub
import nwp_data

pytest.importorskip("metpy")
import emagram  # noqa: E402

NOW = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
Q = dict(lat="35.7", lon="139.7")


def synthetic_F(gh_all=True):
    lats = np.arange(0.0, 60.01, 0.5)
    lons = np.arange(100.0, 160.01, 0.5)
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    p = {}
    for lv, t0, h in ((1000, 20.0, 100.0), (925, 16.0, 760.0), (850, 10.0, 1450.0), (700, 0.0, 3000.0), (500, -20.0, 5700.0),
                      (400, -32.0, 7200.0), (300, -45.0, 9300.0), (250, -50.0, 10400.0), (200, -55.0, 11800.0), (150, -58.0, 13500.0), (100, -60.0, 16200.0)):
        z = dict(t=t0 - 0.2 * (la - 30), u=20.0 + 0 * la, v=5.0 * np.sin(np.deg2rad(lo * 6)),
                 r=np.clip(70 + 25 * np.sin(np.deg2rad(la * 8)), 5, 100), w=-15.0 * np.sin(np.deg2rad(la * 6)))
        if gh_all or lv == 500:
            z["gh"] = h + 20 * (la - 30)
        p[lv] = z
    return dict(lats=lats, lons=lons, sp=np.full(la.shape, 1005.0), p=p)


def test_validate_emagram():
    p = engine.validate("gfs", "2026111100", "12", Q, now=NOW, kind="emagram")
    assert (p["kind"], p["lat"], p["lon"]) == ("emagram", 35.7, 139.7)
    assert engine.describe(p) == "エマグラム (35.7N,139.7E)"
    assert engine.image_name(p).startswith("emagram_gfs_2026111100_012_") and engine.NAME_RE.fullmatch(engine.image_name(p))
    for bad in (dict(lat="abc", lon="139"), dict(lat="35", lon="10"), dict(lat="95", lon="139"), dict(lat="35")):
        with pytest.raises(engine.CustomError):
            engine.validate("gfs", "2026111100", "12", bad, now=NOW, kind="emagram")
    with pytest.raises(engine.CustomError):
        engine.validate("gfs", "2026111100", "12", Q, now=NOW, kind="hodograph")


def test_kinds_do_not_share_image_names():
    q = dict(lat1="45", lon1="130", lat2="25", lon2="130", lat="35", lon="135")
    a = engine.validate("gfs", "2026111100", "12", q, now=NOW, kind="xsection")
    b = engine.validate("gfs", "2026111100", "12", q, now=NOW, kind="emagram")
    assert engine.image_name(a) != engine.image_name(b)


def test_emagram_names_can_be_published():
    assert ghpub.NAME_RE.fullmatch("emagram_ecmwf_2026111118_012_0123456789ab.png")
    assert not ghpub.NAME_RE.fullmatch("emagram_ecmwf_2026111118_012_0123456789ab.json")
    assert not ghpub.NAME_RE.fullmatch("hodograph_ecmwf_2026111118_012_0123456789ab.png")


def test_all_levels_height_is_fetched_only_for_emagram():
    """断面図は 500hPa の高度だけ、エマグラムは全層の高度も取る（取得ファイルも別名）。"""
    rows = [dict(param=p, levtype="pl", levelist=str(l), _offset=i, _length=1)
            for i, (p, l) in enumerate((("gh", 500), ("gh", 850), ("gh", 300), ("t", 850)))]
    assert sorted(o for o, _ in nwp_data._ecmwf_select(rows, "ecmwf", nwp_data.PLEVELS)) == [0, 3]
    assert sorted(o for o, _ in nwp_data._ecmwf_select(rows, "ecmwf", nwp_data.PLEVELS, all_gh=True)) == [0, 1, 2, 3]
    i = nwp_data.parse_init("2026111100")
    assert nwp_data.grib_path("gfs", i, 12, all_gh=True).name == "f012_gh.grib2" and nwp_data.grib_path("gfs", i, 12).name == "f012.grib2"
    assert engine.npz_path("gfs", i, 12, True) != engine.npz_path("gfs", i, 12)


def test_profile_and_map_extent():
    prof = emagram.profile_from_F(synthetic_F(), 35.7, 139.7)
    assert prof["p"][0] == 1000 and len(prof["p"]) >= 8 and np.isfinite(prof["h"]).all()
    assert abs(prof["grid_lat"] - 35.5) < 0.6 or abs(prof["grid_lat"] - 36.0) < 0.6
    w, e, s, n = emagram.map_extent(35.7, 139.7)
    assert w == pytest.approx(127.7) and e == pytest.approx(151.7) and s < 35.7 < n


def test_profile_needs_three_levels_above_ground():
    F = synthetic_F()
    F["sp"] = np.full(F["sp"].shape, 600.0)               # 高い山の上（地表気圧 600hPa）
    p = emagram.profile_from_F(F, 35.7, 139.7)
    assert p["p"][0] <= 600
    F["sp"] = np.full(F["sp"].shape, 150.0)
    with pytest.raises(ValueError):
        emagram.profile_from_F(F, 35.7, 139.7)


def test_emagram_renders(tmp_path):
    out = tmp_path / "em.png"
    r = emagram.render_emagram(synthetic_F(), datetime(2026, 11, 10, 18), 12, 35.7, 139.7, str(out), model="TEST")
    assert out.exists() and out.stat().st_size > 20_000
    assert np.isfinite(r["cape"]) and np.isfinite(r["cin"])
