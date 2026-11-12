# 入力の検証・画像名・キャッシュ・データ変換のテスト（ネットワークは使わない）
from datetime import datetime, timezone

import numpy as np
import pytest

import engine
import nwp_data

NOW = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
Q = dict(lat1="45", lon1="130", lat2="25", lon2="130")


def v(model="gfs", init="2026111100", ft="12", q=None, now=NOW):
    return engine.validate(model, init, ft, q or Q, now=now)


def test_valid_defaults():
    p = v()
    assert (p["model"], p["init"], p["ft"]) == ("gfs", "2026111100", 12)
    assert (p["shade"], p["contours"], p["barbs"]) == ("div", "theta,ept,wind", "front")


@pytest.mark.parametrize("model,init,ft,why", [
    ("jra55", "2026111100", "12", "モデル"), ("gsm", "2026111100", "12", "モデル"), (None, "2026111100", "12", "モデル"),
    ("gfs", "20261111", "12", "YYYYMMDDHH"), ("gfs", "2026111103", "12", "00/06/12/18"), ("gfs", "2026133100", "12", "日時"),
    ("gfs", "2026111300", "12", "未来"), ("gfs", "2026010100", "12", "古すぎ"),
    ("gfs", "2026111100", "13", "予報時間"), ("gfs", "2026111100", "-3", "範囲"), ("gfs", "2026111100", "abc", "数値"),
    ("aifs", "2026111100", "9", "予報時間"), ("ecmwf", "2026111106", "96", "予報時間"),
])
def test_rejects_bad_input(model, init, ft, why):
    with pytest.raises(engine.CustomError) as e:
        v(model, init, ft)
    assert why in str(e.value)


@pytest.mark.parametrize("bad", [
    dict(lat1="abc"), dict(lat1="nan"), dict(lat1="inf"), dict(lon1="10"), dict(lon2="200"), dict(lat2="90"),
    dict(lat2="45.2", lon2="130.2"),      # 近すぎる
    dict(lat2="-10", lon2="185", lat1="82", lon1="50"),   # 長すぎる
    dict(shade="x"), dict(contours="theta,foo"), dict(barbs="x"),
])
def test_rejects_bad_section(bad):
    with pytest.raises(engine.CustomError):
        v(q={**Q, **bad})


def test_step_rules():
    i = nwp_data.parse_init("2026111100")
    assert nwp_data.step_ok("gfs", i, 0) and nwp_data.step_ok("gfs", i, 384)
    assert nwp_data.step_ok("ecmwf", i, 144) and not nwp_data.step_ok("ecmwf", i, 147) and nwp_data.step_ok("ecmwf", i, 150)
    assert not nwp_data.step_ok("aifs", i, 3) and nwp_data.step_ok("aifs", i, 6)
    assert not nwp_data.step_ok("ecmwf", nwp_data.parse_init("2026111106"), 93)


def test_image_name_is_stable_and_distinct():
    a, b = v(), v(q={**Q, "shade": "omega"})
    assert engine.image_name(a) == engine.image_name(v())
    assert engine.image_name(a) != engine.image_name(b)
    assert engine.NAME_RE.fullmatch(engine.image_name(a))


def test_name_regex_rejects_path_tricks():
    for bad in ("../x.png", "xsection_gfs_2026111100_012_abc.png", "x/xsection_gfs_2026111100_012_0123456789ab.png",
                "xsection_gfs_2026111100_012_0123456789ab.png/../a", "xsection_gfs_2026111100_012_0123456789ab.json"):
        assert not engine.NAME_RE.fullmatch(bad)


def test_prune_cache_keeps_newest_and_removes_json(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(engine, "CACHE_MAX_FILES", 2)
    for i in range(4):
        f = tmp_path / f"xsection_gfs_2026111100_{i:03d}_0123456789ab.png"
        f.write_bytes(b"x")
        f.with_suffix(".json").write_text("{}")
        os.utime(f, (1000 + i, 1000 + i))
    engine.prune_cache()
    assert sorted(f.name for f in tmp_path.glob("*.png")) == [f"xsection_gfs_2026111100_{i:03d}_0123456789ab.png" for i in (2, 3)]
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_prune_grib_by_size(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(engine, "GRIB_DIR", tmp_path)
    monkeypatch.setattr(engine, "GRIB_MAX_BYTES", 250)
    for i in range(4):
        d = tmp_path / "gfs" / "2026111100"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"f{i:03d}.npz"
        f.write_bytes(b"x" * 100)
        os.utime(f, (1000 + i, 1000 + i))
    engine.prune_grib()
    assert sorted(f.name for f in tmp_path.glob("*/*/*.npz")) == ["f002.npz", "f003.npz"]


def test_npz_roundtrip(tmp_path):
    lats, lons = np.arange(0.0, 5.0), np.arange(100.0, 106.0)
    F = dict(lats=lats, lons=lons, sp=np.full((5, 6), 1000.0),
             p={850: {k: np.ones((5, 6), dtype=np.float32) * i for i, k in enumerate(("gh", "t", "u", "v", "r", "w"))}})
    nwp_data.save_npz(F, tmp_path / "a.npz")
    G = nwp_data.load_npz(tmp_path / "a.npz")
    assert set(G["p"]) == {850} and set(G["p"][850]) == {"gh", "t", "u", "v", "r", "w"}
    assert np.allclose(G["p"][850]["r"], 4) and np.allclose(G["sp"], 1000.0) and np.allclose(G["lats"], lats)


def test_rh_from_q_is_sane():
    rh = nwp_data._rh_from_q(np.array([0.01, 0.0, 0.5]), 20.0, 1000.0)
    assert 60 < rh[0] < 80 and rh[1] == 0.0 and rh[2] == 100.0


def test_legal_text_mentions_source():
    assert "CC BY 4.0" in nwp_data.legal_short("ecmwf", 2026, "2026-11-12")
    assert "CC BY 4.0" in nwp_data.legal_short("aifs", 2026, "2026-11-12")
    assert "NOAA" in nwp_data.legal_short("gfs", 2026, "2026-11-12")


def test_child_main_protocol(capsys):
    """別プロセス側の入口: 成功は 0、入力・データの問題は 3 とメッセージ（JSON）、使い方の誤りは 2。"""
    assert engine._child_main(["engine.py", "render", '{"a": 1}'], render_fn=lambda p: None) == 0

    def bad(p):
        raise engine.CustomError("データを取得できません")
    assert engine._child_main(["engine.py", "render", "{}"], render_fn=bad) == 3
    assert "データを取得できません" in capsys.readouterr().out
    assert engine._child_main(["engine.py"]) == 2


def test_server_side_modules_stay_light():
    """サーバー本体（app・engine の検証）は numpy・matplotlib を読み込まない（メモリ節約）。"""
    import subprocess
    import sys
    code = ("import sys; sys.path.insert(0, '.'); import app, engine, opts; "
            "engine.validate('gfs', '2026111100', '12', dict(lat1='45', lon1='130', lat2='25', lon2='130'), "
            "now=__import__('datetime').datetime(2026, 11, 12, tzinfo=__import__('datetime').timezone.utc)); "
            "print([m for m in ('numpy', 'matplotlib', 'scipy') if m in sys.modules])")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    assert r.stdout.strip() == "[]", r.stdout + r.stderr


def test_only_500hpa_height_is_selected():
    """高度(gh)は500hPaだけ取る（取得量とメモリを減らす）。ECMWF の索引・GFS の索引（偽の内容）で確認する。"""
    rows = [dict(param=p, levtype="pl", levelist=str(l), _offset=i, _length=1)
            for i, (p, l) in enumerate((("gh", 500), ("gh", 850), ("t", 850), ("t", 500), ("gh", 300)))]
    rows.append(dict(param="sp", levtype="sfc", _offset=9, _length=1))
    rows.append(dict(param="msl", levtype="sfc", _offset=10, _length=1))
    sel = nwp_data._ecmwf_select(rows, "ecmwf", nwp_data.PLEVELS)
    assert sorted(o for o, _ in sel) == [0, 2, 3, 9]            # gh500・t850・t500・sp だけ（gh850・gh300・msl は取らない）


def test_children_peak_mb_after_a_child_runs():
    """子プロセスを動かした後は、最大メモリ[MB]が取れる（Linux は KB・macOS は bytes の違いを吸収する）。"""
    import subprocess
    import sys
    subprocess.run([sys.executable, "-c", "x = bytearray(30 * 1024 * 1024)"], check=True)
    peak = engine.children_peak_mb()
    assert peak is not None and 30 < peak < 400
