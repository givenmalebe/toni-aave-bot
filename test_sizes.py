import os, json, urllib.request, base64, collections

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, ".env")) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

rpc = os.environ.get("SOLANA_RPC", "")

def post(url, body, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())

def fetch_all_v2(pid, filters=None, limit=10000, max_pages=20):
    all_accts = []
    cursor = None
    for page in range(max_pages):
        opts = {"encoding": "base64", "limit": limit}
        if filters:
            opts["filters"] = filters
        if cursor:
            opts["cursor"] = cursor
        d = post(rpc, {"jsonrpc":"2.0","id":page+1,"method":"getProgramAccountsV2",
            "params":[pid, opts]})
        r = d.get("result",{})
        accts = r.get("accounts",[])
        all_accts.extend(accts)
        cursor = r.get("cursor")
        if not cursor or not accts:
            break
    return all_accts

# 1. Kamino - discover obligation sizes
print("=== Kamino obligation account sizes ===")
print("Fetching all Kamino accounts (no size filter)...")
accts = fetch_all_v2("KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD")
print(f"Total accounts: {len(accts)}")
size_counter = collections.Counter()
for acc in accts:
    data_b64 = (acc.get("account",{}).get("data",[""]))[0]
    if data_b64:
        raw_len = len(base64.b64decode(data_b64))
        size_counter[raw_len] += 1
print("Size distribution:")
for size, count in size_counter.most_common(20):
    print(f"  {size} bytes: {count} accounts")

# 2. MarginFi - discover account sizes (no filter)
print("\n=== MarginFi account sizes (no filter) ===")
print("Fetching all MarginFi accounts...")
accts = fetch_all_v2("MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA")
print(f"Total accounts: {len(accts)}")
size_counter = collections.Counter()
for acc in accts:
    data_b64 = (acc.get("account",{}).get("data",[""]))[0]
    if data_b64:
        raw_len = len(base64.b64decode(data_b64))
        size_counter[raw_len] += 1
print("Size distribution:")
for size, count in size_counter.most_common(20):
    print(f"  {size} bytes: {count} accounts")
