#!/usr/bin/env python
# coding: utf-8
"""作図した画像を、公開サイト（nwp_chart_web の gh-pages ブランチの output/custom/）に載せる／外す。

GitHub の Contents API で、画像と list.json（新しい順・最大20件）だけを書き換える。リポジトリは clone しない（Render の軽量環境向け）。
nwp_chart_web の publish.py --custom と同じ形式の list.json を書く。publish.py は定期的に孤立コミットで作り直すので、
この方法で増えたコミットも履歴に残らない。トークンは対象リポジトリの Contents 書き込み権限だけの fine-grained トークンにする。
"""
import base64
import json
import re
import time
from pathlib import PurePosixPath

import requests

MAX_ITEMS = 20          # 公開サイトに載せる最大件数（nwp_chart_web の publish.py の CUSTOM_MAX と同じにする）
NAME_RE = re.compile(r"(?:xsection|emagram)_[a-z0-9]+_\d{10}_\d{3}_[0-9a-f]{12}\.png")


class GitHubError(RuntimeError):
    pass


class GitHub:
    """Contents API の最小クライアント。"""

    def __init__(self, token, repo, branch="gh-pages", base="https://api.github.com", session=None):
        self.repo, self.branch, self.base = repo, branch, base.rstrip("/")
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "nwp-xsection-api"})

    def _url(self, path):
        return f"{self.base}/repos/{self.repo}/contents/{path}"

    def get(self, path):
        """ファイルを (中身のbytes, sha) で返す。無ければ None。"""
        r = self.s.get(self._url(path), params={"ref": self.branch}, timeout=30)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise GitHubError(f"GET {path}: HTTP {r.status_code}")
        j = r.json()
        if j.get("content") is None or j.get("encoding") != "base64":    # 1MB超などは別取得が要る（list.json・PNGは小さいので通常は来ない）
            raise GitHubError(f"GET {path}: 中身を取得できません")
        return base64.b64decode(j["content"]), j["sha"]

    def put(self, path, data, message, sha=None):
        body = {"message": message, "content": base64.b64encode(data).decode("ascii"), "branch": self.branch}
        if sha:
            body["sha"] = sha
        r = self.s.put(self._url(path), json=body, timeout=60)
        if r.status_code in (200, 201):
            return
        if r.status_code in (409, 422):       # 他の更新と衝突（sha が古い）
            raise Conflict(f"PUT {path}: HTTP {r.status_code}")
        raise GitHubError(f"PUT {path}: HTTP {r.status_code}")

    def delete(self, path, sha, message):
        r = self.s.delete(self._url(path), json={"message": message, "sha": sha, "branch": self.branch}, timeout=30)
        if r.status_code in (200, 404):
            return
        if r.status_code in (409, 422):
            raise Conflict(f"DELETE {path}: HTTP {r.status_code}")
        raise GitHubError(f"DELETE {path}: HTTP {r.status_code}")

    def list_dir(self, path):
        """フォルダ内のファイル名と sha（無ければ空）。"""
        r = self.s.get(self._url(path), params={"ref": self.branch}, timeout=30)
        if r.status_code == 404:
            return []
        if r.status_code != 200:
            raise GitHubError(f"GET {path}/: HTTP {r.status_code}")
        return [{"name": x["name"], "sha": x["sha"]} for x in r.json() if x.get("type") == "file"]


class Conflict(GitHubError):
    pass


def _read_list(gh, dirpath):
    got = gh.get(f"{dirpath}/list.json")
    if got is None:
        return [], None
    try:
        return list(json.loads(got[0].decode("utf-8")).get("items", [])), got[1]
    except (ValueError, UnicodeDecodeError):
        return [], got[1]


def _write_list(gh, dirpath, items, sha, message):
    text = json.dumps({"items": items, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, ensure_ascii=False)
    gh.put(f"{dirpath}/list.json", text.encode("utf-8"), message, sha)


def _name_of(item):
    return PurePosixPath(item.get("url", "")).name


def _sweep(gh, dirpath, items):
    """一覧から外れた画像を公開側からも消す。"""
    want = {_name_of(x) for x in items}
    for f in gh.list_dir(dirpath):
        if f["name"].endswith(".png") and f["name"] not in want:
            gh.delete(f"{dirpath}/{f['name']}", f["sha"], f"自由選択の断面図を外す: {f['name']}")


def publish(gh, dirpath, name, png: bytes, item: dict, retries=3):
    """画像を載せ、list.json の先頭に item を足す（同じ名前の項目は置き換える）。載っている名前の一覧を返す。"""
    if not NAME_RE.fullmatch(name):
        raise GitHubError("画像名が不正です")
    for attempt in range(retries):
        try:
            existing = gh.get(f"{dirpath}/{name}")
            gh.put(f"{dirpath}/{name}", png, f"自由選択の断面図を公開: {name}", existing[1] if existing else None)
            items, sha = _read_list(gh, dirpath)
            items = ([item] + [x for x in items if _name_of(x) != name])[:MAX_ITEMS]
            _write_list(gh, dirpath, items, sha, f"自由選択の一覧を更新: {name}")
            _sweep(gh, dirpath, items)
            return [_name_of(x) for x in items]
        except Conflict:
            if attempt == retries - 1:
                raise GitHubError("他の更新と重なりました。少し待ってからもう一度試してください")
            time.sleep(1.5 * (attempt + 1))


def unpublish(gh, dirpath, name, retries=3):
    """公開側から画像を外す。載っている名前の一覧を返す。"""
    if not NAME_RE.fullmatch(name):
        raise GitHubError("画像名が不正です")
    for attempt in range(retries):
        try:
            items, sha = _read_list(gh, dirpath)
            items = [x for x in items if _name_of(x) != name]
            _write_list(gh, dirpath, items, sha, f"自由選択の一覧を更新（外す）: {name}")
            _sweep(gh, dirpath, items)
            return [_name_of(x) for x in items]
        except Conflict:
            if attempt == retries - 1:
                raise GitHubError("他の更新と重なりました。少し待ってからもう一度試してください")
            time.sleep(1.5 * (attempt + 1))
