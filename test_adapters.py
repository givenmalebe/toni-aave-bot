import sol_lending as slend
import time

t0 = time.time()
result = slend.scan_all_obligations(max_accounts=40)
elapsed = time.time() - t0

print("Time: %.1fs" % elapsed)
print("Opportunities: %d" % len(result.get("opportunities", [])))
print("Watch: %d" % len(result.get("watch", [])))
print("Probed: %s" % result.get("probed", 0))
print("Hydrated: %s" % result.get("hydrated", 0))
print("Errors: %s" % result.get("errors", []))

for a in result.get("adapters", []):
    print("  %s: opps=%d probed=%d hydrated=%d errors=%s" % (
        a["id"], a["opps"], a["probed"], a["hydrated"], a["errors"]))

for o in result.get("opportunities", [])[:5]:
    print("  opp: proto=%s hf=%s coll=%s debt=%s coll_usd=%s debt_usd=%s" % (
        o.get("protocol_id"), o.get("hf"),
        o.get("coll_usd"), o.get("debt_usd"),
        o.get("deposited_usd"), o.get("borrowed_usd")))

for w in result.get("watch", [])[:5]:
    print("  watch: proto=%s hf=%s coll_usd=%s debt_usd=%s" % (
        w.get("protocol_id"), w.get("hf"),
        w.get("coll_usd"), w.get("debt_usd")))
