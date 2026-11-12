# 合言葉の確認と回数制限のテスト
import auth
from limits import SlidingWindow


def test_parse_and_check():
    keys = auth.parse_keys(f"yamada:{auth.hash_key('secret-1')}, sato:{auth.hash_key('secret-2').upper()},broken,x:zz")
    assert sorted(keys.values()) == ["sato", "yamada"]
    assert auth.check("Bearer secret-1", keys) == "yamada"
    assert auth.check("Bearer  secret-2", keys) == "sato"
    for bad in (None, "", "secret-1", "Bearer wrong", "Basic secret-1", "Bearer"):
        assert auth.check(bad, keys) is None
    assert auth.check("Bearer secret-1", {}) is None        # 鍵が1つも設定されていないときは誰も通さない


def test_hash_is_not_the_key():
    assert "secret-1" not in auth.hash_key("secret-1") and len(auth.hash_key("secret-1")) == 64


def test_sliding_window():
    t = [0.0]
    w = SlidingWindow(2, 10.0, clock=lambda: t[0])
    assert w.hit("a") and w.hit("a") and not w.hit("a")
    assert w.hit("b")                       # 相手ごとに数える
    assert not w.allowed("a") and w.retry_after("a") >= 1
    t[0] = 10.5
    assert w.allowed("a") and w.hit("a") and w.retry_after("a") == 0
