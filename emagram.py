#!/usr/bin/env python
# coding: utf-8
"""エマグラム（気温・露点・持ち上げ気塊・CAPE/CIN・ホドグラフ）。

tenkizu の GRIB2_Emagram.py の描画内容を、格子データ F（nwp_data.load_fields の形式）から作る。
最寄りの格子点の鉛直プロファイルを取り出し、log(気圧) で細かい層に補間してから描く
（取得している気圧面は 975/925/850/700/500/300/200hPa と少なく、そのままでは CAPE が粗いため）。
nwp_chart_web の emagram.py のコピーを調整したもの（共通部品化しない）。任意の緯度経度で作る。
軽量化のため、小地図は cartopy を使わず assets/coast.json で描く（MetPy は SkewT・CAPE の計算に必要なので使う）。
取得は全層の高度つき（nwp_data の all_gh。ホドグラフを高度で色分けするため）。
"""
from datetime import timedelta

import numpy as np
import matplotlib.pyplot as plt

from plotutil import DPI, FONTS, KT, _caption, _coast_lines, _quantize_png

def _dewpoint(t_c, rh):
    rh = np.clip(rh, 1.0, 100.0)
    a, b = 17.62, 243.12
    g = np.log(rh / 100.0) + a * t_c / (b + t_c)
    return b * g / (a - g)


def nearest(F, lat, lon):
    """最寄りの格子点の (iy, ix) と、その緯度・経度。"""
    lats, lons = F["lats"], F["lons"]
    iy = int(np.abs(lats - lat).argmin())
    ix = int(np.abs(lons - (lon % 360.0)).argmin())
    return iy, ix, float(lats[iy]), float(lons[ix])


def profile_from_F(F, lat, lon):
    """最寄り格子点のプロファイル（地面より上の気圧面だけ）。p[hPa] 下→上、T/Td[℃]、u/v[m/s]、h[m]。"""
    iy, ix, glat, glon = nearest(F, lat, lon)
    sp = float(F["sp"][iy, ix])
    rows = []
    for lv in sorted(F["p"], reverse=True):
        if lv > sp:
            continue                      # 地面より下の層は使わない
        d = F["p"][lv]
        t = float(d["t"][iy, ix])
        td = min(float(_dewpoint(t, float(d["r"][iy, ix]))), t) if lv >= 300 else np.nan   # 上空の湿度は信頼できない
        rows.append((lv, t, td, float(d["u"][iy, ix]), float(d["v"][iy, ix]), float(d["gh"][iy, ix])))
    if len(rows) < 3:
        raise ValueError("地面より上の気圧面が3層未満です（高い山の上など）")
    a = np.array(rows)
    return dict(p=a[:, 0], T=a[:, 1], Td=a[:, 2], u=a[:, 3], v=a[:, 4], h=a[:, 5],
                sp=sp, grid_lat=glat, grid_lon=glon)


def refine(prof, step=10.0):
    """log(気圧) で線形補間した細かい層（診断・曲線用）。下端は最下層、上端は最上層。"""
    p = prof["p"]
    pf = np.arange(p[0], p[-1] - 0.5, -step)
    lp, lpf = np.log(p)[::-1], np.log(pf)[::-1]
    out = {"p": pf}
    for k in ("T", "Td", "u", "v"):
        y = prof[k][::-1]
        ok = np.isfinite(y)
        f = np.interp(lpf, lp[ok], y[ok], left=np.nan, right=np.nan)      # 有効な層の範囲だけ補間（外は NaN）
        out[k] = f[::-1]
    return out


def diagnostics(fine):
    """最大相当温位の層（850hPa以下）から持ち上げた気塊の LCL・EL・CAPE・CIN。"""
    import metpy.calc as mpcalc
    from metpy.units import units
    td_fill = np.where(np.isfinite(fine["Td"]), fine["Td"], fine["T"])        # 露点が無い上空は飽和とみなす（気塊の診断に影響しない高さ）
    p, T, Td = fine["p"] * units.hPa, fine["T"] * units.degC, td_fill * units.degC
    low = np.where(fine["p"] >= 850.0)[0]
    if low.size == 0:
        low = np.array([0])
    ept = mpcalc.equivalent_potential_temperature(p[low], T[low], Td[low])
    i0 = int(low[int(np.nanargmax(ept.magnitude))])
    pe, te, tde = p[i0:], T[i0:], Td[i0:]
    prof = mpcalc.parcel_profile(pe, T[i0], Td[i0]).to("degC")
    lcl_p, lcl_t = mpcalc.lcl(p[i0], T[i0], Td[i0])
    try:
        el_p, el_t = mpcalc.el(pe, te, tde, prof)
    except Exception:
        el_p = el_t = None
    cape, cin = mpcalc.cape_cin(pe, te, tde, prof, which_lfc="bottom", which_el="top")
    return dict(i0=i0, parcel=prof, pe=pe, te=te, lcl_p=lcl_p, lcl_t=lcl_t, el_p=el_p, el_t=el_t,
                cape=float(cape.magnitude), cin=float(cin.magnitude))


def map_extent(lat, lon, dlon=12.0, dlat=8.0):
    """地点を中心にした小地図の範囲 (経度の西端, 東端, 緯度の南端, 北端)。緯度は ±90 度を超えないようにする。"""
    return (lon - dlon, lon + dlon, max(-90.0, lat - dlat), min(90.0, lat + dlat))


def render_emagram(F, init, ft, lat, lon, outpath, model="", name="", quantize=True, ptop=100):
    """エマグラムを1枚描画して outpath へ保存する。"""
    from metpy.plots import Hodograph, SkewT
    from metpy.units import units
    from mpl_toolkits.axes_grid1.inset_locator import inset_axes
    prof = profile_from_F(F, lat, lon)
    fine = refine(prof)
    dg = diagnostics(fine)
    W, H = 9.5, 7.6
    fig = plt.figure(figsize=(W, H), dpi=DPI)
    fig.subplots_adjust(top=0.90, bottom=0.115, left=0.09, right=0.78)   # 右に地点の地図を置く
    skew = SkewT(fig, rotation=0, aspect=120)
    skew.ax.set_xlim(-70, 40)
    skew.ax.set_ylim(1000, ptop)                 # 高度設定: 表示する範囲の上端（CAPE・CIN は全層の気塊で計算するので変わらない）
    skew.ax.set_xlabel("Temperature (°C)")
    skew.ax.set_ylabel("Pressure (hPa)")
    p, T = fine["p"] * units.hPa, fine["T"] * units.degC
    Td_plot = fine["Td"] * units.degC            # 300hPaより上は NaN（線が途切れる）

    skew.plot_dry_adiabats(t0=np.arange(203, 533, 10) * units.K, alpha=0.25, color="orangered")
    skew.plot_moist_adiabats(t0=np.arange(203, 400, 5) * units.K, alpha=0.25, color="tab:green")
    skew.plot_mixing_lines(pressure=np.arange(1000, 99, -20) * units.hPa, linestyle="dotted", color="tab:blue")
    skew.plot(p, T, "r", linewidth=1.8, label="気温")
    skew.plot(p, Td_plot, "g", linewidth=1.8, label="露点温度")
    skew.plot(dg["pe"], dg["parcel"], "k", linewidth=2, alpha=0.55, label="持ち上げ気塊")
    skew.plot(p[dg["i0"]], T[dg["i0"]], "ko", markerfacecolor="black")
    skew.plot(dg["lcl_p"], dg["lcl_t"], "ko", markerfacecolor="blue")
    if dg["el_p"] is not None and np.isfinite(dg["el_p"].magnitude):
        skew.plot(dg["el_p"], dg["el_t"], "ko", markerfacecolor="red")
    if dg["cape"] > 0:                    # 自由対流高度が無い（安定）場合は CIN の塗りが広範囲になり誤解を招くので塗らない
        try:
            skew.shade_cin(dg["pe"], dg["te"], dg["parcel"])
            skew.shade_cape(dg["pe"], dg["te"], dg["parcel"])
        except Exception:
            pass
    # 風矢羽は取得している気圧面のところだけ（kt）
    skew.plot_barbs(prof["p"] * units.hPa, (prof["u"] * KT) * units.knots, (prof["v"] * KT) * units.knots,
                    y_clip_radius=0.03)
    skew.ax.legend(loc="lower left", bbox_to_anchor=(0.02, 0.17), fontsize=8, framealpha=0.85,
                   prop={"family": FONTS, "size": 8})

    ia = inset_axes(skew.ax, "34%", "34%", loc=1)
    hod = Hodograph(ia, component_range=80.0)
    hod.add_grid(increment=20)
    hm = prof["p"] >= ptop                        # ホドグラフも表示する高度の範囲まで（2点未満なら全層）
    if hm.sum() < 2:
        hm = np.ones_like(hm, dtype=bool)
    hod.plot_colormapped(prof["u"][hm] * units("m/s"), prof["v"][hm] * units("m/s"), prof["h"][hm] * units.m)

    skew.ax.text(0.02, 0.02,
                 f"Parcel start: {fine['p'][dg['i0']]:.0f} hPa\nLCL: {dg['lcl_p'].magnitude:.0f} hPa\n"
                 f"CAPE: {dg['cape']:.0f} J/kg\nCIN: {dg['cin']:.0f} J/kg",
                 transform=skew.ax.transAxes, fontsize=8, va="bottom", ha="left",
                 bbox={"boxstyle": "round,pad=0.3", "fc": "white", "alpha": 0.8})

    # 地点の小地図（断面図と同じ作り）: 500hPa高度の線・海岸線・地点（赤い●）。緯度経度の数字では場所が分かりにくいため。cartopy は使わない
    ma = fig.add_axes([0.76, 0.58, 0.23, 0.32])
    w, e_, s, n = map_extent(lat, lon)
    if "gh" in F["p"].get(500, {}):
        ma.contour(F["lons"], F["lats"], F["p"][500]["gh"], levels=np.arange(4800, 6000, 30), colors="#999999", linewidths=0.6)
    for ln in _coast_lines():
        ma.plot(ln[0], ln[1], color="k", lw=0.6)
    ma.set_xticks(np.arange(np.ceil(w / 10) * 10, e_ + 0.1, 10))
    ma.set_yticks(np.arange(np.ceil(s / 10) * 10, n + 0.1, 10))
    ma.grid(color="#999999", linewidth=0.4, alpha=0.7)
    ma.tick_params(labelbottom=False, labelleft=False, length=0)
    ma.plot([lon], [lat], marker="o", markersize=8, markerfacecolor="red", markeredgecolor="white", markeredgewidth=1.2,
            linestyle="none", zorder=6)
    ma.set_xlim(w, e_)                 # データの範囲は東経50〜185度なので、経度の折り返し（360→0）は起きない
    ma.set_ylim(s, n)
    ma.text(0.03, 0.04, f"{lat:.1f}N {lon:.1f}E", transform=ma.transAxes, fontsize=8, color="#a00000", va="bottom", ha="left", zorder=7,
            bbox={"boxstyle": "round,pad=0.15", "fc": "white", "ec": "none", "alpha": 0.8})

    valid = init + timedelta(hours=ft)
    fig.text(0.09, 0.955, name or f"{lat:.2f}N {lon:.2f}E", fontsize=13, family=FONTS, va="center")
    fig.text(0.985, 0.955, f"{valid:%m/%d %H}UTC", fontsize=13, family="serif", color="#5a3410",
             va="center", ha="right")
    fig.text(0.09, 0.915, f"要求 {lat:.2f}N {lon:.2f}E / 格子 {prof['grid_lat']:.2f}N {prof['grid_lon']:.2f}E"
             f"（地表気圧 {prof['sp']:.0f}hPa）" + (f"／表示 〜{ptop}hPa" if ptop != 100 else ""), fontsize=8, family=FONTS, va="center")
    fig.text(0.006, 0.012, f"{model} INIT:{init:%m/%d %H:%M}(UTC)".strip(), fontsize=9, va="bottom")
    _caption(fig, [(f"{init:%d%H}", "#1010e0", 16), (f"<{ft}>", "#d020d0", 16),
                      ("Emagram", "#7a5a30", 16)], y=0.045, fw=W)
    fig.savefig(outpath, dpi=DPI)
    plt.close(fig)
    if quantize:
        _quantize_png(outpath)
    return dict(cape=dg["cape"], cin=dg["cin"])
