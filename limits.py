#!/usr/bin/env python
# coding: utf-8
"""回数制限（直近の一定時間に何回まで）。メモリ上だけで持つ（再起動で消える。個人向けの軽い保護）。"""
import threading
import time
from collections import defaultdict, deque


class SlidingWindow:
    """key ごとに、直近 window 秒の記録が max 回を超えないようにする。"""

    def __init__(self, max_hits: int, window: float, clock=time.monotonic):
        self.max, self.window, self.clock = max_hits, window, clock
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key, now):
        q = self._hits[key]
        while q and now - q[0] >= self.window:
            q.popleft()
        if not q:
            self._hits.pop(key, None)
        return q

    def allowed(self, key) -> bool:
        """まだ回数に余裕があるか（記録はしない）。"""
        with self._lock:
            return len(self._trim(key, self.clock())) < self.max

    def hit(self, key) -> bool:
        """1回分を記録する。上限を超える場合は記録せず False。"""
        with self._lock:
            now = self.clock()
            q = self._trim(key, now)
            if len(q) >= self.max:
                return False
            self._hits[key].append(now)
            return True

    def retry_after(self, key) -> int:
        """次に1回使えるまでの秒数（余裕があれば 0）。"""
        with self._lock:
            now = self.clock()
            q = self._trim(key, now)
            if len(q) < self.max:
                return 0
            return max(1, int(self.window - (now - q[0])) + 1)
