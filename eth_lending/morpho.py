"""Morpho Blue liquidatable positions via official GraphQL + on-chain verify.

Live send: Aave V3 flash of the loan asset → Morpho Blue `liquidate`.
"""
from __future__ import annotations

import time

import requests
import liquidation_bot as lb

from . import executor as ex
from . import util as u

ID = "morpho"
LABEL = "Morpho"
# Canonical Morpho Blue singleton (docs + deployments).
MORPHO = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
GRAPHQL = "https://api.morpho.org/graphql"
_GQL_BACKOFF_S = 600
_gql_fail_until = 0.0
WAD = 10 ** 18
ORACLE_SCALE = 10 ** 36
MAX_LIF = int(1.15e18)
LIQ_CURSOR = int(0.3e18)

_position_query = """
query LiqPositions {
  marketPositions(
    first: 80
    orderBy: HealthFactor
    orderDirection: Asc
    where: { chainId_in: [1] }
  ) {
    items {
      healthFactor
      user { address }
      market {
        uniqueKey
        marketId
        lltv
        irmAddress
        oracleAddress
        liquidationIncentiveFactor
        loanAsset { address symbol decimals }
        collateralAsset { address symbol decimals }
      }
      state {
        collateral
        borrowAssets
        borrowShares
        borrowAssetsUsd
        collateralUsd
      }
    }
  }
}
"""


def enabled() -> bool:
    return u.has_code(MORPHO)


def _lif(lltv: int) -> int:
    gap = (WAD - int(lltv)) * LIQ_CURSOR // WAD
    denom = WAD - gap
    if denom <= 0:
        return MAX_LIF
    raw = (WAD * WAD) // denom
    return min(MAX_LIF, raw)


def _gql_positions() -> tuple[list[dict], list[str]]:
    global _gql_fail_until
    errs = []
    if time.time() < _gql_fail_until:
        return [], ["morpho gql backing off"]
    queries = [_position_query, """
query LiqPositionsSimple {
  marketPositions(first: 80, where: { chainId_in: [1] }) {
    items {
      healthFactor
      user { address }
      market {
        uniqueKey
        lltv
        irmAddress
        oracleAddress
        loanAsset { address symbol decimals }
        collateralAsset { address symbol decimals }
      }
      state {
        collateral
        borrowAssets
        borrowShares
        borrowAssetsUsd
        collateralUsd
      }
    }
  }
}
"""]
    for q in queries:
        try:
            r = requests.post(
                GRAPHQL,
                json={"query": q},
                headers={"Content-Type": "application/json",
                         "User-Agent": getattr(lb, "_UA", "toni-bot")},
                timeout=14,
            )
            r.raise_for_status()
            js = r.json()
            if js.get("errors"):
                err0 = js["errors"][0]
                errs.append(str(err0.get("message") or err0)[:160])
                continue
            items = (((js.get("data") or {}).get("marketPositions") or {}).get("items")
                     or [])
            return items, errs
        except Exception as e:  # noqa: BLE001
            body = ""
            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    body = str(getattr(resp, "text", ""))[:200]
                except Exception:  # noqa: BLE001
                    body = ""
            errs.append((str(e)[:160] + (f" | {body}" if body else "")))
    if errs:
        _gql_fail_until = time.time() + _GQL_BACKOFF_S
    return [], errs


def _onchain_params(market_id: str) -> dict | None:
    mid = (market_id or "").replace("0x", "").rjust(64, "0")
    try:
        raw = u.call(MORPHO, u.SEL["idToMarketParams"] + mid)
        ws = u.words(raw)
        if len(ws) < 5:
            return None
        return {
            "loan": u.addr_word(ws[0]),
            "coll": u.addr_word(ws[1]),
            "oracle": u.addr_word(ws[2]),
            "irm": u.addr_word(ws[3]),
            "lltv": int(ws[4], 16),
        }
    except Exception:
        return None


def _onchain_position(market_id: str, user: str) -> dict | None:
    mid = (market_id or "").replace("0x", "").rjust(64, "0")
    try:
        raw = u.call(MORPHO, u.SEL["position"] + mid + u.pad_addr(user)[2:])
        ws = u.words(raw)
        if len(ws) < 3:
            return None
        mraw = u.call(MORPHO, u.SEL["market"] + mid)
        mw = u.words(mraw)
        if len(mw) < 4:
            return None
        borrow_shares = int(ws[1], 16)
        coll = int(ws[2], 16)
        tot_borrow_assets = int(mw[2], 16)
        tot_borrow_shares = int(mw[3], 16)
        borrowed = 0
        if tot_borrow_shares > 0 and borrow_shares > 0:
            # toAssetsUp
            borrowed = (borrow_shares * tot_borrow_assets + tot_borrow_shares - 1) // tot_borrow_shares
        return {
            "borrow_shares": borrow_shares,
            "collateral": coll,
            "borrowed": borrowed,
            "tot_borrow_assets": tot_borrow_assets,
            "tot_borrow_shares": tot_borrow_shares,
        }
    except Exception:
        return None


def _oracle_price(oracle: str) -> int:
    try:
        raw = u.call(oracle, u.SEL["price"])
        return int(raw, 16)
    except Exception:
        return 0


def _hf_onchain(params: dict, pos: dict) -> float | None:
    borrowed = int(pos.get("borrowed") or 0)
    if borrowed <= 0:
        return None
    price = _oracle_price(params.get("oracle") or "")
    if price <= 0:
        return None
    coll_in_loan = int(pos.get("collateral") or 0) * price // ORACLE_SCALE
    max_borrow = coll_in_loan * int(params.get("lltv") or 0) // WAD
    return max_borrow / borrowed if borrowed else None


def harvest_users(from_block: int, to_block: int) -> dict:
    users: dict[str, int] = {}
    n_logs = 0
    errors = []
    for topic, idx in ((u.TOPIC["morpho_borrow"], 2), (u.TOPIC["morpho_liq"], 3)):
        logs, err = lb.get_logs_chunked(from_block, to_block, MORPHO, [topic])
        errors.extend(err)
        n_logs += len(logs)
        for lg in logs:
            topics = lg.get("topics") or []
            if len(topics) > idx:
                usr = u.addr_word(str(topics[idx]).replace("0x", "").rjust(64, "0"))
                if usr.startswith("0x") and int(usr, 16):
                    users[usr] = int(lg.get("blockNumber") or "0x0", 16)
    cached = u.load_users(ID)
    for a in cached:
        users.setdefault(a, 0)
    u.save_users(ID, list(users.keys()) + cached)
    return {
        "users": list(users.keys()),
        "n_logs": n_logs,
        "errors": errors[:8],
        "from_block": from_block,
        "to_block": to_block,
    }


def _score_item(item: dict, gas_gwei: float, eth_usd: float,
                leftover_gql: str = "") -> dict | None:
    user = str(((item.get("user") or {}).get("address") or "")).lower()
    if not user.startswith("0x"):
        return None
    market = item.get("market") or {}
    state = item.get("state") or {}
    mid = market.get("uniqueKey") or market.get("marketId") or ""
    hf_gql = item.get("healthFactor")
    try:
        hf_f = float(hf_gql)
    except (TypeError, ValueError):
        hf_f = None
    params = _onchain_params(mid) if mid else None
    pos = _onchain_position(mid, user) if mid else None
    leftover = leftover_gql if leftover_gql else ""
    if params and pos:
        leftover = ""
        on = _hf_onchain(params, pos)
        if on is not None:
            hf_f = on
            if on >= 1.0:
                return None  # on-chain healthy — don't trust stale indexer
        elif pos.get("borrowed"):
            leftover = (leftover + " · " if leftover else "") + "oracle.price() failed; used GraphQL HF"
    if hf_f is None or hf_f >= 1.0:
        return None
    coll = market.get("collateralAsset") or {}
    loan = market.get("loanAsset") or {}
    coll_addr = (params["coll"] if params else (coll.get("address") or "")).lower()
    debt_addr = (params["loan"] if params else (loan.get("address") or "")).lower()
    try:
        coll_usd = float(state.get("collateralUsd") or 0)
        debt_usd = float(state.get("borrowAssetsUsd") or 0)
    except (TypeError, ValueError):
        coll_usd, debt_usd = 0.0, 0.0
    if debt_usd <= 0 and pos and params:
        debt_amt = pos.get("borrowed") or 0
        dec = int(loan.get("decimals") or lb.token_decimals(debt_addr) or 18)
        px = lb.asset_price_usd(debt_addr)
        debt_usd = (debt_amt / (10 ** dec)) * px if px else 0.0
        coll_amt = pos.get("collateral") or 0
        cdec = int(coll.get("decimals") or lb.token_decimals(coll_addr) or 18)
        cpx = lb.asset_price_usd(coll_addr)
        coll_usd = (coll_amt / (10 ** cdec)) * cpx if cpx else 0.0
    if debt_usd < u.DUST_USD:
        return None
    lltv = 0
    if params:
        lltv = int(params["lltv"])
    else:
        try:
            v = float(market.get("lltv") or 0)
            lltv = int(v * WAD) if 0 < v <= 1.0 else int(v)
        except (TypeError, ValueError):
            lltv = 0
    lif_raw = market.get("liquidationIncentiveFactor")
    try:
        lif = int(float(lif_raw) * WAD) if lif_raw and float(lif_raw) < 10 else int(float(lif_raw or 0))
    except (TypeError, ValueError):
        lif = 0
    if lif < WAD:
        lif = _lif(lltv) if lltv else 0
    if lif <= WAD:
        leftover = (leftover + " · " if leftover else "") + "LIF not decoded — no fake bonus"
        return None
    bonus_pct = lif / WAD - 1.0
    cover = min(debt_usd, coll_usd / (lif / WAD) if lif else 0)
    bonus_usd = cover * bonus_pct
    gas = u.gas_usd(gas_gwei, eth_usd)
    extra = u.apply_flash_fee(cover, bonus_usd, 0.0, gas)
    if extra["net_usd"] <= 0:
        return None
    rec = u.opp_base(protocol_id=ID, protocol_label=LABEL, user=user, live_ok=True,
                     leftover=leftover)
    rec.update({
        "hf": str(int(hf_f * 1e18)),
        "coll_usd": round(coll_usd, 2),
        "debt_usd": round(debt_usd, 2),
        "cover_usd": round(cover, 2),
        "bonus_usd": round(bonus_usd, 2),
        "gas_usd": round(gas, 2),
        "coll_sym": (coll.get("symbol") or lb.token_sym(coll_addr)),
        "debt_sym": (loan.get("symbol") or lb.token_sym(debt_addr)),
        "coll_addr": coll_addr,
        "debt_addr": debt_addr,
        "collateralAsset": coll_addr,
        "debtAsset": debt_addr,
        "venue": "blue",
        "market_id": mid,
        "liquidatable": True,
        "source": "morpho-api+rpc",
        **extra,
    })
    seized = 0
    repaid_shares = int((pos or {}).get("borrow_shares") or 0)
    if repaid_shares <= 0:
        seized = int((pos or {}).get("collateral") or 0)
    flash_wei = int((pos or {}).get("borrowed") or 0)
    if flash_wei <= 0:
        w = ex.usd_to_wei(debt_addr, cover)
        flash_wei = int(w or 0)
    if flash_wei > 0:
        flash_wei = flash_wei * 101 // 100
    oracle = (params or {}).get("oracle") or ""
    irm = (params or {}).get("irm") or ""
    fp = ex.plan_morpho(
        user=user,
        coll=coll_addr,
        loan=debt_addr,
        oracle=oracle,
        irm=irm,
        lltv=int(lltv or 0),
        seized=seized,
        repaid_shares=repaid_shares,
        flash_wei=flash_wei,
    )
    fp["market_id"] = mid
    ex.apply_plan(rec, fp)
    rec["debtToCover"] = str(flash_wei)
    return rec


def scan_liquidatable(users, *, gas_gwei: float, eth_usd: float,
                      contested=None, recent_comp=None, max_users: int = 80) -> dict:
    contested = {str(x).lower() for x in (contested or [])}
    recent_comp = {str(x).lower() for x in (recent_comp or [])}
    items, gql_err = _gql_positions()
    leftover_gql = ""
    opps, watch, skipped = [], [], {
        "dust": 0, "healthy": 0, "no_account": 0, "negative": 0, "rpc": 0,
        "gql": 1 if gql_err else 0,
    }
    scanned = 0
    if items:
        for item in items[:max_users]:
            scanned += 1
            user = str(((item.get("user") or {}).get("address") or "")).lower()
            try:
                hf_f = float(item.get("healthFactor"))
            except (TypeError, ValueError):
                hf_f = None
            st = item.get("state") or {}
            watch.append({
                "user": user,
                "hf": str(int(hf_f * 1e18)) if hf_f is not None else str((1 << 256) - 1),
                "coll_usd": round(float(st.get("collateralUsd") or 0), 2),
                "debt_usd": round(float(st.get("borrowAssetsUsd") or 0), 2),
                "protocol_id": ID,
                "protocol": LABEL,
            })
            if hf_f is None or hf_f >= 1.0:
                skipped["healthy"] += 1
                continue
            rec = _score_item(item, gas_gwei, eth_usd, leftover_gql)
            if not rec:
                skipped["negative"] += 1
                continue
            race = rec["user"] in contested or rec["user"] in recent_comp
            rec["contested"] = race
            rec["recent_competitor"] = rec["user"] in recent_comp
            rec["race"] = race
            opps.append(rec)
    elif users:
        skipped["no_account"] += 1  # borrowers without market id cannot be HF-checked honestly
    watch.sort(key=lambda r: int(r.get("hf") or 0))
    if opps:
        u.save_users(ID, [o["user"] for o in opps] + u.uniq_addrs(users, 40))
    extra_err = gql_err[:4]
    return {
        "opps": opps,
        "watch": watch[:12],
        "scanned": scanned or len(u.uniq_addrs(users, max_users)),
        "skipped": skipped,
        "errors": extra_err,
        "leftover": (extra_err[0] if extra_err and not items
                     else ("Morpho GraphQL empty — no invented HF from borrow logs only"
                           if not items else "")),
    }


def _parse_liq(lg) -> dict | None:
    topics = lg.get("topics") or []
    if len(topics) < 4:
        return None
    t0 = str(topics[0]).lower()
    if t0 != u.TOPIC["morpho_liq"]:
        return None
    mid = str(topics[1])
    searcher = u.addr_word(str(topics[2]).replace("0x", "").rjust(64, "0"))
    user = u.addr_word(str(topics[3]).replace("0x", "").rjust(64, "0"))
    ws = u.words(lg.get("data"))
    repaid = int(ws[0], 16) if ws else 0
    seized = int(ws[2], 16) if len(ws) > 2 else 0
    params = _onchain_params(mid)
    coll_addr = (params or {}).get("coll") or ""
    debt_addr = (params or {}).get("loan") or ""
    return {
        "protocol_id": ID,
        "protocol": LABEL,
        "venue": "blue",
        "coll_addr": coll_addr,
        "debt_addr": debt_addr,
        "coll_sym": lb.token_sym(coll_addr) if coll_addr else "?",
        "debt_sym": lb.token_sym(debt_addr) if debt_addr else "?",
        "user": user,
        "searcher": searcher,
        "debt_to_cover": repaid,
        "coll_seized": seized,
        "spoke": MORPHO.lower(),
        "tx": (lg.get("transactionHash") or "").lower(),
        "block": int(lg.get("blockNumber") or "0x0", 16),
        "log_index": int(lg.get("logIndex") or "0x0", 16),
        "topic0": t0,
        "market_id": mid,
    }


def scan_competitor_logs(from_block: int, to_block: int, extra_addrs=None) -> dict:
    logs, errors = lb.get_logs_chunked(
        from_block, to_block, MORPHO, [u.TOPIC["morpho_liq"]])
    events = []
    for lg in logs:
        p = _parse_liq(lg)
        if p:
            events.append(p)
    return {
        "events": events,
        "n_logs": len(logs),
        "errors": errors[:8],
        "from_block": from_block,
        "to_block": to_block,
    }
