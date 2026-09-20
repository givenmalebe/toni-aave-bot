"""sol_rpc failure semantics: no misleading "failed: None" when all endpoints
are circuit-broken, and soft-bypass so a single success self-heals."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import sol_scanner as s


def _resp(value):
    return SimpleNamespace(json=lambda: value)


def _all_closed(monkeypatch):
    monkeypatch.setattr(s, "_rpc_is_open", lambda url: False)


def _pipe(monkeypatch, rpcs, resp, exc=None):
    monkeypatch.setattr(s, "_sol_rpcs", lambda: rpcs)

    def fake_post(url, payload, timeout):
        if exc is not None:
            raise exc
        return resp

    monkeypatch.setattr(s, "_http_post", fake_post)


def test_no_endpoints_configured_reports_cleanly(monkeypatch):
    monkeypatch.setattr(s, "_sol_rpcs", lambda: [])
    with pytest.raises(RuntimeError) as ei:
        s.sol_rpc("getNumbers")
    assert "no RPC endpoints configured" in str(ei.value)


def test_all_broken_soft_bypass_recovers(monkeypatch):
    s._RPC_CIRCUIT.update({
        "https://a.example": (9, 10**12),
        "https://b.example": (9, 10**12),
    })
    _all_closed(monkeypatch)
    _pipe(monkeypatch, ["https://a.example", "https://b.example"],
          _resp({"result": "ok"}))
    try:
        res, url = s.sol_rpc("getNumbers", [], timeout=1)
        assert res == "ok"
        assert url == "https://a.example"
        assert "https://a.example" not in s._RPC_CIRCUIT
        assert "https://b.example" in s._RPC_CIRCUIT
    finally:
        s._RPC_CIRCUIT.clear()


def test_all_broken_all_fail_reports_cause_not_none(monkeypatch):
    s._RPC_CIRCUIT.clear()
    _all_closed(monkeypatch)
    _pipe(monkeypatch, ["https://a.example"],
          None, exc=ConnectionError("boom"))
    try:
        with pytest.raises(RuntimeError) as ei:
            s.sol_rpc("getNumbers", [], timeout=1)
        msg = str(ei.value)
        assert "failed:" in msg
        assert "boom" in msg
        assert "failed: None" not in msg
    finally:
        s._RPC_CIRCUIT.clear()


def test_open_endpoint_used_broken_ones_skipped(monkeypatch):
    s._RPC_CIRCUIT.update({"https://b.example": (9, 10**12)})
    monkeypatch.setattr(s, "_rpc_is_open",
                        lambda url: url == "https://a.example")
    _pipe(monkeypatch, ["https://a.example", "https://b.example"],
          _resp({"result": "ok"}))
    try:
        res, url = s.sol_rpc("getNumbers", [], timeout=1)
        assert res == "ok"
        assert url == "https://a.example"
    finally:
        s._RPC_CIRCUIT.clear()