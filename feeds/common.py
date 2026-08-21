"""Shared feed plumbing: provider health, fire dedupe, sharding."""
import threading
import time


class ProviderHealth:
    def __init__(self, name: str, bench_threshold: int = 3,
                 bench_window_s: int = 300, bench_seconds: int = 600):
        self.name = name
        self.bench_threshold = max(1, bench_threshold)
        self.bench_window_s = bench_window_s
        self.bench_seconds = bench_seconds
        self._fails: list[float] = []
        self._benched_until = 0.0
        self.ok_count = 0
        self.fail_count = 0
        self.last_event_ts = 0.0
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return time.time() >= self._benched_until

    def record_ok(self) -> None:
        with self._lock:
            self.ok_count += 1
            self.last_event_ts = time.time()
            self._fails.clear()

    def record_fail(self) -> bool:
        with self._lock:
            self.fail_count += 1
            now = time.time()
            self._fails = [t for t in self._fails if now - t < self.bench_window_s]
            self._fails.append(now)
            if len(self._fails) >= self.bench_threshold:
                self._benched_until = now + self.bench_seconds
                self._fails.clear()
                return True
            return False

    def note_event(self) -> None:
        with self._lock:
            self.last_event_ts = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "available": self.available,
                "ok": self.ok_count,
                "fail": self.fail_count,
                "last_event_age_s": round(time.time() - self.last_event_ts, 1)
                if self.last_event_ts else None,
            }


class FireDedupe:
    def __init__(self, ttl_s: int = 300):
        self.ttl_s = ttl_s
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def seen(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            self._seen = {k: t for k, t in self._seen.items()
                          if now - t < self.ttl_s}
            if key in self._seen:
                return True
            self._seen[key] = now
            return False


def shard(items: list, n: int) -> list[list]:
    n = max(1, n)
    return [list(items[i::n]) for i in range(n)]
