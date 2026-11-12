#!/usr/bin/env python
# coding: utf-8
"""ECMWF オープンデータ（IFS・AIFS）/ NOAA GFS（ともに 0.25°）から、断面図に必要な変数だけ取得して読む。

nwp_chart_web の nwp_data.py のコピーを、断面図に必要な部分だけに絞ったもの（共通部品化しない）。
どちらも「1予報時間=1ファイル」の大きなGRIB2だが、索引（ECMWF: .index、GFS: .idx）から必要な変数の
バイト範囲だけを HTTP Range で取得できる（気圧面の gh・t・u・v・r・w と地表気圧 sp だけ。約20〜40MB/ステップ）。
認証は不要。

  ECMWF: https://data.ecmwf.int/forecasts/{YYYYMMDD}/{HH}z/ifs/0p25/oper/{YYYYMMDDHH}0000-{step}h-oper-fc.{grib2,index}
         利用条件: CC-BY-4.0（出典明記）。00/12UTCは360hまで、06/18UTCは90hまで。
  AIFS : 同じ構造で ifs → aifs-single。相対湿度 r が無く比湿 q があるため、読み込み時に q から相対湿度を計算する。
  GFS  : https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{YYYYMMDD}/{HH}/atmos/gfs.t{HH}z.pgrb2.0p25.f{FFF}{,.idx}
         NOAAの成果物（米国政府、出典明記を推奨）。
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NWP-Xsection-API/0.1)"}
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"

MODELS = {
    "ecmwf": dict(label="ECMWF"),
    "gfs": dict(label="GFS"),
    "aifs": dict(label="AIFS"),
}

# ECMWFのオープンデータ配信の形式を持つモデル → 配信元のパスのモデル名
ECMWF_LIKE = {"ecmwf": "ifs", "aifs": "aifs-single"}

CC_BY_URL = "https://creativecommons.org/licenses/by/4.0/"
GFS_REGISTRY_URL = "https://registry.opendata.aws/noaa-gfs-bdp-pds/"

ECM_BASE = "https://data.ecmwf.int/forecasts"
GFS_BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"

# 断面図用の気圧面（鉛直の解像度のため多め）。ECMWFオープンデータに975は無い（存在する面だけが使われる）
PLEVELS = (1000, 975, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100)

GH_LEVEL = 500     # 高度（gh）を取る気圧面（小地図の等高線用）。他の面の高度は断面図に使わない

# 読み込み時に切り出す範囲（東経50〜185度・北緯−15〜82度）。全球のまま処理すると遅くメモリも食う
CROP = dict(lon0=50.0, lon1=185.0, lat0=-15.0, lat1=82.0)


def legal_short(model: str, year: int, accessed: str) -> str:
    """画面に常時出す短い出典表示（配信元の利用条件に沿った内容。ECMWF = CC BY 4.0、NOAA = 出典の要請）。"""
    if model in ECMWF_LIKE:
        what = "IFS open data" if model == "ecmwf" else "AIFS open data (ECMWF's machine-learning forecast model)"
        return (f"© {year} ECMWF (www.ecmwf.int). This service is based on data and products of the European Centre "
                f"for Medium-Range Weather Forecasts (ECMWF), {what}, licensed under CC BY 4.0. "
                "Modified: the data were processed into charts. "
                "ECMWF does not accept any liability for any error or omission in the data.")
    return (f"NOAA/NCEP Global Forecast System (GFS), accessed {accessed} from registry.opendata.aws/noaa-gfs-bdp-pds. "
            "Modified: the data were processed into charts and are not original NOAA data. "
            "Not endorsed by or affiliated with NOAA/NWS. Provided as is; use at your own risk.")


def parse_init(text: str) -> datetime:
    return datetime.strptime(text, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def max_step(model: str, init: datetime) -> int:
    """その初期値で提供される最大予報時間。"""
    if model == "ecmwf":
        return 360 if init.hour in (0, 12) else 90
    if model == "aifs":
        return 360
    return 384


def step_ok(model: str, init: datetime, ft: int) -> bool:
    """その予報時間が配信されているか（存在しないステップを配信元に問い合わせないための事前確認）。"""
    if ft < 0 or ft > max_step(model, init):
        return False
    if model == "aifs":
        return ft % 6 == 0
    if model == "ecmwf":
        return ft % 3 == 0 if ft <= 144 else ft % 6 == 0
    return ft % 3 == 0     # GFS は120hまで1時間毎・以降3時間毎だが、断面図は3時間毎に絞る


# ------------------------------------------------------------------ URL
def base_url(model: str, init: datetime, ft: int) -> str:
    """索引・拡張子を除いたURL。"""
    if model in ECMWF_LIKE:
        return (f"{ECM_BASE}/{init:%Y%m%d}/{init:%H}z/{ECMWF_LIKE[model]}/0p25/oper/"
                f"{init:%Y%m%d%H}0000-{ft}h-oper-fc")
    return f"{GFS_BASE}/gfs.{init:%Y%m%d}/{init:%H}/atmos/gfs.t{init:%H}z.pgrb2.0p25.f{ft:03d}"


def index_url(model: str, init: datetime, ft: int) -> str:
    return base_url(model, init, ft) + (".index" if model in ECMWF_LIKE else ".idx")


def data_url(model: str, init: datetime, ft: int) -> str:
    return base_url(model, init, ft) + (".grib2" if model in ECMWF_LIKE else "")


# ------------------------------------------------------------------ 索引 → バイト範囲
def _ecmwf_rows(model, init, ft):
    r = requests.get(index_url(model, init, ft), headers=HEADERS, timeout=60)
    r.raise_for_status()
    return [json.loads(l) for l in r.text.splitlines() if l.strip()]


def _ecmwf_select(rows, model="ecmwf", plevels=None, all_gh=False):
    """必要なメッセージの (offset, length) を返す。AIFSは相対湿度 r が無いので比湿 q も取る。"""
    pl = {str(l) for l in (plevels or PLEVELS)}
    pvars = ("t", "u", "v", "r", "w") + (("q",) if model == "aifs" else ())
    out = []
    for r in rows:
        p, lt = r["param"], r.get("levtype")
        if lt == "pl" and p in pvars and r.get("levelist") in pl:
            out.append((r["_offset"], r["_length"]))
        elif lt == "pl" and p == "gh" and (r.get("levelist") == str(GH_LEVEL) or (all_gh and r.get("levelist") in pl)):
            # 高度は小地図の500hPaだけ（取得量とメモリを減らす）。エマグラム（ホドグラフの高度）のときだけ全層
            out.append((r["_offset"], r["_length"]))
        elif lt == "sfc" and p == "sp":
            out.append((r["_offset"], r["_length"]))
    return out


def _gfs_select(init, ft, plevels=None, all_gh=False):
    r = requests.get(index_url("gfs", init, ft), headers=HEADERS, timeout=60)
    r.raise_for_status()
    lines = r.text.strip().splitlines()
    rows = []
    for i, l in enumerate(lines):
        p = l.split(":")
        off = int(p[1])
        nxt = int(lines[i + 1].split(":")[1]) if i + 1 < len(lines) else None
        rows.append((p[3], p[4], off, None if nxt is None else nxt - off))
    lv = {f"{x} mb" for x in (plevels or PLEVELS)}
    out = []
    for var, level, off, ln in rows:
        if var in ("TMP", "UGRD", "VGRD", "RH", "VVEL") and level in lv:
            out.append((off, ln))
        elif var == "HGT" and (level == f"{GH_LEVEL} mb" or (all_gh and level in lv)):   # 高度は小地図の500hPaだけ。エマグラムのときだけ全層
            out.append((off, ln))
        elif var == "PRES" and level == "surface":
            out.append((off, ln))
    return out


def _merge(ranges):
    """隣り合うバイト範囲を結合して Range 要求の回数を減らす。"""
    rs = sorted((o, l) for o, l in ranges)
    merged = []
    for o, l in rs:
        if merged and l is not None and merged[-1][1] is not None and merged[-1][0] + merged[-1][1] == o:
            merged[-1] = (merged[-1][0], merged[-1][1] + l)
        else:
            merged.append((o, l))
    return merged


def _download_ranges(url: str, ranges, dest: Path) -> Path | None:
    """指定バイト範囲を連結して dest に保存（GRIB2はメッセージ連結でそのまま読める）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.part")        # プロセスごとに別名（同じファイルを同時に取得しても、お互いの一時ファイルを壊さない）
    try:
        with open(tmp, "wb") as f:
            for off, ln in _merge(ranges):
                hdr = dict(HEADERS, Range=f"bytes={off}-{'' if ln is None else off + ln - 1}")
                for attempt in range(3):
                    try:
                        r = requests.get(url, headers=hdr, timeout=120)
                        if r.status_code in (200, 206):
                            f.write(r.content)
                            break
                        raise requests.RequestException(f"HTTP {r.status_code}")
                    except requests.RequestException:
                        if attempt == 2:
                            raise
                        time.sleep(2 * (attempt + 1))
        tmp.replace(dest)
        return dest
    except requests.RequestException as e:
        print(f"  ダウンロード失敗 {dest.name}: {e}", flush=True)
        tmp.unlink(missing_ok=True)
        return None


def grib_path(model: str, init: datetime, ft: int, data_dir: Path = DEFAULT_DATA_DIR, all_gh: bool = False) -> Path:
    """取得データの置き場。全層の高度つき（エマグラム用）は別ファイル名（_gh）にして、断面図用と混ざらないようにする。"""
    return Path(data_dir) / model / f"{init:%Y%m%d%H}" / f"f{ft:03d}{'_gh' if all_gh else ''}.grib2"


def fetch_subset(model: str, init: datetime, ft: int, data_dir: Path = DEFAULT_DATA_DIR, plevels=None, all_gh: bool = False) -> Path | None:
    """必要な変数だけを取得して data_dir/{model}/{初期値}/f{FFF}.grib2 に保存。既存ならスキップ。all_gh: 全層の高度も取る（エマグラム用）。"""
    dest = grib_path(model, init, ft, data_dir, all_gh)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        sel = (_ecmwf_select(_ecmwf_rows(model, init, ft), model, plevels, all_gh) if model in ECMWF_LIKE
               else _gfs_select(init, ft, plevels, all_gh))
    except requests.RequestException as e:
        print(f"  索引の取得に失敗 {model} FT{ft}: {e}", flush=True)
        return None
    if not sel:
        print(f"  必要な変数が索引に見つかりません {model} FT{ft}", flush=True)
        return None
    return _download_ranges(data_url(model, init, ft), sel, dest)


# ------------------------------------------------------------------ 読み込み
def _read(path: Path, plevels=None):
    """GRIB2 を走査し、気圧面の変数と地表気圧を返す。範囲を CROP に切り出す（float32）。"""
    import numpy as np
    import pygrib

    out = {"p": {}}
    lats = lons = None
    iy = ix = None
    grbs = pygrib.open(str(path))
    try:
        for m in grbs:
            sn, tl, lv = m.shortName, m.typeOfLevel, m.level
            if tl == "isobaricInhPa" and sn in ("gh", "t", "u", "v", "r", "w", "q") and lv in (plevels or PLEVELS):
                key = ("p", lv, sn)
            elif sn in ("sp", "pres") and tl == "surface":
                key = ("s", "sp")
            else:
                continue
            if lats is None:
                la, lo = m.latlons()
                la1, lo1 = la[:, 0], lo[0, :]
                iy = np.where((la1 >= CROP["lat0"]) & (la1 <= CROP["lat1"]))[0]
                ix = np.where((lo1 >= CROP["lon0"]) & (lo1 <= CROP["lon1"]))[0]
                lats, lons = la1[iy[0]:iy[-1] + 1], lo1[ix[0]:ix[-1] + 1]
                del la, lo
            # 切り出した部分は必ずコピーする（ビューのままだと、切り出し前の全球配列がメモリに残り続ける）
            v = np.array(np.asarray(m.values)[iy[0]:iy[-1] + 1, ix[0]:ix[-1] + 1], dtype=np.float32)
            if key[0] == "p":
                out["p"].setdefault(key[1], {})[key[2]] = v
            else:
                out[key[1]] = v
    finally:
        grbs.close()
    out["lats"], out["lons"] = lats, lons
    return out


def _rh_from_q(q, t_c, p_hpa: float):
    """比湿 q[kg/kg]・気温[℃]・気圧[hPa] → 相対湿度[%]。AIFS（相対湿度が無い）用（Bolton 1980）。"""
    import numpy as np
    q = np.clip(q, 0.0, None)
    e = q * p_hpa / (0.622 + 0.378 * q)
    es = 6.112 * np.exp(17.67 * t_c / (t_c + 243.5))
    return np.clip(100.0 * e / es, 0.0, 100.0).astype(np.float32)


def load_fields(model: str, path: Path, plevels=None) -> dict:
    """取得済みのGRIB2を断面図用の辞書にする。

    返り値: {"lats"(昇順),"lons","sp"(hPa),
             "p": {気圧: {"t"(℃),"u","v","r"(%),"w"(hPa/h)（500hPaだけ "gh"(m) も。エマグラム用の取得では全層）}}}
    """
    import numpy as np
    d = _read(Path(path), plevels)
    if d["lats"] is None or "sp" not in d:
        raise ValueError("GRIB2 に必要な変数（地表気圧など）がありません")
    F = {"p": {}, "lats": d["lats"], "lons": d["lons"], "sp": d["sp"] / 100.0}
    for lv, dd in d["p"].items():
        if not all(k in dd for k in ("t", "u", "v")):
            continue
        z = {"t": dd["t"] - 273.15, "u": dd["u"], "v": dd["v"]}
        if "gh" in dd:
            z["gh"] = dd["gh"]
        if "r" in dd:
            z["r"] = dd["r"]
        elif "q" in dd:                                       # AIFS: 比湿から相対湿度を計算
            z["r"] = _rh_from_q(dd["q"], dd["t"] - 273.15, float(lv))
        else:
            z["r"] = np.full_like(dd["t"], 50.0)
        z["w"] = dd["w"] * 36.0 if "w" in dd else np.zeros_like(dd["t"])   # Pa/s → hPa/h
        F["p"][lv] = z
    if F["lats"][0] > F["lats"][-1]:       # 緯度を昇順（南→北）へ
        F["lats"] = F["lats"][::-1]
        F["sp"] = F["sp"][::-1, :]
        for z in F["p"].values():
            for k in z:
                z[k] = z[k][::-1, :]
    return F


# ------------------------------------------------------------------ npz への変換（展開を別プロセスで行い、本体のメモリを抑える）
def save_npz(F: dict, dest: Path):
    """load_fields の結果を .npz に保存する（キー: lats・lons・sp・p_<気圧>_<変数>）。"""
    import numpy as np
    arrs = {"lats": F["lats"], "lons": F["lons"], "sp": F["sp"]}
    for lv, z in F["p"].items():
        for k, v in z.items():
            arrs[f"p_{lv}_{k}"] = np.ascontiguousarray(v, dtype=np.float32)
    tmp = dest.with_suffix(".tmp.npz")
    np.savez(tmp, **arrs)
    tmp.replace(dest)


def load_npz(path: Path) -> dict:
    """save_npz で保存したものを、load_fields と同じ形の辞書に戻す。"""
    import numpy as np
    F = {"p": {}}
    with np.load(path) as z:
        for key in z.files:
            if key.startswith("p_"):
                _, lv, k = key.split("_", 2)
                F["p"].setdefault(int(lv), {})[k] = z[key]
            else:
                F[key] = z[key]
    return F


def _main(argv):
    """python nwp_data.py pack <model> <grib2> <npz> : GRIB2 を展開して npz にする（本体から別プロセスで呼ぶ）。"""
    if len(argv) == 5 and argv[1] == "pack":
        save_npz(load_fields(argv[2], Path(argv[3])), Path(argv[4]))
        return 0
    print(_main.__doc__)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv))
