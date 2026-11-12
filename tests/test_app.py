# HTTPサーバー全体のテスト（実際にポートを開く。作図とGitHubは偽物に差し替える）
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import app
import auth
import engine
from test_ghpub import FakeGH

ORIGIN = "https://masauehr.github.io"
KEY = "test-secret-key"
Q = "model=gfs&init={init}&ft=12&lat1=45&lon1=130&lat2=25&lon2=130"


def init_str():
    from datetime import datetime, timedelta, timezone
    d = datetime.now(timezone.utc) - timedelta(days=1)
    return f"{d:%Y%m%d}00"


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path)
    calls = []

    def fake_render(p):
        calls.append(p)
        time.sleep(0.2)
        out = engine.out_path(p)
        out.write_bytes(b"\x89PNG fake")
        engine.write_meta(out, p)
        return out

    for w in (app._render_ip, app._render_all, app._auth_fail, app._publish_key):     # 回数制限を毎回まっさらにする
        w._hits.clear()
    app.Handler.jobs = app.Jobs(render_fn=fake_render)
    app.Handler.keys = auth.parse_keys(f"tanaka:{auth.hash_key(KEY)}")
    app.Handler.gh = FakeGH()
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield dict(base=f"http://127.0.0.1:{server.server_address[1]}", calls=calls, gh=app.Handler.gh)
    server.shutdown()


def call(base, path, method="GET", body=None, headers=None):
    req = urllib.request.Request(base + path, method=method, headers=headers or {},
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def jcall(*a, **k):
    code, body, h = call(*a, **k)
    return code, json.loads(body), h


def wait_done(base, jid, limit=10):
    for _ in range(limit * 10):
        code, j, _ = jcall(base, f"/api/job/{jid}")
        if j.get("state") in ("done", "error"):
            return j
        time.sleep(0.1)
    raise AssertionError("ジョブが終わらない")


def test_health_and_cors(srv):
    code, j, h = jcall(srv["base"], "/api/health", headers={"Origin": ORIGIN})
    assert code == 200 and j["ok"] and j["publish"] and h["Access-Control-Allow-Origin"] == ORIGIN
    _, _, h2 = jcall(srv["base"], "/api/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in h2
    code, _, h3 = call(srv["base"], "/api/publish", "OPTIONS", headers={"Origin": ORIGIN})
    assert code == 204 and "Authorization" in h3["Access-Control-Allow-Headers"]
    assert call(srv["base"], "/api/publish", "OPTIONS", headers={"Origin": "https://evil.example"})[0] == 403


def test_render_flow_and_cache(srv):
    q = Q.format(init=init_str())
    code, j, _ = jcall(srv["base"], "/api/xsection?" + q)
    assert code == 202 and j["state"] == "queued"
    done = wait_done(srv["base"], j["job"])
    assert done["state"] == "done" and done["url"].startswith("/img/") and "断面" in done["label"]
    assert "NOAA" in done["legal"]                                       # 出典表示を返す（画面に出す）
    code, body, h = call(srv["base"], done["url"])
    assert code == 200 and body.startswith(b"\x89PNG") and h["Content-Type"] == "image/png"
    code, j2, _ = jcall(srv["base"], "/api/xsection?" + q)             # 同じ条件はすぐ返る（作り直さない）
    assert code == 200 and j2["state"] == "done" and len(srv["calls"]) == 1


def test_bad_input_is_400(srv):
    code, j, _ = jcall(srv["base"], "/api/xsection?model=jra55&init=2026111100&ft=0")
    assert code == 400 and "モデル" in j["error"]
    assert jcall(srv["base"], "/api/xsection")[0] == 400


def test_same_condition_while_running_shares_the_job(srv):
    q = Q.format(init=init_str())
    a = jcall(srv["base"], "/api/xsection?" + q)[1]
    b = jcall(srv["base"], "/api/xsection?" + q)[1]
    assert a["job"] == b["job"]
    wait_done(srv["base"], a["job"])
    assert len(srv["calls"]) == 1


def test_per_ip_limit(srv, monkeypatch):
    monkeypatch.setattr(app, "_render_ip", app.SlidingWindow(2, 3600))
    base = srv["base"]
    for ft in (0, 3):
        assert jcall(base, f"/api/xsection?model=gfs&init={init_str()}&ft={ft}&lat1=45&lon1=130&lat2=25&lon2=130")[0] == 202
    code, j, h = jcall(base, f"/api/xsection?model=gfs&init={init_str()}&ft=6&lat1=45&lon1=130&lat2=25&lon2=130")
    assert code == 429 and "Retry-After" in h and "上限" in j["error"]


def test_queue_full_is_503(srv, monkeypatch):
    monkeypatch.setattr(app, "_render_ip", app.SlidingWindow(100, 3600))
    monkeypatch.setattr(app, "_render_all", app.SlidingWindow(100, 3600))
    codes = [jcall(srv["base"], f"/api/xsection?model=gfs&init={init_str()}&ft={ft}&lat1=45&lon1=130&lat2=25&lon2=130")[0]
             for ft in range(0, 30, 3)]
    assert 503 in codes and codes[0] == 202


def test_job_unknown_is_404(srv):
    assert jcall(srv["base"], "/api/job/nope")[0] == 404
    assert call(srv["base"], "/img/..%2f..%2fetc%2fpasswd")[0] == 404
    assert call(srv["base"], "/img/xsection_gfs_2026111100_012_0123456789ab.png")[0] == 404


def make_image(srv):
    j = jcall(srv["base"], "/api/xsection?" + Q.format(init=init_str()))[1]
    return wait_done(srv["base"], j["job"])["name"]


def test_publish_requires_key(srv):
    name = make_image(srv)
    code, j, _ = jcall(srv["base"], "/api/publish", "POST", {"name": name})
    assert code == 401
    code, _, _ = jcall(srv["base"], "/api/publish", "POST", {"name": name}, {"Authorization": "Bearer wrong"})
    assert code == 401 and not srv["gh"].files


def test_publish_and_unpublish(srv):
    name = make_image(srv)
    h = {"Authorization": f"Bearer {KEY}", "Origin": ORIGIN}
    code, j, _ = jcall(srv["base"], "/api/publish", "POST", {"name": name}, h)
    assert code == 200 and j["names"] == [name]
    items = json.loads(srv["gh"].files["output/custom/list.json"][0])["items"]
    assert items[0]["by"] == "tanaka" and items[0]["url"] == f"output/custom/{name}" and "legal" not in items[0]
    assert srv["gh"].files[f"output/custom/{name}"][0].startswith(b"\x89PNG")
    code, j, _ = jcall(srv["base"], "/api/unpublish", "POST", {"name": name}, h)
    assert code == 200 and j["names"] == []


def test_publish_gone_when_cache_expired(srv):
    name = "xsection_gfs_2026111100_012_0123456789ab.png"
    code, j, _ = jcall(srv["base"], "/api/publish", "POST", {"name": name}, {"Authorization": f"Bearer {KEY}"})
    assert code == 410


def test_publish_bad_name_and_foreign_origin(srv):
    h = {"Authorization": f"Bearer {KEY}"}
    assert jcall(srv["base"], "/api/publish", "POST", {"name": "../x.png"}, h)[0] == 400
    assert jcall(srv["base"], "/api/publish", "POST", {"name": "a"}, {**h, "Origin": "https://evil.example"})[0] == 403


def test_wrong_key_lockout(srv):
    for _ in range(5):
        assert jcall(srv["base"], "/api/publish", "POST", {"name": "x"}, {"Authorization": "Bearer bad"})[0] == 401
    code, j, h = jcall(srv["base"], "/api/publish", "POST", {"name": "x"}, {"Authorization": f"Bearer {KEY}"})
    assert code == 429                          # 正しい合言葉でも、間違いが続いた相手は一定時間受け付けない


def test_publish_disabled_without_config(srv):
    app.Handler.gh = None
    assert jcall(srv["base"], "/api/publish", "POST", {"name": "x"}, {"Authorization": f"Bearer {KEY}"})[0] == 503


def test_client_ip():
    assert app.client_ip("1.1.1.1, 2.2.2.2", "9.9.9.9", 1) == "2.2.2.2"        # 右側（プロキシが付けた方）を使う
    assert app.client_ip("6.6.6.6, 1.1.1.1, 2.2.2.2", "9.9.9.9", 2) == "1.1.1.1"
    assert app.client_ip(None, "9.9.9.9", 1) == "9.9.9.9"


def test_client_ip_skips_internal_proxies():
    """Render では右端が内部のプロキシ（10.x）。それを飛ばして、本物のクライアントを採る。"""
    assert app.client_ip("133.1.2.3, 10.20.30.40", "10.0.0.1", 1) == "133.1.2.3"
    assert app.client_ip("133.1.2.3, 10.20.30.40, 10.1.1.1", "10.0.0.1", 1) == "133.1.2.3"
    assert app.client_ip("6.6.6.6, 133.1.2.3, 10.20.30.40", "10.0.0.1", 1) == "133.1.2.3"     # 左に偽装があっても、右の公開アドレスを採る
    assert app.client_ip("133.1.2.3, 10.20.30.40", "10.0.0.1", 2) == "10.0.0.1"                # 公開アドレスが足りなければ接続元（安全側）
    assert app.client_ip("10.1.1.1, 192.168.0.5, fe80::1, ::1, 127.0.0.1", "9.9.9.9", 1) == "9.9.9.9"
    assert app.client_ip("not-an-ip, 133.1.2.3", "9.9.9.9", 1) == "133.1.2.3"
