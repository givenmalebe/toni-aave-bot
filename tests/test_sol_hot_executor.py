import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard
from sol_fees import LossBreaker, TipFloor


def _make_dash(sol_opps, funds_bot_sol=1.0):
    class FakeDash(dashboard.Dashboard):
        def __init__(self):
            self.state = {"sol": {"opportunities": sol_opps,
                                  "funds": {"bot": {"sol": funds_bot_sol},
                                            "sponsor": {"sol": 0.5}},
                                  "sol_price_usd": 100.0},
                          "shadow": {}}
            self._sol_hot_kick = None
            self._sol_kick_pubkeys = set()
            self.sol_tip_floor = TipFloor()
            self.sol_breaker = LossBreaker(threshold_lamports=10_000_000)
            self.submitted = []
        def log(self, *a, **k): pass
        def _sol_record(self, rec, skip=False): pass
        def _sol_maybe_submit(self, kind, opp, plan):
            self.submitted.append((kind, dict(opp), dict(plan)))
            return {"stage": "simulated", "detail": "dry-run"}
    return FakeDash()


class FakeKick:
    async def wait(self):
        pass

    def clear(self):
        pass


def _drain_one_cycle(fd, pubkeys):
    fd._sol_kick_pubkeys.update(pubkeys)
    fd._sol_hot_kick = FakeKick()
    # Run a single executor cycle by cancelling after first pass.
    async def run_once():
        task = asyncio.create_task(fd._sol_hot_executor())
        await asyncio.sleep(0.4)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(run_once())


def test_kick_submits_matching_opp():
    opp = {"obligation": "OblA", "protocol_id": "solend",
           "profit_usd": 3.0, "net_usd": 2.5,
           "plan": {"kind": "liq", "ready": True, "obligation": "OblA"}}
    fd = _make_dash([opp])
    _drain_one_cycle(fd, ["OblA"])
    assert len(fd.submitted) == 1
    kind, o, plan = fd.submitted[0]
    assert plan["obligation"] == "OblA"
    assert fd.state["shadow"]["sol_kick_submits"] == 1


def test_kick_without_opp_counts_and_skips():
    fd = _make_dash([])
    _drain_one_cycle(fd, ["Ghost"])
    assert not fd.submitted
    assert fd.state["shadow"]["sol_kick_no_opp"] == 1


def test_low_float_blocks_submit():
    opp = {"obligation": "OblB", "protocol_id": "solend",
           "profit_usd": 3.0,
           "plan": {"kind": "liq", "ready": True, "obligation": "OblB"}}
    fd = _make_dash([opp], funds_bot_sol=0.05)
    _drain_one_cycle(fd, ["OblB"])
    assert not fd.submitted
    assert fd.state["shadow"]["sol_kick_low_float"] == 1


def test_stub_kamino_plan_gets_real_minimal_plan():
    opp = {"obligation": "OblC", "protocol_id": "kamino",
           "profit_usd": 4.0,
           "plan": {"kind": "liq", "protocol_id": "kamino",
                    "ready": False, "obligation": "OblC",
                    "note": "kamino plan builder not yet wired"}}
    fd = _make_dash([opp])
    _drain_one_cycle(fd, ["OblC"])
    assert len(fd.submitted) == 1
    _, _, plan = fd.submitted[0]
    assert plan["protocol_id"] == "kamino"
    assert plan["ready"] is True
    assert plan["execute"] == "kamino-jito"


def test_calibrated_fees_applied_to_plan():
    fd = _make_dash([])
    fd.sol_tip_floor.update({"data": [{
        "landed_tips_50th_percentile": 0.001,
        "landed_tips_25th_percentile": 0.0005,
        "landed_tips_75th_percentile": 0.002,
        "landed_tips_95th_percentile": 0.005}]})
    plan = {}
    opp = {"net_usd": 5.0}
    fd._apply_calibrated_fees(plan, opp)
    lam = plan["jito_tip_lamports"]
    # $5 pre-tip at $100/SOL -> share cap $1.50 = 1.5M lam; p50=1M wins.
    assert lam == 1_000_000


def test_failed_live_outcome_feeds_breaker():
    fd = _make_dash([])
    fd._record_live_outcome({"stage": "error"},
                            {"jito_tip_lamports": 2_000_000})
    fd._record_live_outcome({"stage": "sent"},
                            {"jito_tip_lamports": 2_000_000})
    assert fd.sol_breaker.lost_lamports == 2_000_000
    assert not fd.sol_breaker.paused
