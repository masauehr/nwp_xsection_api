# GitHub への公開（Contents API）のテスト。メモリ上の偽の GitHub で、一覧の更新・件数上限・衝突の再試行を確認する
import json

import pytest

import ghpub

DIR = "output/custom"


class FakeGH:
    """Contents API の振る舞いを真似る（sha が古いと衝突）。"""

    def __init__(self):
        self.files, self.n, self.fail_next_list_put = {}, 0, 0

    def _sha(self):
        self.n += 1
        return f"sha{self.n}"

    def get(self, path):
        return self.files.get(path)

    def put(self, path, data, message, sha=None):
        cur = self.files.get(path)
        if cur and sha != cur[1]:
            raise ghpub.Conflict("sha mismatch")
        if not cur and sha:
            raise ghpub.Conflict("no such file")
        if path.endswith("list.json") and self.fail_next_list_put:
            self.fail_next_list_put -= 1
            raise ghpub.Conflict("race")
        self.files[path] = (data, self._sha())

    def delete(self, path, sha, message):
        self.files.pop(path, None)

    def list_dir(self, path):
        return [{"name": p.rsplit("/", 1)[1], "sha": v[1]} for p, v in self.files.items() if p.startswith(path + "/")]


def name(i):
    return f"xsection_gfs_2026111100_{i:03d}_0123456789ab.png"


def item(i):
    return dict(kind="xsection", mk="gfs", ik="2026111100", ft=i, label=f"断面{i}", model="GFS", initText="x", url=f"{DIR}/{name(i)}", ts=1000.0 + i, by="tanaka")


def items_of(gh):
    return json.loads(gh.files[f"{DIR}/list.json"][0])["items"]


def test_publish_adds_image_and_list():
    gh = FakeGH()
    assert ghpub.publish(gh, DIR, name(1), b"png1", item(1)) == [name(1)]
    assert gh.files[f"{DIR}/{name(1)}"][0] == b"png1"
    assert items_of(gh)[0]["by"] == "tanaka" and items_of(gh)[0]["url"] == f"{DIR}/{name(1)}"


def test_newest_first_and_max_with_sweep():
    gh = FakeGH()
    n = ghpub.MAX_ITEMS
    assert n == 20
    for i in range(n + 2):
        ghpub.publish(gh, DIR, name(i), b"p", item(i))
    names = [x["url"].rsplit("/", 1)[1] for x in items_of(gh)]
    assert names == [name(i) for i in range(n + 1, 1, -1)]                    # 新しい順・上限の件数
    assert sorted(p.rsplit("/", 1)[1] for p in gh.files if p.endswith(".png")) == sorted(names)   # 外れた画像は公開側からも消える


def test_republish_same_name_replaces_not_duplicates():
    gh = FakeGH()
    ghpub.publish(gh, DIR, name(1), b"old", item(1))
    ghpub.publish(gh, DIR, name(2), b"p", item(2))
    ghpub.publish(gh, DIR, name(1), b"new", item(1))
    assert [x["ft"] for x in items_of(gh)] == [1, 2] and gh.files[f"{DIR}/{name(1)}"][0] == b"new"


def test_conflict_is_retried(monkeypatch):
    monkeypatch.setattr(ghpub.time, "sleep", lambda s: None)
    gh = FakeGH()
    gh.fail_next_list_put = 1
    assert ghpub.publish(gh, DIR, name(1), b"p", item(1)) == [name(1)]
    gh.fail_next_list_put = 5
    with pytest.raises(ghpub.GitHubError):
        ghpub.publish(gh, DIR, name(2), b"p", item(2))


def test_unpublish():
    gh = FakeGH()
    ghpub.publish(gh, DIR, name(1), b"p", item(1))
    ghpub.publish(gh, DIR, name(2), b"p", item(2))
    assert ghpub.unpublish(gh, DIR, name(1)) == [name(2)]
    assert f"{DIR}/{name(1)}" not in gh.files


def test_keeps_other_items_written_by_publish_py():
    """nwp_chart_web の publish.py が書いた項目（by なし・別の項目）も壊さずに残す。"""
    gh = FakeGH()
    old = {"items": [dict(item(5), by=None)]}
    old["items"][0].pop("by")
    gh.files[f"{DIR}/list.json"] = (json.dumps(old).encode(), "s0")
    gh.files[f"{DIR}/{name(5)}"] = (b"p", "s1")
    ghpub.publish(gh, DIR, name(6), b"p", item(6))
    assert [x["ft"] for x in items_of(gh)] == [6, 5] and "by" not in items_of(gh)[1]


def test_rejects_bad_name():
    with pytest.raises(ghpub.GitHubError):
        ghpub.publish(FakeGH(), DIR, "../../etc.png", b"p", item(1))
