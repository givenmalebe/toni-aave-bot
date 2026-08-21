import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feeds.common import ProviderHealth, FireDedupe, shard


def test_provider_benches_after_threshold():
    ph = ProviderHealth("p1", bench_threshold=3, bench_window_s=300, bench_seconds=600)
    assert ph.available
    benched = False
    for _ in range(3):
        benched = ph.record_fail()
    assert benched and not ph.available
    ph._benched_until = time.time() - 1
    assert ph.available


def test_ok_resets_fail_streak():
    ph = ProviderHealth("p1", bench_threshold=3)
    ph.record_fail(); ph.record_fail()
    ph.record_ok()
    assert not ph.record_fail()


def test_dedupe_within_ttl():
    d = FireDedupe(ttl_s=60)
    assert not d.seen("k")
    assert d.seen("k")


def test_shard_round_robin():
    s = shard(list(range(7)), 3)
    assert [len(x) for x in s] == [3, 2, 2]
    assert sorted(sum(s, [])) == list(range(7))
