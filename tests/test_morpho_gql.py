import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "aave-v4-liquidation-bot"))

import time
import eth_lending.morpho as morpho


def test_gql_backoff_after_repeated_errors(monkeypatch):
    calls = {"n": 0}
    class FakeResp:
        def raise_for_status(self):
            raise RuntimeError("400 Client Error")
    def fake_post(*a, **k):
        calls["n"] += 1
        return FakeResp()
    monkeypatch.setattr(morpho.requests, "post", fake_post)
    morpho._gql_fail_until = 0.0
    a, errs = morpho._gql_positions()
    b, errs_b = morpho._gql_positions()
    first_calls = calls["n"]
    assert first_calls >= 1 and b == [] and errs_b
    morpho._gql_fail_until = time.time() + 600
    before = calls["n"]
    c, errs2 = morpho._gql_positions()
    assert calls["n"] == before and c == []
