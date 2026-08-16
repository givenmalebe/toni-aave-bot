# TONI Solana programs (`liq` + `arb`)

Anchor workspace scaffolding. **Placeholder program IDs are stubs — never
send to `Arb1111…` / `Liq1111…`.** The dashboard live path is Python:

| Path | What actually lands |
|------|---------------------|
| **Arb** | Fresh Jupiter `/quote` → `/swap` serialized tx (mainnet Jupiter v6) + Jito tip transfer, `sendBundle` |
| **Liq** | Solend `LiquidateObligationAndRedeemReserveCollateral` (ix 17) + refresh + Jito tip, same bundle API |

`execute_roundtrip` / `execute_plan` in these programs still `msg!(… stub)`.
CPI is not required for LIVE.

| Program | Path | Role |
|---------|------|------|
| `liq` | `programs/liq` | Solend liquidation executor (CPI stub; unused when Python path is live) |
| `arb` | `programs/arb` | Jupiter CPI stub; unused — Python talks to Jupiter directly |

Primary lending target: **Solend (Save)**.

## Practical MEV stack (dashboard)

| Layer | What runs live | Blocked without |
|-------|----------------|-----------------|
| Priority fees | `getRecentPrioritizationFees` → mempool twin + histogram | — |
| Solend watch | Reserves util / APY (public API) | — |
| Obligation HF | `getProgramAccounts` + on-chain hydrate | GPA-capable `SOLANA_RPC` |
| Competitors | Recent Solend sigs + log decode for `Liquidate*` | — |
| Arb | Jupiter lite multi-pair size grid, net after CU + Jito + slip | — |
| Broadcast | **sim_only default.** LIVE = Jupiter/Solend txs + Jito bundle | arm + funded wallets + bot keypair + `solders` |

Borrower HF is **not** a free public Solend list. The sweep documents that and
still ships a real GPA probe path (often rate-limited on public RPCs).

## Toolchain

```bash
# Install if missing (docs only — run on your machine):
sh -c "$(curl -sSfL https://release.anza.xyz/stable/install)"
cargo install --git https://github.com/coral-xyz/anchor avm --locked
avm install 0.30.1 && avm use 0.30.1

solana --version
anchor --version
```

This workstation may not have `solana` / `anchor` / `cargo` on PATH; sources
here remain structured for `anchor build` once the toolchain is present.

## Build

```bash
cd /home/kali/Downloads/bugbounty/x/aave-v4-dashboard/solana
anchor build
```

Artifacts:
- `target/deploy/liq.so` / `arb.so`
- `target/idl/liq.json` / `arb.json`

IDL placeholders (pre-build) live under `target/idl/*.placeholder.json`.

## Deploy (mainnet — funded keypair required)

```bash
export SOLANA_KEYPAIR=~/.config/solana/id.json   # funded JSON keypair
solana config set --url https://api.mainnet-beta.solana.com
solana config set --keypair "$SOLANA_KEYPAIR"
solana balance   # must be funded

# Generate real program keypairs (once), update declare_id! + Anchor.toml
solana-keygen new -o target/deploy/liq-keypair.json
solana-keygen new -o target/deploy/arb-keypair.json
# Update programs/*/src/lib.rs declare_id! to match `solana-keygen pubkey …`
anchor build
anchor deploy --provider.cluster mainnet
```

Or:

```bash
./scripts/deploy.sh   # refuses if SOLANA_KEYPAIR unset / unfunded
```

Post-deploy (liq):

```bash
# after initialize, point config at Solend program
# Solend mainnet: So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo
```

## Dashboard env wiring

```bash
# SOL_LIQ_PROGRAM / SOL_ARB_PROGRAM: optional; stub IDs are ignored
export SOL_KEYPAIR=/path/to/toni-aave-bot/solana/keys/bot.json
export SOL_FUNDER=4BHGQ9CXhajxDq5b3jvKfimsXEsFHoHUi2qg21qYVnGy
export SOL_SPONSOR=...   # auto-generated pubkey — see solana/keys/pubkeys.json
export SOL_BOT=...
export SOLANA_RPC=https://api.mainnet-beta.solana.com   # GPA-capable preferred
export SOL_JITO_TIP_SOL=0.00001        # floor; LIVE tip is a share of expected net
export SOL_JITO_TIP_SHARE=0.15
export MIN_SOL_ARB_USD=0.05
export SOL_SIM_ONLY=1                  # keep default; LIVE only after arm
# JITO_UUID=                           # optional rate-limit auth
```

Runtime keypairs (`sponsor.json`, `bot.json`) are created on dashboard start under
`solana/keys/` (chmod 600). **Never commit or print the private JSON.** Fund from
the funder pubkey: **0.08 SOL → sponsor** (Jito + prio) and **0.25 SOL → bot**
(CU fees + small inventory). Arb plans use bot as fee payer and sponsor as tip payer.

Also accepted via `contracts.json` keys `SOL_LIQ_PROGRAM` / `SOL_ARB_PROGRAM`.

Control API (SOL arm/sim):

```bash
curl -s -X POST http://127.0.0.1:8081/api/control \
  -H 'Content-Type: application/json' \
  -d '{"chain":"sol","sim_only":true}'
curl -s -X POST http://127.0.0.1:8081/api/control \
  -H 'Content-Type: application/json' \
  -d '{"chain":"sol","armed":true,"sim_only":false,"arm_minutes":15}'
```

Broadcast remains **simulated** while `SOL_SIM_ONLY=1` (default). Arm LIVE from
the dashboard (`sim_only: false`, `armed: true`) **and** fund wallets: sponsor
≥ 0.01 SOL, bot ≥ 0.05 SOL. Then +EV arb lands as a Jupiter+Jito bundle; +EV
liq lands when Solend accounts + repay inventory are present.

## Status honesty

- On-chain programs are **scaffolds**. LIVE does **not** call them.
- Arb LIVE: Jupiter `/swap` (fresh quote used immediately) + Jito `sendBundle`.
- Liq LIVE: Python Solend ix 17. Remaining gaps: flash-loan repay (bot must
  already hold the debt token), optional liquidator whitelist, ALT if the tx
  exceeds packet size.
- Dynamic Jito tip is a share of expected net (`SOL_JITO_TIP_SHARE`, default
  15%) with a floor from `SOL_JITO_TIP_SOL`, capped so net stays > 0 and ≥ floor.
- Never spam Jito without intent: sim_only is default; LIVE requires arm.
