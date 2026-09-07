import os, json, urllib.request, base64, base58

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

# Fetch a Kamino 1032-byte account and find the market offset
d = post({"jsonrpc":"2.0","id":1,"method":"getProgramAccountsV2",
    "params":["KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD",
              {"encoding":"base64","limit":1,"filters":[{"dataSize":1032}]}]})
accts = d.get("result",{}).get("accounts",[])
if not accts:
    print("No 1032-byte accounts!")
    exit()

raw = base64.b64decode((accts[0].get("account",{}).get("data",[""]))[0])
market_bytes = base58.b58decode("7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF")

# Find where the market pubkey appears in the data
idx = raw.find(market_bytes)
print(f"Market pubkey found at offset: {idx}")
print(f"First 8 bytes (disc?): {raw[:8].hex()}")
print(f"First 32 bytes: {raw[:32].hex()}")
print(f"Account total: {len(raw)} bytes")

# Also check 3344-byte account
d = post({"jsonrpc":"2.0","id":2,"method":"getProgramAccountsV2",
    "params":["KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD",
              {"encoding":"base64","limit":1,"filters":[{"dataSize":3344}]}]})
accts2 = d.get("result",{}).get("accounts",[])
if accts2:
    raw2 = base64.b64decode((accts2[0].get("account",{}).get("data",[""]))[0])
    idx2 = raw2.find(market_bytes)
    print(f"\n3344-byte account:")
    print(f"  Market pubkey at offset: {idx2}")
    print(f"  First 8 bytes: {raw2[:8].hex()}")
    print(f"  First 40 bytes: {raw2[:40].hex()}")

# Also fetch a MarginFi 2312-byte account and inspect layout
d = post({"jsonrpc":"2.0","id":3,"method":"getProgramAccountsV2",
    "params":["MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA",
              {"encoding":"base64","limit":1,"filters":[{"dataSize":2312}]}]})
accts3 = d.get("result",{}).get("accounts",[])
if accts3:
    raw3 = base64.b64decode((accts3[0].get("account",{}).get("data",[""]))[0])
    print(f"\nMarginFi 2312-byte account:")
    print(f"  First 8 bytes (disc): {raw3[:8].hex()}")
    print(f"  Bytes 8-40 (group?): {raw3[8:40].hex()}")
    print(f"  Bytes 40-72 (owner?): {raw3[40:72].hex()}")
    # Check the group at offset 8
    group_bytes = base58.b58decode("4qp6Fx6tnZkY5Wropq9wUYgtFxXKwE6viZxFHg3rdAG8")
    print(f"  Expected group: {group_bytes.hex()}")
    print(f"  Actual group at 8-40: {raw3[8:40].hex()}")
    print(f"  Group matches: {raw3[8:40] == group_bytes}")
