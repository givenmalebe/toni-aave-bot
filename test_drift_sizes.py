"""Find actual Drift User account discriminator and size."""
import os, json, urllib.request, base58

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, ".env")) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

rpc = os.environ.get("SOLANA_RPC", "")

def post(body, timeout=20):
    req = urllib.request.Request(rpc, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())

DRIFT = "dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH"

# Get sizes distribution
d = post({"jsonrpc":"2.0","id":1,"method":"getProgramAccountsV2",
    "params":[DRIFT, {"encoding":"base64","limit":50000,"withContext":True}]})
accts = d.get("result",{}).get("accounts",[])
print(f"Total Drift accounts: {len(accts)}")

# Count by size
from collections import Counter
size_counts = Counter()
disc_map = {}  # size -> set of discriminators
for acc in accts:
    data_b64 = (acc.get("account",{}).get("data",[""]))[0]
    if not data_b64:
        continue
    import base64
    raw = base64.b64decode(data_b64)
    sz = len(raw)
    size_counts[sz] += 1
    if sz not in disc_map:
        disc_map[sz] = set()
    disc_map[sz].add(raw[:8].hex())

print("\nSize distribution (top 20):")
for sz, count in size_counts.most_common(20):
    discs = disc_map.get(sz, set())
    disc_str = ", ".join(sorted(discs)[:3])
    print(f"  {sz:5d} bytes: {count:5d} accounts | disc: {disc_str}")

# Known Drift discriminators
print("\n--- Known Drift User discriminator check ---")
KNOWN_DISC = b"\xd0\xc0\xda\xa4\x17\x15\x14\x0b"
print(f"Expected User disc: {KNOWN_DISC.hex()}")

for sz, discs in disc_map.items():
    if KNOWN_DISC.hex() in discs:
        print(f"  Found at size: {sz}")

# Check what size the Drift User actually is
print("\n--- Looking for Drift User accounts (disc d0c0daa41715140b) ---")
for sz, discs in sorted(disc_map.items()):
    if len(discs) > 0:
        # Check first account of each size for discriminator match
        for acc in accts:
            data_b64 = (acc.get("account",{}).get("data",[""]))[0]
            raw = base64.b64decode(data_b64)
            if len(raw) == sz and raw[:8] == KNOWN_DISC:
                print(f"  FOUND: size={sz}, disc={raw[:8].hex()}")
                print(f"  First 64 bytes: {raw[:64].hex()}")
                print(f"  Account pubkey: {acc.get('pubkey','')}")
                break
