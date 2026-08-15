# TONI Solana programs (`liq` + `arb`)

Anchor workspace scaffolding for the SOL side of the TONI dashboard.
**Not deployed to mainnet.** Placeholder program IDs in `Anchor.toml` are
compile-time stubs only.

| Program | Path | Role |
|---------|------|------|
| `liq` | `programs/liq` | Solend liquidation plan executor (CPI stub) |
| `arb` | `programs/arb` | Jupiter multi-hop round-trip arb executor (CPI stub) |

Primary lending target for the dashboard: **Solend (Save)** — labeled honestly
in the UI as “Liquidatable Opportunities (Solend)”.

## Practical MEV stack (dashboard)

| Layer | What runs live | Blocked without |
|-------|----------------|-----------------|
| Priority fees | `getRecentPrioritizationFees` → mempool twin + histogram | — |
| Solend watch | Reserves util / APY (public API) | — |
| Obligation HF | `getProgramAccounts` + `/v1/obligation?ids=` hydrate | GPA-capable `SOLANA_RPC` |
| Competitors | Recent Solend sigs + log decode for `Liquidate*` | — |
| Arb | Jupiter lite multi-pair size grid, priority-fee net, near-miss | — |
| Broadcast | Dry-run plans + Jito tip **metadata** only | Deployed programs + `SOL_KEYPAIR` |

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
export SOL_LIQ_PROGRAM=<liq program pubkey>
export SOL_ARB_PROGRAM=<arb program pubkey>
export SOL_KEYPAIR=/home/kali/Downloads/bugbounty/x/aave-v4-dashboard/solana/keys/bot.json
export SOL_FUNDER=4BHGQ9CXhajxDq5b3jvKfimsXEsFHoHUi2qg21qYVnGy
export SOL_SPONSOR=...   # auto-generated pubkey — see solana/keys/pubkeys.json
export SOL_BOT=...
export SOLANA_RPC=https://api.mainnet-beta.solana.com   # GPA-capable preferred
export SOL_JITO_TIP_SOL=0.00001   # plan metadata only
export MIN_SOL_ARB_USD=0.05
export SOL_SIM_ONLY=1
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

Broadcast remains **blocked** until programs are deployed and `SOL_KEYPAIR` exists.
Sim-only history records Jupiter / liq dry-run plans (with optional Jito tip field).

## Status honesty

- Programs are **scaffolds**: `execute_*` emits events / bumps counters; Solend +
  Jupiter CPIs are TODO.
- Do not report mainnet deploy unless `solana program show <id>` succeeds with
  your keypair.
- Never spam Jito bundles without an auth key — tip is plan metadata only.
