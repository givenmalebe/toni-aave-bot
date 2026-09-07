import os, json, urllib.request, base64

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

# 1. MarginFi: get 3 accounts and inspect first bytes
print("=== MarginFi discriminator check ===")
d = post({"jsonrpc":"2.0","id":1,"method":"getProgramAccountsV2",
    "params":["MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA",
              {"encoding":"base64","limit":3,"filters":[{"dataSize":2312}]}]})
accts = d.get("result",{}).get("accounts",[])
for acc in accts[:3]:
    data_b64 = (acc.get("account",{}).get("data",[""]))[0]
    raw = base64.b64decode(data_b64)
    disc = raw[:8]
    print(f"  {acc['pubkey'][:24]}... disc={disc.hex()} ({disc})")

# 2. Kamino: test filters one by one
print("\n=== Kamino filter diagnostics ===")
# No filter
d = post({"jsonrpc":"2.0","id":2,"method":"getProgramAccountsV2",
    "params":["KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD",
              {"encoding":"base64","limit":3}]})
accts = d.get("result",{}).get("accounts",[])
print(f"  No filter: {len(accts)} accounts")
if accts:
    data_b64 = (accts[0].get("account",{}).get("data",[""]))[0]
    raw = base64.b64decode(data_b64)
    print(f"  First account size: {len(raw)} bytes")
    print(f"  First 20 bytes: {raw[:20].hex()}")
    # Check offset 11 (lending market field)
    print(f"  Bytes at offset 11: {raw[11:43].hex()}")

# dataSize=1032 only
d = post({"jsonrpc":"2.0","id":3,"method":"getProgramAccountsV2",
    "params":["KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD",
              {"encoding":"base64","limit":3,"filters":[{"dataSize":1032}]}]})
accts = d.get("result",{}).get("accounts",[])
print(f"  dataSize=1032: {len(accts)} accounts")

# dataSize=1032 + memcmp offset=11
d = post({"jsonrpc":"2.0","id":4,"method":"getProgramAccountsV2",
    "params":["KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD",
              {"encoding":"base64","limit":3,"filters":[
                  {"dataSize":1032},
                  {"memcmp":{"offset":11,"bytes":"7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF"}}
              ]}]})
accts = d.get("result",{}).get("accounts",[])
print(f"  dataSize=1032 + memcmp offset=11: {len(accts)} accounts")

# Get the base58 encoding of the market address and check what the actual bytes at offset 11 look like
import base58
market_b58 = "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF"
market_bytes = base58.b58decode(market_b58)
print(f"  Market address bytes (base58 decoded): {market_bytes.hex()}")
print(f"  Market address bytes len: {len(market_bytes)}")
