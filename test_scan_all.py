import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

from sol_lending import scan_all_obligations, registered

print("Registered adapters:", [(a["id"], a["enabled"]) for a in registered()])
t0 = time.time()
r = scan_all_obligations(max_accounts=20)
elapsed = time.time() - t0
print(f"\nscan_all_obligations took {elapsed:.1f}s")
print(f"  opportunities: {len(r['opportunities'])}")
print(f"  watch: {len(r['watch'])}")
print(f"  probed: {r['probed']}")
print(f"  hydrated: {r['hydrated']}")
print(f"  errors: {r['errors'][:3]}")
print(f"\nAdapters:")
for a in r['adapters']:
    print(f"  {a['id']}: enabled={a['enabled']} probed={a['probed']} hydrated={a['hydrated']} opps={a['opps']} errors={a['errors'][:2]}")

print(f"\nSample opportunities (first 10):")
for o in r['opportunities'][:10]:
    pid = o.get("protocol_id", "?")
    hf = o.get("hf", "?")
    user = (o.get("user") or o.get("obligation") or "?")[:16]
    print(f"  {pid:12s} HF={hf} user={user}... coll={o.get('coll_usd', o.get('assets_raw','?'))} debt={o.get('debt_usd', o.get('liabs_raw','?'))}")

print(f"\nSample watch (first 10):")
for w in r['watch'][:10]:
    pid = w.get("protocol_id", "?")
    hf = w.get("hf", "?")
    user = (w.get("user") or w.get("obligation") or "?")[:16]
    print(f"  {pid:12s} HF={hf} user={user}...")
