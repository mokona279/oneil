<#
  Incremental data update to TODAY (auto-detected). Standalone so the slow KRX
  pull can run separately from screening (e.g. after market close).

  Auto date: today = (Get-Date). oneil_fetch does an incremental append when data
  already exists, so only the missing recent sessions are fetched.

  Usage:
    powershell -ExecutionPolicy Bypass -File scripts\fetch.ps1
    scripts\fetch.ps1 -Symbols 005930,000660     # only these (fast test)
    scripts\fetch.ps1 -DryRun                     # plan only, no price fetch
    scripts\fetch.ps1 -Start 2015-10-01           # warmup start for new listings
#>
param(
  [string]$EnvFile = "C:\Users\mh.han\repos\krx\.env",   # KRX credentials (.env)
  [string]$Start   = "2015-10-01",                        # warmup start (harmless for incremental)
  [string]$Symbols = "",                                  # CSV of codes; empty = full universe
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$py   = "C:\Users\mh.han\repos\daytrading\.venv\Scripts\python.exe"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
$today = (Get-Date).ToString("yyyy-MM-dd")     # <-- auto today

if (-not (Test-Path $EnvFile)) { throw "env file not found: $EnvFile" }

$args = @("-m","oneil_fetch","--start",$Start,"--end",$today,"--out","data","--env-file",$EnvFile)
if ($Symbols -ne "") { $args += @("--symbols",$Symbols) }
if ($DryRun)         { $args += "--dry-run" }

Write-Host "fetch -> end=$today  (incremental; full universe may take minutes)"
Write-Host ("cmd: {0} {1}" -f $py, ($args -join " "))
& $py @args
if ($LASTEXITCODE -ne 0) { throw "oneil_fetch failed (exit $LASTEXITCODE)" }
Write-Host "done. data updated through last trading day <= $today"
