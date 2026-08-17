# Deploy GenericFlashLiquidator to Ethereum mainnet (PowerShell).
# Constructor: none. Then paste LIQ_GENERIC_CONTRACT and restart the dashboard.
#
#   $env:KEYSTORE_PATH = "C:\path\to\keystore.json"
#   $env:KEYSTORE_PW = "..."
#   $env:BOT_EOA = "0x..."   # optional; only if deployer != bot
#   .\deploy_generic.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Get-Command forge -ErrorAction SilentlyContinue)) {
    Write-Error "forge not found. Install Foundry: irm https://foundry.paradigm.xyz | iex   then foundryup"
}

$Rpc = $env:AAVE_RPC
if (-not $Rpc) { $Rpc = $env:ETH_RPC_URL }
if (-not $Rpc) { $Rpc = "https://ethereum-rpc.publicnode.com" }

$Ks = $env:KEYSTORE_PATH
$Pw = $env:KEYSTORE_PW
if (-not $Ks) { Write-Error "set KEYSTORE_PATH (encrypted json; same name the bot uses)" }
if (-not (Test-Path $Ks)) { Write-Error "KEYSTORE_PATH not a file: $Ks" }
if (-not $Pw) { Write-Error "set KEYSTORE_PW (same name the bot uses)" }

Write-Host "deploying GenericFlashLiquidator (no constructor args) via $Rpc"
$json = forge create GenericFlashLiquidator.sol:GenericFlashLiquidator `
    --rpc-url $Rpc `
    --keystore $Ks `
    --password $Pw `
    --broadcast `
    --json 2>&1 | Out-String

$addr = $null
try {
    $obj = $json | ConvertFrom-Json
    $addr = $obj.deployedTo
    if (-not $addr) { $addr = $obj.deployed_to }
} catch {
    if ($json -match "Deployed to:\s*(0x[0-9a-fA-F]{40})") {
        $addr = $Matches[1]
    }
}

if (-not $addr) {
    Write-Host $json
    Write-Host "copy Deployed-to, then set LIQ_GENERIC_CONTRACT=0x... in repo-root .env and restart dashboard.py"
    exit 0
}

Write-Host "deployed $addr"
Write-Host "KIND check (want 0x8caa2b9a42135cb026f57f48dfc7f1d565f83039807016026fa2fdfe883d27d1):"
cast call $addr "KIND()(bytes32)" --rpc-url $Rpc

$bot = $env:BOT_EOA
if ($bot -and $bot.StartsWith("0x")) {
    Write-Host "setOwner -> $bot"
    cast send $addr "setOwner(address)" $bot --keystore $Ks --password $Pw --rpc-url $Rpc
} else {
    Write-Host "owner = deployer (this KEYSTORE). If that is the dashboard bot, skip setOwner."
    Write-Host "else: cast send $addr `"setOwner(address)`" 0xBOT --keystore `$env:KEYSTORE_PATH --password `$env:KEYSTORE_PW --rpc-url $Rpc"
}

Write-Host ""
Write-Host "paste into repo-root .env then restart dashboard.py:"
Write-Host "  LIQ_GENERIC_CONTRACT=$addr"
Write-Host "  # optional: also LIQ_CONTRACT=$addr   (keeps Aave 4-arg flashLiquidate)"
Write-Host "then: fund bot ETH -> Broadcast Arm LIVE / Keep Live ON / Sim OFF"
Write-Host "full steps: contracts/DEPLOY.md"
