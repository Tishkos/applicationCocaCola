# Stops a running diagnostic session started by run_diag.ps1
. "$PSScriptRoot\config.ps1"
$f = "$OUT_ROOT\current_run.json"
if (-not (Test-Path $f)) { Write-Host "No running session found."; exit }
$s = Get-Content $f | ConvertFrom-Json
foreach ($pid in @($s.capture, $s.latency)) { if ($pid) { Stop-Process -Id $pid -ErrorAction SilentlyContinue } }
Get-Process tshark, python -ErrorAction SilentlyContinue | Stop-Process -ErrorAction SilentlyContinue
Get-Process ping -ErrorAction SilentlyContinue | Stop-Process -ErrorAction SilentlyContinue
Remove-Item $f -ErrorAction SilentlyContinue
"stopped $(Get-Date -Format o) by stop_diag" | Out-File "$($s.out)\run.log" -Append
Write-Host "Stopped. Zip and send: $($s.out)"
