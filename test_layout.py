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

market_bytes = base58.b58decode("7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF")

# Fetch a 3344-byte Kamino obligation account
d = post({"jsonrpc":"2.0","id":1,"method":"getProgramAccountsV2",
    "params":["KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD",
              {"encoding":"base64","limit":3,"filters":[{"dataSize":3344}]}]})
accts = d.get("result",{}).get("accounts",[])

for i, acc in enumerate(accts[:3]):
    raw = base64.b64decode((acc.get("account",{}).get("data",[""]))[0])
    print(f"\n=== Obligation #{i} ({acc['pubkey'][:20]}...) ===")
    print(f"  Size: {len(raw)} bytes")
    print(f"  Hex dump of first 256 bytes:")
    for off in range(0, 256, 32):
        chunk = raw[off:off+32]
        print(f"    [{off:3d}] {chunk.hex()}")
    
    # Check specific fields
    disc = raw[:8]
    print(f"\n  Discriminator: {disc.hex()}")
    
    # Market at offset 32
    mkt = raw[32:64]
    print(f"  Market (32-64): {mkt.hex()}")
    print(f"  Market matches main: {mkt == market_bytes}")
    
    # Owner at offset 64?
    owner = raw[64:96]
    print(f"  Potential owner (64-96): {owner.hex()}")
    
    # Try to find u128 wad values that look like USD amounts
    import struct
    for off in range(0, min(200, len(raw)-16), 8):
        val = struct.unpack_from('<Q', raw, off)[0]
        if val > 1000000000000000 and val < 100000000000000000000000:
            # Looks like a wad (1e18 scale)
            usd = val / 1e18
            print(f"  Potential wad at offset {off}: {val} = ${usd:.2f}")

# Also check 4664 and 8624 byte accounts
for target_size in [4664, 8624]:
    d = post({"jsonrpc":"2.0","id":2,"method":"getProgramAccountsV2",
        "params":["KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD",
                  {"encoding":"base64","limit":1,"filters":[{"dataSize":target_size}]}]})
    accts = d.get("result",{}).get("accounts",[])
    if accts:
        raw = base64.b64decode((accts[0].get("account",{}).get("data",[""]))[0])
        idx = raw.find(market_bytes)
        print(f"\n=== {target_size}-byte account: market at offset {idx} ===")
        print(f"  First 64 bytes: {raw[:64].hex()}")
