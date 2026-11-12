#!/usr/bin/env python
# coding: utf-8
"""断面図APIサーバー（標準ライブラリのみ。Render などの軽量環境向け）。

  GET  /api/health                 → 動作確認（{"ok":true, "ip":…}。ip は回数制限が見ている相手のアドレス）
  GET  /api/xsection?model=gfs&init=YYYYMMDDHH&ft=12&lat1=..&lon1=..&lat2=..&lon2=..&shade=..&contours=..&barbs=..
                                   → 認証なし。同じ条件の画像があれば {"state":"done",…}、無ければ作図ジョブを作って 202 {"state":"queued","job":id}
  GET  /api/job/<id>               → ジョブの状態（queued / running / done / error）。done なら url・label などを返す
  GET  /img/<名前>.png             → 作図した画像
  POST /api/publish   {"name":"xsection_….png"}   Authorization: Bearer <合言葉> → 公開サイト（GitHub）に載せる
  POST /api/unpublish {"name":"xsection_….png"}   同上 → 公開サイトから外す

作図は1件ずつ（待ち行列に最大 QUEUE_MAX 件）。1回に1〜2分かかるため、すぐ返してポーリングさせる（Render のタイムアウト対策）。
設定は環境変数（README.md の表）。秘密（GITHUB_TOKEN・API_KEYS）はリポジトリに入れない。
"""
import ipaddress
import json
import os
import queue
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import auth
import engine
import ghpub
from limits import SlidingWindow

QUEUE_MAX = int(os.environ.get("QUEUE_MAX", "3"))                      # 待ち行列（実行中を除く）の最大件数
RENDER_PER_HOUR = int(os.environ.get("RENDER_PER_HOUR", "8"))          # 1つのIPが1時間に新しく作図できる回数（キャッシュに当たる分は数えない）
GLOBAL_PER_HOUR = int(os.environ.get("GLOBAL_PER_HOUR", "60"))         # 全体で1時間に新しく作図できる回数
TRUSTED_HOPS = int(os.environ.get("TRUSTED_HOPS", "1"))                # X-Forwarded-For の右から（内部アドレスを除いて）何番目の公開アドレスを相手のIPとみなすか
DEBUG_IP = os.environ.get("DEBUG_IP") == "1"                           # 1 のとき /api/health に受け取った X-Forwarded-For を出す（診断用。確認後は外す）
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get(
    "ALLOWED_ORIGINS", "https://masauehr.github.io,http://127.0.0.1:8790,http://localhost:8790").split(",") if o.strip()}
GITHUB_REPO = os.environ.get("GITHUB_REPO", "masauehr/nwp_chart_web")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "gh-pages")
PUBLISH_DIR = os.environ.get("PUBLISH_DIR", "output/custom")
PUBLIC_SITE_URL = os.environ.get("PUBLIC_SITE_URL", "https://masauehr.github.io/nwp_chart_web/")
JOB_KEEP_SEC = 3600

_render_ip = SlidingWindow(RENDER_PER_HOUR, 3600)
_render_all = SlidingWindow(GLOBAL_PER_HOUR, 3600)
_auth_fail = SlidingWindow(5, 600)          # 合言葉の間違いは10分に5回まで（総当たり対策）
_publish_key = SlidingWindow(20, 3600)      # 合言葉ごとの公開操作は1時間に20回まで


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Jobs:
    """作図ジョブ。1本のワーカーが順に処理する（メモリを抑えるため同時には1件）。"""

    def __init__(self, render_fn=None):
        self.render_fn = render_fn or engine.render_subprocess
        self.q = queue.Queue(maxsize=QUEUE_MAX)
        self.jobs = {}
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.thread.start()

    def _prune(self):
        now = time.time()
        for k in [k for k, j in self.jobs.items() if j["state"] in ("done", "error") and now - j["finished"] > JOB_KEEP_SEC]:
            del self.jobs[k]

    def find_active(self, name):
        for j in self.jobs.values():
            if j["name"] == name and j["state"] in ("queued", "running"):
                return j
        return None

    def submit(self, p):
        """ジョブを作る。同じ条件で処理中のものがあればそれを返す。待ち行列が満杯なら None。"""
        name = engine.image_name(p)
        with self.lock:
            self._prune()
            act = self.find_active(name)
            if act:
                return act
            job = dict(id=secrets.token_urlsafe(9), state="queued", name=name, p=p, created=time.time(), msg="順番待ちです")
            try:
                self.q.put_nowait(job)
            except queue.Full:
                return None
            self.jobs[job["id"]] = job
            return job

    def get(self, jid):
        with self.lock:
            return self.jobs.get(jid)

    def position(self, job):
        """待ち行列の中の位置（実行中なら0）。"""
        with self.lock:
            ahead = [j for j in list(self.q.queue) if j["created"] < job["created"]]
            running = any(j["state"] == "running" for j in self.jobs.values())
            return len(ahead) + (1 if running and job["state"] == "queued" else 0)

    def _work(self):
        while True:
            job = self.q.get()
            job["state"], job["msg"] = "running", "作図中です（データ取得が入ると1〜2分かかります）"
            t = time.time()
            try:
                out = self.render_fn(job["p"])
                job["state"], job["msg"] = "done", "完了"
                log("作図完了", out.name, f"{time.time() - t:.0f}秒")
            except engine.CustomError as e:
                job["state"], job["msg"] = "error", str(e)
            except Exception as e:          # 想定外。詳細はログだけに残し、画面には出さない
                log("作図に失敗", repr(e))
                job["state"], job["msg"] = "error", "作図に失敗しました（しばらくしてからもう一度試してください）"
            job["finished"] = time.time()


def _is_public(addr):
    """公開されているアドレスか（10.x・192.168.x・127.x などプロキシ内部のものは False）。"""
    try:
        a = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_unspecified or a.is_reserved)


def client_ip(xff, peer, hops=TRUSTED_HOPS):
    """相手のIP。プロキシが付けた X-Forwarded-For を右から見て、プライベートアドレス（Render 内部のプロキシ）を飛ばし、
    公開アドレスを hops 番目に採る（左側は相手が偽装できるので使わない）。見つからなければ接続元。"""
    if xff:
        pub = [x.strip() for x in xff.split(",") if _is_public(x.strip())]
        if len(pub) >= hops:
            return pub[-hops]
    return peer


class Handler(BaseHTTPRequestHandler):
    server_version = "nwp-xsection-api"
    jobs: Jobs = None
    keys: dict = {}
    gh = None

    # ---------- 共通
    def _origin_ok(self):
        o = self.headers.get("Origin")
        return o.rstrip("/") if o and o.rstrip("/") in ALLOWED_ORIGINS else None

    def _send(self, code, body: bytes, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        o = self._origin_ok()
        if o:
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Vary", "Origin")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8",
                   {"Cache-Control": "no-store", **(extra or {})})

    def _ip(self):
        return client_ip(self.headers.get("X-Forwarded-For"), self.client_address[0])

    def log_message(self, fmt, *args):
        pass

    # ---------- OPTIONS（CORS の事前確認）
    def do_OPTIONS(self):
        o = self._origin_ok()
        self.send_response(204 if o else 403)
        if o:
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- GET
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/health" or u.path == "/":
            body = {"ok": True, "ip": self._ip(), "publish": self.gh is not None and bool(self.keys)}
            if DEBUG_IP:
                body["debug"] = {"xff": self.headers.get("X-Forwarded-For"), "peer": self.client_address[0]}
            self._json(200, body)
        elif u.path == "/api/xsection":
            self._xsection({k: v[0] for k, v in parse_qs(u.query).items()})
        elif u.path.startswith("/api/job/"):
            self._job(u.path[len("/api/job/"):])
        elif u.path.startswith("/img/"):
            self._img(u.path[len("/img/"):])
        else:
            self._json(404, {"error": "not found"})

    def _done(self, name):
        meta = engine.read_meta(name) or {}
        return {"state": "done", "name": name, "url": f"/img/{name}", "label": meta.get("label"), "model": meta.get("model"),
                "initText": meta.get("initText"), "ft": meta.get("ft"), "legal": meta.get("legal")}

    def _xsection(self, q):
        try:
            p = engine.validate(q.get("model"), q.get("init"), q.get("ft"), q)
        except engine.CustomError as e:
            self._json(400, {"error": str(e)})
            return
        name = engine.image_name(p)
        if engine.out_path(p).exists() and engine.read_meta(name):
            os.utime(engine.out_path(p), None)
            self._json(200, self._done(name))
            return
        ip = self._ip()
        act = self.jobs.find_active(name)            # 同じ条件が処理中なら、回数は数えずにそのジョブを返す
        if act is None:
            if not _render_ip.allowed(ip):
                ra = _render_ip.retry_after(ip)
                self._json(429, {"error": f"作図の回数が上限です（1時間に{RENDER_PER_HOUR}回まで）。{ra // 60 + 1}分ほど待ってください"}, {"Retry-After": str(ra)})
                return
            if not _render_all.allowed("all"):
                self._json(429, {"error": "いま混み合っています。しばらくしてからもう一度試してください"}, {"Retry-After": str(_render_all.retry_after("all"))})
                return
        job = self.jobs.submit(p)
        if job is None:
            self._json(503, {"error": "順番待ちが満杯です。しばらくしてからもう一度試してください"}, {"Retry-After": "60"})
            return
        if act is None:
            _render_ip.hit(ip)
            _render_all.hit("all")
        self._json(202, {"state": job["state"], "job": job["id"], "name": name, "msg": job["msg"], "position": self.jobs.position(job)})

    def _job(self, jid):
        job = self.jobs.get(jid)
        if job is None:
            self._json(404, {"error": "ジョブが見つかりません（再起動で消えた可能性があります。もう一度作図してください）"})
        elif job["state"] == "done":
            self._json(200, self._done(job["name"]))
        elif job["state"] == "error":
            self._json(200, {"state": "error", "error": job["msg"]})
        else:
            self._json(200, {"state": job["state"], "msg": job["msg"], "position": self.jobs.position(job)})

    def _img(self, name):
        if not engine.NAME_RE.fullmatch(name):
            self._json(404, {"error": "not found"})
            return
        try:
            data = (engine.CACHE_DIR / name).read_bytes()
        except OSError:
            self._json(404, {"error": "画像がありません（時間が経つと消えます。もう一度作図してください）"})
            return
        self._send(200, data, "image/png", {"Cache-Control": "public, max-age=3600"})

    # ---------- POST（公開・公開取り下げ）
    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/publish", "/api/unpublish"):
            self._json(404, {"error": "not found"})
            return
        o = self.headers.get("Origin")
        if o and not self._origin_ok():
            self._json(403, {"error": "許可されていないOriginです"})
            return
        if self.gh is None or not self.keys:
            self._json(503, {"error": "公開の機能が設定されていません"})
            return
        ip = self._ip()
        if not _auth_fail.allowed(ip):
            self._json(429, {"error": "合言葉の間違いが続いたため、しばらく受け付けません"}, {"Retry-After": str(_auth_fail.retry_after(ip))})
            return
        who = auth.check(self.headers.get("Authorization"), self.keys)
        if who is None:
            _auth_fail.hit(ip)
            self._json(401, {"error": "合言葉が違います"})
            return
        if not _publish_key.hit(who):
            self._json(429, {"error": "公開操作の回数が上限です（1時間に20回まで）"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8")) if 0 < n < 2000 else {}
        except (ValueError, UnicodeDecodeError):
            body = {}
        name = body.get("name") if isinstance(body, dict) else None
        if not isinstance(name, str) or not engine.NAME_RE.fullmatch(name):
            self._json(400, {"error": "画像の指定が不正です"})
            return
        try:
            if path == "/api/unpublish":
                names = ghpub.unpublish(self.gh, PUBLISH_DIR, name)
                log("公開から外す", name, "by", who)
            else:
                meta = engine.read_meta(name)
                try:
                    png = (engine.CACHE_DIR / name).read_bytes()
                except OSError:
                    png = None
                if meta is None or png is None:
                    self._json(410, {"error": "画像が残っていません（時間が経つと消えます）。もう一度作図してから公開してください"})
                    return
                item = {k: meta[k] for k in ("kind", "mk", "ik", "ft", "label", "model", "initText") if k in meta}
                item.update(url=f"{PUBLISH_DIR}/{name}", ts=meta.get("created", time.time()), by=who)
                names = ghpub.publish(self.gh, PUBLISH_DIR, name, png, item)
                log("公開", name, "by", who)
        except ghpub.GitHubError as e:
            log("公開に失敗", repr(e))
            self._json(502, {"error": f"公開サイトへの反映に失敗しました: {e}"})
            return
        self._json(200, {"ok": True, "names": names, "site": PUBLIC_SITE_URL})


def make_gh():
    token = os.environ.get("GITHUB_TOKEN")
    return ghpub.GitHub(token, GITHUB_REPO, GITHUB_BRANCH) if token else None


def main():
    port = int(os.environ.get("PORT", "8800"))
    host = os.environ.get("HOST", "0.0.0.0")
    Handler.jobs = Jobs()
    Handler.keys = auth.parse_keys(os.environ.get("API_KEYS", ""))
    Handler.gh = make_gh()
    if Handler.gh is None or not Handler.keys:
        log("注意: GITHUB_TOKEN か API_KEYS が未設定のため、公開（GitHubへの掲載）は無効です")
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    log(f"http://{host}:{port}/ で待ち受け（許可Origin: {', '.join(sorted(ALLOWED_ORIGINS))}）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
