#!/usr/bin/env bash
# Deploy TONI Solana liq + arb programs. Refuses without a funded keypair.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

KEYPAIR="${SOLANA_KEYPAIR:-${SOLANA_WALLET:-$HOME/.config/solana/id.json}}"
CLUSTER="${SOLANA_CLUSTER:-mainnet}"

if ! command -v anchor >/dev/null 2>&1; then
  echo "ERROR: anchor CLI not found. See README.md toolchain section." >&2
  exit 1
fi
if ! command -v solana >/dev/null 2>&1; then
  echo "ERROR: solana CLI not found. See README.md toolchain section." >&2
  exit 1
fi
if [[ ! -f "$KEYPAIR" ]]; then
  echo "ERROR: keypair missing: $KEYPAIR" >&2
  echo "Set SOL_KEYPAIR / SOLANA_KEYPAIR to a funded JSON keypair. Not deploying." >&2
  exit 1
fi

solana config set --keypair "$KEYPAIR" >/dev/null
solana config set --url "$CLUSTER" >/dev/null 2>&1 || \
  solana config set --url "https://api.mainnet-beta.solana.com" >/dev/null

BAL="$(solana balance 2>/dev/null | awk '{print $1}')"
if [[ -z "$BAL" || "$BAL" == "0" || "$BAL" == "0.0" ]]; then
  echo "ERROR: keypair appears unfunded (balance=$BAL). Refusing mainnet deploy." >&2
  exit 1
fi

echo "[deploy] building…"
anchor build

echo "[deploy] deploying to $CLUSTER with $KEYPAIR (balance ${BAL} SOL)…"
anchor deploy --provider.cluster "$CLUSTER"

echo "[deploy] done. Export program IDs:"
echo "  export SOL_LIQ_PROGRAM=\$(solana address -k target/deploy/liq-keypair.json 2>/dev/null || true)"
echo "  export SOL_ARB_PROGRAM=\$(solana address -k target/deploy/arb-keypair.json 2>/dev/null || true)"
echo "Wire into dashboard env / contracts.json — do not claim live until verified."
