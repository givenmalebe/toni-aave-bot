"""Test adapters using the actual sol_gpa + adapter code."""
import os, sys, time

# Load .env before anything else
HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, ".env")) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

from sol_lending import kamino, marginfi, drift

MAX_ACC = 500

t0 = time.time()
print("--- Kamino ---")
k = kamino.scan_obligations(max_accounts=MAX_ACC)
print(f"  opps={len(k['opportunities'])} watch={len(k['watch'])} "
      f"probed={k['probed']} hydrated={k['hydrated']} "
      f"errors={k['errors']}")

print("\n--- MarginFi ---")
m = marginfi.scan_obligations(max_accounts=MAX_ACC)
print(f"  opps={len(m['opportunities'])} watch={len(m['watch'])} "
      f"probed={m['probed']} hydrated={m['hydrated']} "
      f"errors={m['errors']}")

print("\n--- Drift ---")
d = drift.scan_obligations(max_accounts=MAX_ACC)
print(f"  opps={len(d['opportunities'])} watch={len(d['watch'])} "
      f"probed={d['probed']} hydrated={d['hydrated']} "
      f"errors={d['errors']}")

print(f"\nTotal: {time.time()-t0:.1f}s")
