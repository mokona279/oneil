<#
  Daily workflow: fetch data -> screen buy candidates (+ holdings check + sizing).

  No brokerage auto-sync. Keep cash/holdings in state\holdings.csv by hand; update
  that file after fills. This script re-marks holdings to the latest close, sizes
  new candidates off the resulting equity, and checks stop/exit signals for holdings.

  Usage:
    powershell -ExecutionPolicy Bypass -File scripts\daily.ps1
    scripts\daily.ps1 -SkipFetch      # screening only (no data update)
    scripts\daily.ps1 -Backtest       # also run full-universe backtest
    scripts\daily.ps1 -Equity 5e7     # size off equity when no holdings file

  Output: out\daily\<date>\buy_candidates.csv , holdings_report.csv
  (Console tables are printed in Korean by the Python screener.)
  Side effect: appends the session's signals to forward\ (shadow ledger, P8-1).
#>
param(
  [string]$EnvFile    = "C:\Users\mh.han\repos\krx\.env",   # KRX credentials (.env)
  [string]$Holdings   = "state\holdings.csv",               # cash + positions
  [double]$Equity     = 1e8,                                 # sizing capital when no holdings
  [string]$FetchStart = "2015-10-01",                        # warmup start (harmless for incremental)
  [switch]$SkipFetch,                                        # skip data update
  [switch]$Backtest                                          # also run full-universe backtest
)
$ErrorActionPreference = "Stop"
$py   = "C:\Users\mh.han\repos\daytrading\.venv\Scripts\python.exe"
$repo = Split-Path -Parent $PSScriptRoot     # parent of scripts\ = repo root
Set-Location $repo
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
$today  = (Get-Date).ToString("yyyy-MM-dd")
$outDir = "out\daily\$today"

# 1) incremental data update (through today) ----------------------------------
if (-not $SkipFetch) {
  if (Test-Path $EnvFile) {
    Write-Host "[1/3] fetch data -> $today (full universe, may take minutes)"
    & $py -m oneil_fetch --start $FetchStart --end $today --out data --env-file $EnvFile
    if ($LASTEXITCODE -ne 0) { throw "oneil_fetch failed (exit $LASTEXITCODE)" }
  } else {
    Write-Host "[1/3] env file not found ($EnvFile) - skipping fetch"
  }
} else {
  Write-Host "[1/3] -SkipFetch - skipping data update"
}

# 2) screen candidates + holdings check ---------------------------------------
Write-Host "[2/3] screening (holdings check + buy candidates + sizing)"
$screenArgs = @(
  "scripts\screen_today.py",
  "--price-dir","data\prices","--kospi","data\kospi.csv","--kosdaq","data\kosdaq.csv",
  "--meta","data\meta.csv","--rules","config\rules_v3-3.yaml","--costs","config\costs.yaml",
  "--out-dir",$outDir
)
if (Test-Path $Holdings) {
  $screenArgs += @("--holdings",$Holdings)
} else {
  $screenArgs += @("--equity",("{0}" -f $Equity))
  Write-Host ("  ({0} not found -> sizing off --equity {1})" -f $Holdings, $Equity)
}
& $py @screenArgs
if ($LASTEXITCODE -ne 0) { throw "screen_today failed (exit $LASTEXITCODE)" }

# 2b) forward shadow ledger - append-only OOS record (P8-1) --------------------
#     Seals today's signals before outcomes are known. Commit forward\ to git
#     to make the record tamper-evident.
Write-Host "[2b/3] forward shadow ledger -> forward\"
& $py scripts\forward_ledger.py --candidates "$outDir\buy_candidates.csv" `
  --kospi data\kospi.csv --kosdaq data\kosdaq.csv `
  --rules config\rules_v3-3.yaml --costs config\costs.yaml --out forward
if ($LASTEXITCODE -ne 0) { throw "forward_ledger failed (exit $LASTEXITCODE)" }

# 3) (optional) full-universe backtest - model performance / book flow ---------
if ($Backtest) {
  Write-Host "[3/3] full-universe backtest (2017-01-02 -> $today)"
  & $py -m oneil_bt.cli.run_portfolio `
    --price-dir data\prices --kospi data\kospi.csv --kosdaq data\kosdaq.csv `
    --meta data\meta.csv --rules config\rules_v3-3.yaml --costs config\costs.yaml `
    --start 2017-01-02 --end $today --cash 1e8 --out $outDir
} else {
  Write-Host "[3/3] -Backtest not set - skipping full-universe backtest"
}

Write-Host ""
Write-Host "done -> $outDir\buy_candidates.csv"
if (Test-Path "$outDir\holdings_report.csv") { Write-Host "        $outDir\holdings_report.csv" }
