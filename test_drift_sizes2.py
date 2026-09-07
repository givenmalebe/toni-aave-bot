"""Debug Drift GPA: try various filter combinations."""
import os, json, urllib.request, base64, base58, time

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
disc = b"\xd0\xc0\xda\xa4\x17\x15\x14\x0b"

# Test various data sizes for Drift user accounts
tests = [
    ("dataSize=4376 only", [{"dataSize": 4376}]),
    ("dataSize=2744", [{"dataSize": 2744}]),
    ("dataSize=2432", [{"dataSize": 2432}]),
    ("dataSize=256", [{"dataSize": 256}]),
    ("dataSize=512", [{"dataSize": 512}]),
    ("dataSize=1024", [{"dataSize": 1024}]),
    ("dataSize=1544", [{"dataSize": 1544}]),
    ("dataSize=2000", [{"dataSize": 2000}]),
    ("dataSize=4376+disc_b58", [{"dataSize": 4376}, {"memcmp": {"offset": 0, "bytes": base58.b58encode(disc).decode()}}]),
    ("dataSize=2744+disc_b58", [{"dataSize": 2744}, {"memcmp": {"offset": 0, "bytes": base58.b58encode(disc).decode()}}]),
]

for label, filters in tests:
    t0 = time.time()
    try:
        d = post({"jsonrpc":"2.0","id":1,"method":"getProgramAccountsV2",
            "params":[DRIFT, {"encoding":"base64","limit":3,"filters":filters}]})
        accts = d.get("result",{}).get("accounts",[])
        elapsed = time.time() - t0
        sz_str = ""
        if accts:
            raw = base64.b64decode((accts[0].get("account",{}).get("data",[""]))[0])
            sz_str = f" size={len(raw)} disc={raw[:8].hex()}"
        print(f"  {label:35s} => {len(accts):4d} accounts  ({elapsed:.1f}s){sz_str}")
    except Exception as e:
        print(f"  {label:35s} => ERROR: {str(e)[:80]}")

# Also try standard getSignaturesForAddress to find a recent Drift tx
# with a user account that we can decode
print("\n--- Finding Drift User accounts via recent signatures ---")
try:
    d = post({"jsonrpc":"2.0","id":2,"method":"getSignaturesForAddress",
        "params":[DRIFT, {"limit": 10, "commitment": "confirmed"}]})
    sigs = d.get("result", [])
    print(f"  Got {len(sigs)} signatures")
    for sig_row in sigs[:5]:
        sig = sig_row.get("signature","")
        d = post({"jsonrpc":"2.0","id":3,"method":"getTransaction",
            "params":[sig, {"encoding":"json","maxSupportedTransactionVersion":0}]})
        tx = d.get("result",{})
        if not tx:
            continue
        msg = (tx.get("transaction") or {}).get("message") or {}
        accs = msg.get("accountKeys") or []
        ixs = msg.get("instructions") or []
        # Find accounts that might be Drift user accounts
        for ak in accs[:3]:
            if isinstance(ak, str):
                # Fetch and check size
                d2 = post({"jsonrpc":"2.0","id":4,"method":"getAccountInfo",
                    "params":[ak, {"encoding":"base64"}]})
                val = d2.get("result",{}).get("value")
                if val and val.get("data"):
                    raw = base64.b64decode(val["data"][0])
                    if raw[:8] == disc:
                        print(f"  FOUND USER: {ak} size={len(raw)} disc={raw[:8].hex()}")
                        print(f"    First 128: {raw[:128].hex()}")
                        break
except Exception as e:
    print(f"  Error: {e}")
