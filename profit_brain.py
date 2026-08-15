#!/usr/bin/env python3
"""Online deep profit brain for TONI MEV dashboard.

Architecture: residual MLP (20 → 64 → 64 → 32 → 2) with Adam, ReLU, and
experience replay. Head-0 = P(should act / beat competitors), head-1 =
expected net USD (scaled). Trains continuously from competitor misses,
near-miss arb gaps, and our broadcast outcomes — then emits policy knobs
that tighten floors / cadence so we hunt where searchers are weak.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "profit_brain_state.json")
FEAT_DIM = 20
HIDDEN = (64, 64, 32)
LR = 1e-3
REPLAY = 512
BATCH = 32
PROFIT_SCALE = 100.0  # USD → network target
MODEL_NAME = "TONI-DeepProfit-v1 (residual MLP + Adam + replay)"


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -40, 40)))


def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(x, 0.0)


class OnlineDeepMLP:
    """Small deep net trained online; persists weights to disk."""

    def __init__(self, rng: Optional[np.random.Generator] = None):
        self.rng = rng or np.random.default_rng(42)
        self.steps = 0
        self.loss_ema = 0.5
        self.acc_ema = 0.5
        self.updated = 0
        self.replay: Deque[Tuple[np.ndarray, np.ndarray]] = deque(maxlen=REPLAY)
        self._init_weights()
        self._init_adam()

    def _init_weights(self) -> None:
        dims = [FEAT_DIM, *HIDDEN, 2]
        self.W: List[np.ndarray] = []
        self.b: List[np.ndarray] = []
        for i in range(len(dims) - 1):
            # He init
            fan = dims[i]
            self.W.append(self.rng.normal(0, math.sqrt(2.0 / fan),
                                          (dims[i], dims[i + 1])).astype(np.float64))
            self.b.append(np.zeros(dims[i + 1], dtype=np.float64))

    def _init_adam(self) -> None:
        self.mW = [np.zeros_like(w) for w in self.W]
        self.vW = [np.zeros_like(w) for w in self.W]
        self.mb = [np.zeros_like(b) for b in self.b]
        self.vb = [np.zeros_like(b) for b in self.b]
        self.t = 0

    def forward(self, x: np.ndarray) -> Tuple[np.ndarray, List[np.ndarray]]:
        """x: (B, F) → logits (B, 2), caches for backprop."""
        caches: List[np.ndarray] = [x]
        h = x
        for i, (W, b) in enumerate(zip(self.W, self.b)):
            z = h @ W + b
            if i < len(self.W) - 1:
                # residual on matching dims
                a = _relu(z)
                if h.shape[-1] == a.shape[-1]:
                    a = a + h
                h = a
            else:
                h = z
            caches.append(h)
        return h, caches

    def predict(self, x: np.ndarray) -> Tuple[float, float]:
        if x.ndim == 1:
            x = x.reshape(1, -1)
        logits, _ = self.forward(x.astype(np.float64))
        p = float(_sigmoid(logits[0, 0]))
        net = float(logits[0, 1] * PROFIT_SCALE)
        return p, net

    def _backward(self, caches: List[np.ndarray], dlogits: np.ndarray
                  ) -> Tuple[List[np.ndarray], List[np.ndarray]]:
        dW = [None] * len(self.W)
        db = [None] * len(self.b)
        dh = dlogits
        for i in reversed(range(len(self.W))):
            h_prev = caches[i]
            z_out = caches[i + 1]
            if i < len(self.W) - 1:
                # d/da through relu(+residual)
                pre = h_prev @ self.W[i] + self.b[i]
                da = dh.copy()
                # residual path
                if h_prev.shape[-1] == z_out.shape[-1]:
                    # z_out = relu(pre) + h_prev
                    drelu = da * (pre > 0)
                    dpre = drelu
                    dh_res = da
                else:
                    dpre = da * (pre > 0)
                    dh_res = 0.0
                dW[i] = h_prev.T @ dpre
                db[i] = dpre.sum(axis=0)
                dh = dpre @ self.W[i].T + dh_res
            else:
                dW[i] = h_prev.T @ dh
                db[i] = dh.sum(axis=0)
                dh = dh @ self.W[i].T
        return dW, db  # type: ignore

    def _adam_step(self, dW, db, lr: float = LR) -> None:
        self.t += 1
        b1, b2, eps = 0.9, 0.999, 1e-8
        for i in range(len(self.W)):
            self.mW[i] = b1 * self.mW[i] + (1 - b1) * dW[i]
            self.vW[i] = b2 * self.vW[i] + (1 - b2) * (dW[i] ** 2)
            self.mb[i] = b1 * self.mb[i] + (1 - b1) * db[i]
            self.vb[i] = b2 * self.vb[i] + (1 - b2) * (db[i] ** 2)
            mW = self.mW[i] / (1 - b1 ** self.t)
            vW = self.vW[i] / (1 - b2 ** self.t)
            mb = self.mb[i] / (1 - b1 ** self.t)
            vb = self.vb[i] / (1 - b2 ** self.t)
            self.W[i] -= lr * mW / (np.sqrt(vW) + eps)
            self.b[i] -= lr * mb / (np.sqrt(vb) + eps)

    def train_batch(self, X: np.ndarray, Y: np.ndarray) -> float:
        """Y[:,0]=act label 0/1, Y[:,1]=net_usd/PROFIT_SCALE."""
        logits, caches = self.forward(X)
        p = _sigmoid(logits[:, 0:1])
        # BCE + Huber on profit head
        y0 = Y[:, 0:1]
        y1 = Y[:, 1:2]
        bce = -(y0 * np.log(p + 1e-8) + (1 - y0) * np.log(1 - p + 1e-8))
        err = logits[:, 1:2] - y1
        huber = np.where(np.abs(err) < 1.0, 0.5 * err ** 2, np.abs(err) - 0.5)
        loss = float(np.mean(bce + 0.5 * huber))
        # grads
        dp = (p - y0) / max(1, X.shape[0])  # dL/dlogit0 via sigmoid
        dprofit = np.where(np.abs(err) < 1.0, err, np.sign(err)) / max(1, X.shape[0])
        dprofit *= 0.5
        dlogits = np.concatenate([dp, dprofit], axis=1)
        dW, db = self._backward(caches, dlogits)
        self._adam_step(dW, db)
        self.steps += 1
        self.loss_ema = 0.95 * self.loss_ema + 0.05 * loss
        pred = (p.ravel() >= 0.5).astype(np.float64)
        acc = float(np.mean(pred == y0.ravel()))
        self.acc_ema = 0.95 * self.acc_ema + 0.05 * acc
        self.updated = int(time.time())
        return loss

    def observe(self, x: np.ndarray, act: float, net_usd: float,
                n_replay: int = 2) -> float:
        y = np.array([float(act), float(net_usd) / PROFIT_SCALE], dtype=np.float64)
        self.replay.append((x.astype(np.float64), y))
        loss = self.train_batch(x.reshape(1, -1), y.reshape(1, -1))
        if len(self.replay) >= 8:
            for _ in range(n_replay):
                idx = self.rng.choice(len(self.replay),
                                      size=min(BATCH, len(self.replay)),
                                      replace=False)
                X = np.stack([self.replay[i][0] for i in idx])
                Y = np.stack([self.replay[i][1] for i in idx])
                loss = self.train_batch(X, Y)
        return loss

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": MODEL_NAME,
            "steps": self.steps,
            "loss_ema": round(self.loss_ema, 5),
            "acc_ema": round(self.acc_ema, 4),
            "updated": self.updated,
            "t": self.t,
            "W": [w.tolist() for w in self.W],
            "b": [b.tolist() for b in self.b],
            "replay": [
                {"x": x.tolist(), "y": y.tolist()}
                for x, y in list(self.replay)[-REPLAY:]
            ],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OnlineDeepMLP":
        m = cls()
        if not d or "W" not in d:
            return m
        try:
            m.W = [np.array(w, dtype=np.float64) for w in d["W"]]
            m.b = [np.array(b, dtype=np.float64) for b in d["b"]]
            m.steps = int(d.get("steps") or 0)
            m.loss_ema = float(d.get("loss_ema") or 0.5)
            m.acc_ema = float(d.get("acc_ema") or 0.5)
            m.updated = int(d.get("updated") or 0)
            m.t = int(d.get("t") or 0)
            m._init_adam()
            for item in d.get("replay") or []:
                m.replay.append((
                    np.array(item["x"], dtype=np.float64),
                    np.array(item["y"], dtype=np.float64),
                ))
        except Exception:
            return cls()
        return m


# --------------------------------------------------------------------------- API

_brain: Optional[OnlineDeepMLP] = None


def get_brain() -> OnlineDeepMLP:
    global _brain
    if _brain is None:
        _brain = load()
    return _brain


def load() -> OnlineDeepMLP:
    if not os.path.exists(STATE_PATH):
        return OnlineDeepMLP()
    try:
        with open(STATE_PATH) as f:
            return OnlineDeepMLP.from_dict(json.load(f))
    except Exception:
        return OnlineDeepMLP()


def save(brain: Optional[OnlineDeepMLP] = None) -> None:
    b = brain or get_brain()
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(b.to_dict(), f)
    except OSError:
        pass


def features_from_state(state: Dict[str, Any]) -> np.ndarray:
    """Build FEAT_DIM vector from live dashboard state."""
    intel = state.get("intel") or {}
    mem = state.get("mempool") or {}
    mev = mem.get("mev") or intel.get("mev") or {}
    cm = state.get("competitors_meta") or {}
    arb = state.get("arb") or {}
    am = arb.get("meta") or {}
    bc = state.get("broadcast") or {}
    funds = state.get("funds") or {}
    bot = (funds.get("bot") or funds.get("BOT") or {}) if isinstance(funds, dict) else {}

    gas = float(state.get("gas_gwei") or 0)
    eth = float(state.get("eth_price_usd") or 0)
    last = intel.get("last") or {}
    hour = time.gmtime(int(time.time()) + 2 * 3600).tm_hour
    dow = time.gmtime(int(time.time()) + 2 * 3600).tm_wday
    hours = intel.get("hours") or {}
    hour_act = float(hours.get(hour) or hours.get(str(hour)) or 0)
    max_h = max([float(v) for v in hours.values()] or [1.0])

    miss = float(cm.get("missed_by_us") or 0)
    cnt = float(cm.get("count_1h") or 0) or 1.0
    miss_rate = miss / cnt
    near_hints = bc.get("near_miss_hints") or []
    best_near = max([float(h.get("best_weth") or -1) for h in near_hints] or [-1.0])
    opps = state.get("opportunities") or []
    best_opp = max([float(o.get("profit_usd") or 0) for o in opps] or [0.0])

    x = np.array([
        math.log1p(gas) / 10.0,
        eth / 5000.0,
        math.sin(2 * math.pi * hour / 24),
        math.cos(2 * math.pi * hour / 24),
        dow / 6.0,
        float(intel.get("readiness") or 0) / 100.0,
        min(float(mev.get("liq") or 0) / 20.0, 1.5),
        min(float(mev.get("router") or 0) / 50.0, 1.5),
        min(float(last.get("spoke_txs") or len(mem.get("spoke_txs") or [])) / 20.0, 1.5),
        miss_rate,
        min(float(cm.get("avg_gas") or 0) / 1_000_000.0, 2.0),
        max(min(best_near, 0.05), -0.2) * 10.0,
        1.0 if int(am.get("actionable") or 0) > 0 else 0.0,
        1.0 if bc.get("edge_bias") else 0.0,
        1.0 if bc.get("peak_hour") else 0.0,
        min(len(opps) / 10.0, 1.5),
        min(best_opp / 100.0, 2.0),
        min(float(bot.get("eth") or 0) / 0.5, 2.0),
        min(float(cm.get("unique_searchers") or 0) / 10.0, 1.5),
        hour_act / max(max_h, 1.0),
    ], dtype=np.float64)
    assert x.shape == (FEAT_DIM,)
    return x


def learn_competitor(state: Dict[str, Any], rec: Dict[str, Any]) -> Dict[str, Any]:
    """Train on a confirmed competitor liquidation.

    Missed + profitable → we should have acted (label 1).
    Contested crowded long-tail miss → still learn expected net.
    """
    brain = get_brain()
    x = features_from_state(state)
    net = float(rec.get("net_est_usd") or rec.get("est_profit_usd") or 0)
    missed = bool(rec.get("missed_by_us"))
    edge = bool(rec.get("edge") or (rec.get("status") == "long-tail"))
    # Positive label when the opp was real profit we missed, or thin-edge we want
    act = 1.0 if (missed and net > 0) or (edge and net > 2) else 0.0
    if not missed and net > 0:
        # competitor took it — still useful as positive "opp existed"
        act = 0.7
    loss = brain.observe(x, act, net)
    if brain.steps % 5 == 0:
        save(brain)
    return {"loss": loss, "act": act, "net": net}


def learn_arb_near(state: Dict[str, Any], row: Dict[str, Any]) -> None:
    brain = get_brain()
    x = features_from_state(state)
    net = float(row.get("net_usd") or row.get("profit_usd") or 0)
    act = 1.0 if row.get("actionable") else 0.0
    brain.observe(x, act, net, n_replay=1)


def learn_broadcast(state: Dict[str, Any], kind: str, rec: Dict[str, Any]) -> None:
    brain = get_brain()
    x = features_from_state(state)
    stage = (rec.get("stage") or rec.get("status") or "").lower()
    ok = stage in ("sent", "ok", "simulated", "dry-run", "cast-ok")
    profit = float(rec.get("profit_usd") or rec.get("net_usd") or 0)
    if stage.startswith("skip"):
        act, profit = 0.0, profit
    elif ok:
        act = 1.0
    else:
        act = 0.0
        profit = min(profit, 0.0)
    brain.observe(x, act, profit)
    save(brain)


def policy(state: Dict[str, Any]) -> Dict[str, Any]:
    """Emit profit-seeking knobs + human advice."""
    brain = get_brain()
    x = features_from_state(state)
    p_act, exp_net = brain.predict(x)
    conf = abs(p_act - 0.5) * 2.0  # 0..1

    # Multipliers: high act-prob → lower floors / faster cadence
    # high competition (many searchers, high miss) → prefer edge, raise floors
    cm = state.get("competitors_meta") or {}
    miss_rate = (float(cm.get("missed_by_us") or 0) /
                 max(1.0, float(cm.get("count_1h") or 0)))
    crowded = float(cm.get("unique_searchers") or 0) >= 3

    min_liq_mult = 1.0
    min_arb_mult = 1.0
    cadence_mult = 1.0
    prefer_edge = False
    advice = "observe"

    if brain.steps < 20:
        advice = "warmup — collecting competitor + near-miss labels"
        min_liq_mult = 1.0
    elif p_act >= 0.62 and exp_net > 5:
        advice = "hunt — model expects beatable edge; lower floors"
        min_liq_mult = 0.75
        min_arb_mult = 0.85
        cadence_mult = 0.65
        prefer_edge = True
    elif crowded or miss_rate > 0.4:
        advice = "evade crowded searchers — long-tail only, raise floors"
        min_liq_mult = 1.35
        min_arb_mult = 1.25
        cadence_mult = 1.1
        prefer_edge = True
    elif p_act < 0.35:
        advice = "stand down — low win probability vs competitors"
        min_liq_mult = 1.5
        min_arb_mult = 1.4
        cadence_mult = 1.25
    elif exp_net < 0:
        advice = "gas regime hostile — wait for cheaper blocks"
        min_liq_mult = 1.2
        min_arb_mult = 1.3
        cadence_mult = 1.15
    else:
        advice = "selective — take edge + clear net-positive only"
        prefer_edge = True
        cadence_mult = 0.85

    return {
        "model": MODEL_NAME,
        "act_prob": round(p_act, 4),
        "exp_net_usd": round(exp_net, 2),
        "confidence": round(conf, 3),
        "advice": advice,
        "prefer_edge": prefer_edge,
        "min_liq_mult": round(min_liq_mult, 3),
        "min_arb_mult": round(min_arb_mult, 3),
        "cadence_mult": round(cadence_mult, 3),
        "steps": brain.steps,
        "loss_ema": round(brain.loss_ema, 5),
        "acc_ema": round(brain.acc_ema, 4),
        "replay": len(brain.replay),
        "updated": brain.updated,
    }
