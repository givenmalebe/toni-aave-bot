#!/usr/bin/env bash
# Deploy GenericFlashLiquidator to Ethereum mainnet.
# Constructor: none. Requires foundry (forge, cast) + funded keystore.
# Windows: use deploy_generic.ps1 instead (or Git Bash).
#
#   export KEYSTORE_PATH=/path/to/keystore.json
#   export KEYSTORE_PW='...'
#   export BOT_EOA=0x...   # optional; only if deployer != bot
#
# After deploy, paste into repo-root .env and restart dashboard:
#   LIQ_GENERIC_CONTRACT=0x...
#   # optional: LIQ_CONTRACT= same address (keeps Aave 4-arg flashLiquidate)

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
if ! command -v forge >/dev/null 2>&1; then
  echo "forge not found. Install Foundry, then foundryup. See DEPLOY.md" >&2
  exit 1
fi
RPC="${AAVE_RPC:-${ETH_RPC_URL:-https://ethereum-rpc.publicnode.com}}"
KS="${KEYSTORE_PATH:?set KEYSTORE_PATH (same env name the bot uses)}"
PW="${KEYSTORE_PW:?set KEYSTORE_PW (same env name the bot uses)}"
if [[ ! -f "$KS" ]]; then
  echo "KEYSTORE_PATH is not a file: $KS" >&2
  exit 1
fi

cd "$ROOT"
echo "deploying GenericFlashLiquidator (no constructor args) via $RPC"
JSON=$(forge create GenericFlashLiquidator.sol:GenericFlashLiquidator \
  --rpc-url "$RPC" \
  --keystore "$KS" \
  --password "$PW" \
  --broadcast \
  --json || true)

ADDR=""
if command -v python >/dev/null 2>&1; then
  ADDR=$(printf '%s' "$JSON" | python -c "import sys,json,re
raw=sys.stdin.read()
try:
    d=json.loads(raw)
    print(d.get('deployedTo') or d.get('deployed_to') or '')
except Exception:
    m=re.search(r'Deployed to:\\s*(0x[0-9a-fA-F]{40})', raw)
    print(m.group(1) if m else '')" 2>/dev/null || true)
fi
if [[ -z "${ADDR:-}" ]]; then
  ADDR=$(printf '%s' "$JSON" | grep -oE '0x[0-9a-fA-F]{40}' | tail -n 1 || true)
fi

if [[ -z "${ADDR:-}" ]]; then
  echo "$JSON"
  echo "copy the Deployed-to address and set in repo-root .env:"
  echo "  LIQ_GENERIC_CONTRACT=0x..."
  echo "  # optional: also LIQ_CONTRACT= the same address"
  exit 0
fi

echo "deployed $ADDR"
echo "KIND check (want 0x8caa2b9a42135cb026f57f48dfc7f1d565f83039807016026fa2fdfe883d27d1):"
cast call "$ADDR" "KIND()(bytes32)" --rpc-url "$RPC" || true

if [[ -n "${BOT_EOA:-}" ]]; then
  echo "setOwner -> $BOT_EOA"
  cast send "$ADDR" "setOwner(address)" "$BOT_EOA" \
    --keystore "$KS" --password "$PW" --rpc-url "$RPC"
else
  echo "owner = deployer (this KEYSTORE). If that is the dashboard bot, skip setOwner."
  echo "else: cast send $ADDR 'setOwner(address)' 0xBOT --keystore \"\$KEYSTORE_PATH\" --password \"\$KEYSTORE_PW\" --rpc-url $RPC"
fi

echo
echo "paste into repo-root .env then restart dashboard.py:"
echo "  LIQ_GENERIC_CONTRACT=$ADDR"
echo "  # optional: also LIQ_CONTRACT=$ADDR"
echo "then: fund bot ETH -> Broadcast Arm LIVE / Keep Live ON / Sim OFF"
echo "full steps: contracts/DEPLOY.md"
