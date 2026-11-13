#!/usr/bin/env python
# coding: utf-8
"""入力の検証・作図・キャッシュ（断面図専用）。

nwp_chart_web の custom.py のコピーを、Render（永続ディスクなし・メモリ小）向けに調整したもの（共通部品化しない）。
  - 画像は CACHE_DIR（既定 /tmp/nwp_xsection/png）に作る。同じ条件ならそれを返す。上限を超えたら古い順に消す
  - 取得した GRIB2 は別プロセスで展開して .npz にし（pygrib の展開は一時的に約300MBを使うため、本体のメモリを守る）、
    GRIB2 は捨てる。.npz は GRIB_DIR（既定 /tmp/nwp_xsection/grib）に置き、上限を超えたら古い順に消す（同じ初期値・予報時間の
    別の断面は、再取得も再展開もせずに作れる）
  - 画像ごとに条件を書いた同名の .json を隣に置く（公開時に list.json の項目を作るのに使う）
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import nwp_data
import opts

WORK_DIR = Path(os.environ.get("WORK_DIR", "/tmp/nwp_xsection"))
CACHE_DIR = WORK_DIR / "png"
GRIB_DIR = WORK_DIR / "grib"
CACHE_MAX_FILES = int(os.environ.get("CACHE_MAX_FILES", "40"))             # 画像（png＋json）の保持件数
GRIB_MAX_BYTES = int(float(os.environ.get("GRIB_MAX_MB", "300")) * 1024 * 1024)   # 取得データの保持容量
INIT_MAX_DAYS = 10     # これより古い初期値は受け付けない（配信元に残っていない）
KINDS = ("xsection", "emagram")
NAME_RE = re.compile(r"(?:xsection|emagram)_[a-z0-9]+_\d{10}_\d{3}_[0-9a-f]{12}\.png")


class CustomError(ValueError):
    """入力不正・データ無し。メッセージは画面にそのまま出す。"""


def _num(v, name, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise CustomError(f"{name} が数値ではありません")
    if x != x or not (lo <= x <= hi):      # NaN も弾く
        raise CustomError(f"{name} は {lo:g}〜{hi:g} の範囲で指定してください")
    return x


def validate(model, init_s, ft, q, now=None, kind="xsection"):
    """入力を検証して正規化した dict を返す。配信されていないはずの初期値・予報時間は、配信元に問い合わせる前に弾く。
    kind: xsection（断面図。lat1・lon1・lat2・lon2 と表示要素）／ emagram（エマグラム。lat・lon）。"""
    if kind not in KINDS:
        raise CustomError("kind は xsection / emagram のどちらかです")
    if model not in nwp_data.MODELS:
        raise CustomError("モデルが不正です（ecmwf / gfs / aifs）")
    if not re.fullmatch(r"\d{10}", init_s or ""):
        raise CustomError("初期値は YYYYMMDDHH（UTC）で指定してください")
    try:
        init = nwp_data.parse_init(init_s)
    except ValueError:
        raise CustomError("初期値が日時として不正です")
    if init.hour not in (0, 6, 12, 18):
        raise CustomError("初期値の時刻は 00/06/12/18UTC のいずれかです")
    now = now or datetime.now(timezone.utc)
    if init > now:
        raise CustomError("初期値が未来です")
    if now - init > timedelta(days=INIT_MAX_DAYS):
        raise CustomError(f"初期値が古すぎます（{INIT_MAX_DAYS}日以内）")
    ft = int(_num(ft, "予報時間", 0, 360))
    if not nwp_data.step_ok(model, init, ft):
        raise CustomError(f"{nwp_data.MODELS[model]['label']} {init_s} には予報時間 {ft}h がありません（3時間刻み・AIFSは6時間刻み・ECMWFの06/18UTCは90hまで）")
    try:
        ptop = opts.parse_ptop(q.get("ptop"))      # 高度設定（表示する高度の範囲の上端）
    except ValueError as e:
        raise CustomError(str(e))
    p = dict(kind=kind, model=model, init=init_s, ft=ft)
    if ptop != opts.DEFAULT_PTOP:                  # 既定のときは p に入れない（画像名が従来と同じになる）
        p["ptop"] = ptop
    c = nwp_data.CROP        # データを読む範囲の中に限る
    if kind == "emagram":
        p.update(lat=_num(q.get("lat"), "緯度", c["lat0"], c["lat1"]), lon=_num(q.get("lon"), "経度", c["lon0"], c["lon1"]))
        if str(q.get("pt") or "").lower() in ("1", "true", "on"):      # 温位エマグラム（横軸が温位）。既定は p に入れない（画像名が従来と同じ）
            p["pt"] = 1
        return p
    p.update(lat1=_num(q.get("lat1"), "始点の緯度", c["lat0"], c["lat1"]), lon1=_num(q.get("lon1"), "始点の経度", c["lon0"], c["lon1"]),
             lat2=_num(q.get("lat2"), "終点の緯度", c["lat0"], c["lat1"]), lon2=_num(q.get("lon2"), "終点の経度", c["lon0"], c["lon1"]))
    span = max(abs(p["lat2"] - p["lat1"]), abs(p["lon2"] - p["lon1"]))
    if span < 1.0:
        raise CustomError("始点と終点が近すぎます（1度以上離してください）")
    if span > 60.0:
        raise CustomError("断面が長すぎます（60度以内にしてください）")
    # 表示要素: shade=色塗り（none/div/rh/omega）・contours=等値線（theta,ept,wind をカンマ区切り）・barbs=風矢羽（off/back/front）
    raw = q.get("contours")
    try:
        o = opts.normalize_opts(dict(shade=q.get("shade") or None, barbs=q.get("barbs") or None,
                                         contours=None if raw is None else [c for c in raw.split(",") if c], ptop=ptop))
    except ValueError as e:
        raise CustomError(str(e))
    p.update(shade=o["shade"], contours=",".join(o["contours"]), barbs=o["barbs"])
    return p


def image_name(p):
    """条件から決まる画像名（同じ条件は同じ名前）。"""
    key = "_".join(f"{k}={p[k]}" for k in sorted(p))
    h = hashlib.sha1(key.encode()).hexdigest()[:12]
    return f"{p['kind']}_{p['model']}_{p['init']}_{p['ft']:03d}_{h}.png"


def out_path(p):
    return CACHE_DIR / image_name(p)


def opts_of(p):
    return dict(shade=p.get("shade"), barbs=p.get("barbs"), ptop=p.get("ptop"),
                contours=None if p.get("contours") is None else [c for c in p["contours"].split(",") if c])


def describe(p):
    """画面に出す名前（断面線＋表示要素、またはエマグラムの地点）。"""
    if p["kind"] == "emagram":
        return (f"{'温位エマグラム' if p.get('pt') else 'エマグラム'} ({p['lat']:g}N,{p['lon']:g}E)"
                + (f" ｜ 上端{p['ptop']}hPa" if "ptop" in p else ""))
    base = f"断面 ({p['lat1']:g}N,{p['lon1']:g}E)→({p['lat2']:g}N,{p['lon2']:g}E)"
    return base + " ｜ " + opts.options_text(opts_of(p))


def init_text(ik):
    return f"{ik[:4]}-{ik[4:6]}-{ik[6:8]} {ik[8:]}Z"


def meta_path(png):
    return png.with_suffix(".json")


def read_meta(name):
    """画像名から条件（.json）を読む。無ければ None。"""
    if not NAME_RE.fullmatch(name):
        return None
    try:
        return json.loads(meta_path(CACHE_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_meta(out, p):
    """画像の隣に条件を書く。形式は nwp_chart_web の custom.py と同じ（公開側の list.json の項目になる）。"""
    now = time.time()
    meta = dict(kind=p["kind"], mk=p["model"], ik=p["init"], ft=p["ft"], label=describe(p),
                model=nwp_data.MODELS[p["model"]]["label"], initText=init_text(p["init"]),
                legal=nwp_data.legal_short(p["model"], int(p["init"][:4]), time.strftime("%Y-%m-%d", time.gmtime(now))),
                created=now)
    meta_path(out).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def prune_cache():
    """画像が上限を超えたら、古い順に（.json と一緒に）消す。"""
    try:
        pngs = sorted((f for f in CACHE_DIR.glob("*.png") if not f.name.endswith(".tmp.png")), key=lambda f: f.stat().st_mtime)
    except OSError:
        return
    for f in pngs[:max(0, len(pngs) - CACHE_MAX_FILES)]:
        f.unlink(missing_ok=True)
        meta_path(f).unlink(missing_ok=True)


def prune_grib():
    """取得データ（.npz・取り残しの .grib2）が上限を超えたら、古い順に消す（ディスクが小さい前提）。取り残しの一時ファイル（.part）も消す。"""
    try:
        for f in GRIB_DIR.glob("*/*/*.part"):
            if time.time() - f.stat().st_mtime > 3600:             # 1時間以上前の取り残し（取得は数分で終わる）
                f.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        files = sorted([*GRIB_DIR.glob("*/*/*.npz"), *GRIB_DIR.glob("*/*/*.grib2")], key=lambda f: f.stat().st_mtime)
    except OSError:
        return
    total = sum(f.stat().st_size for f in files)
    for f in files:
        if total <= GRIB_MAX_BYTES:
            break
        total -= f.stat().st_size
        f.unlink(missing_ok=True)


def npz_path(model, init, ft, all_gh=False):
    return nwp_data.grib_path(model, init, ft, GRIB_DIR, all_gh).with_suffix(".npz")


def load_F(model, init_s, ft, kind="xsection"):
    """格子データ F を返す。無ければ取得して .npz にする（GRIB2 の展開は別プロセス）。
    エマグラムは全層の高度が要る（別ファイル _gh）。断面図は、全層の高度つきがあればそれも使える（上位互換）。"""
    init = nwp_data.parse_init(init_s)
    all_gh = kind == "emagram"
    for cand in ((True,) if all_gh else (False, True)):
        npz = npz_path(model, init, ft, cand)
        if npz.exists():
            try:
                os.utime(npz, None)
                return nwp_data.load_npz(npz)
            except Exception:
                npz.unlink(missing_ok=True)       # 壊れていたら取り直す
    npz = npz_path(model, init, ft, all_gh)
    prune_grib()
    grib = nwp_data.fetch_subset(model, init, ft, GRIB_DIR, all_gh=all_gh)
    if grib is None:
        raise CustomError(f"{nwp_data.MODELS[model]['label']} {init_s} FT{ft}h のデータを取得できません"
                          "（まだ配信されていないか、配信元が混雑しています。初期値・予報時間を確認してください）")
    try:
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "nwp_data.py"), "pack", model, str(grib), str(npz)],
                           capture_output=True, text=True, timeout=240)
        if r.returncode != 0 or not npz.exists():
            print("GRIB2 の展開に失敗:", (r.stderr or "").strip()[-500:], flush=True)
            raise CustomError("取得したデータを読み込めませんでした（もう一度試してください）")
    except subprocess.TimeoutExpired:
        raise CustomError("取得したデータの読み込みが時間内に終わりませんでした（もう一度試してください）")
    finally:
        grib.unlink(missing_ok=True)          # GRIB2 は捨てる（npz だけ残す）
    return nwp_data.load_npz(npz)


def render(p):
    """作図して PNG のパスを返す（同じ条件が既にあればそれを返す）。"""
    out = out_path(p)
    if out.exists():
        os.utime(out, None)                    # 使った画像は消されにくくする
        if not meta_path(out).exists():
            write_meta(out, p)
        return out
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    init = nwp_data.parse_init(p["init"])
    label = nwp_data.MODELS[p["model"]]["label"]
    F = load_F(p["model"], p["init"], p["ft"], p["kind"])
    tmp = out.with_suffix(".tmp.png")
    try:
        if p["kind"] == "emagram":
            import emagram
            try:
                emagram.render_emagram(F, init, p["ft"], p["lat"], p["lon"], str(tmp), model=label,
                                       ptop=p.get("ptop", opts.DEFAULT_PTOP), pt=bool(p.get("pt")))
            except ValueError as e:
                raise CustomError(str(e))
        else:
            import xsection
            xsection.render_xsection(F, init, p["ft"], (p["lat1"], p["lon1"]), (p["lat2"], p["lon2"]), str(tmp),
                                     model=label, name=describe(p).split(" ｜ ")[0], opts=opts_of(p))
        tmp.replace(out)
        write_meta(out, p)
    finally:
        tmp.unlink(missing_ok=True)
        del F
    prune_cache()
    return out


# ---------------------------------------------------------------- 別プロセスでの作図（本体のメモリを守る）
RENDER_TIMEOUT = int(os.environ.get("RENDER_TIMEOUT", "600"))


def children_peak_mb():
    """これまでに終了した子プロセス（作図・GRIB2展開）のうち、最大のメモリ使用量[MB]（このサーバーが起動してからの最大）。
    Linux の ru_maxrss は KB、macOS は bytes。取れなければ None。"""
    try:
        import resource
        v = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    except (ImportError, OSError):
        return None
    return v / (1024 * 1024) if sys.platform == "darwin" else v / 1024


def render_subprocess(p):
    """作図を別プロセスで行い、PNG のパスを返す。作図が終わればプロセスごとメモリが戻るので、サーバー本体は小さいままでいられる。
    入力・データの問題（CustomError）は、そのメッセージのまま CustomError で返す。"""
    try:
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "render", json.dumps(p)],
                           capture_output=True, text=True, timeout=RENDER_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise CustomError("作図が時間内に終わりませんでした（しばらくしてからもう一度試してください）")
    last = (r.stdout.strip().splitlines() or [""])[-1]
    if r.returncode == 0 and out_path(p).exists():
        return out_path(p)
    if r.returncode == 3:
        try:
            raise CustomError(json.loads(last)["error"])
        except (ValueError, KeyError):
            pass
    print("作図プロセスが失敗:", (r.stderr or "").strip()[-800:], flush=True)
    if r.returncode in (-9, 137):          # SIGKILL: 多くはメモリ不足（OOM）で強制終了された
        print("  → 強制終了されました。メモリ不足の可能性があります（Render の無料枠は512MB）", flush=True)
    raise RuntimeError(f"作図プロセスが異常終了しました（終了コード {r.returncode}）")


def _child_main(argv, render_fn=None):
    """python engine.py render '<条件のJSON>' : 作図して終了する（0=成功・3=入力/データの問題・それ以外=想定外）。"""
    if len(argv) != 3 or argv[1] != "render":
        print(_child_main.__doc__)
        return 2
    try:
        (render_fn or render)(json.loads(argv[2]))
        return 0
    except CustomError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(_child_main(sys.argv))
