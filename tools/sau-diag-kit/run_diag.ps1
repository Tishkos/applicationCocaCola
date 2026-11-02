# SAU / InteliNeo diagnostic recorder. Read-only. Run as Administrator.
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\config.ps1"

$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$OUT = Join-Path $OUT_ROOT $stamp
New-Item -ItemType Directory -Force -Path $OUT | Out-Null
"started $(Get-Date -Format o)" | Out-File "$OUT\run.log"
Write-Host "Output folder: $OUT"

# ---- 1. packet capture (tshark) ---------------------------------------------------
$captureProc = $null
if (Test-Path $TSHARK) {
    Write-Host "`nNetwork interfaces:"
    & $TSHARK -D
    $iface = Read-Host "Enter the NUMBER of the wired LAN interface to capture on"
    $filter = "host $SAU_IP and tcp port 502"
    $captureProc = Start-Process -FilePath $TSHARK -PassThru -NoNewWindow `
        -ArgumentList @("-i", $iface, "-f", "`"$filter`"", "-b", "duration:$CAPTURE_ROTATE_S", "-w", "`"$OUT\modbus.pcapng`"")
    "capture pid $($captureProc.Id) iface $iface filter '$filter'" | Out-File "$OUT\run.log" -Append
    Write-Host "Capture running (pid $($captureProc.Id))."
} else {
    Write-Host "tshark not found at $TSHARK - packet capture SKIPPED. Install Wireshark (with Npcap)." -ForegroundColor Yellow
    "tshark not found - capture skipped" | Out-File "$OUT\run.log" -Append
}

# ---- 2. ping log -----------------------------------------------------------------------
$pingJob = Start-Job -ScriptBlock {
    param($ip, $out)
    ping.exe -t $ip | ForEach-Object { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff') $_" } |
        Out-File -FilePath "$out\ping_sau.txt" -Append
} -ArgumentList $SAU_IP, $OUT
$pingJob2 = Start-Job -ScriptBlock {
    param($ip, $out)
    ping.exe -t $ip | ForEach-Object { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff') $_" } |
        Out-File -FilePath "$out\ping_controller.txt" -Append
} -ArgumentList $CONTROLLER_IP, $OUT

# ---- 3. modbus latency test (python) --------------------------------------------------
$pyProc = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pyProc = Start-Process -FilePath "python" -PassThru -NoNewWindow `
        -ArgumentList @("`"$PSScriptRoot\sau_latency.py`"", $SAU_IP, $MODBUS_UNIT, $MODBUS_ADDR, "`"$OUT\modbus_latency.csv`"")
    "latency test pid $($pyProc.Id)" | Out-File "$OUT\run.log" -Append
} else {
    Write-Host "python not found - latency test SKIPPED." -ForegroundColor Yellow
}

# ---- 4. SAU diagnostic page every 60 s -----------------------------------------------------
$pageJob = Start-Job -ScriptBlock {
    param($url, $cookie, $out)
    while ($true) {
        $t = Get-Date -Format "yyyyMMdd_HHmmss"
        try {
            $h = @{}
            if ($cookie) { $h["Cookie"] = $cookie }
            $r = Invoke-WebRequest -Uri $url -Headers $h -UseBasicParsing -TimeoutSec 20
            $r.Content | Out-File "$out\sau_page_$t.html"
            # crude extraction of any 'Send queue'-like numbers for a quick timeline
            $nums = [regex]::Matches($r.Content, '127\.0\.0\.1:502[^0-9]{0,200}?(\d{2,6})') | ForEach-Object { $_.Groups[1].Value }
            "$t loopback_sendq=$($nums -join ',')" | Out-File "$out\sau_queue_timeline.txt" -Append
        } catch {
            "$t fetch_error $($_.Exception.Message)" | Out-File "$out\sau_queue_timeline.txt" -Append
        }
        Start-Sleep -Seconds 60
    }
} -ArgumentList $SAU_DIAG_URL, $SAU_COOKIE, $OUT

# ---- keep running until Ctrl+C ----------------------------------------------------------------
@{ capture=$captureProc.Id; latency=$pyProc.Id; jobs=@($pingJob.Id,$pingJob2.Id,$pageJob.Id); out=$OUT } |
    ConvertTo-Json | Out-File "$OUT_ROOT\current_run.json"

Write-Host "`nRecording. Leave this window open. Press Ctrl+C (or run stop_diag.ps1) to stop." -ForegroundColor Green
try {
    while ($true) {
        Start-Sleep -Seconds 300
        $n = (Get-ChildItem "$OUT\modbus*.pcapng" -ErrorAction SilentlyContinue).Count
        Write-Host "$(Get-Date -Format HH:mm)  capture files: $n   ping lines: $((Get-Content "$OUT\ping_sau.txt" -ErrorAction SilentlyContinue).Count)"
    }
} finally {
    Write-Host "Stopping..."
    if ($captureProc) { Stop-Process -Id $captureProc.Id -ErrorAction SilentlyContinue }
    if ($pyProc)      { Stop-Process -Id $pyProc.Id -ErrorAction SilentlyContinue }
    Get-Job | Stop-Job; Get-Job | Remove-Job
    "stopped $(Get-Date -Format o)" | Out-File "$OUT\run.log" -Append
    Write-Host "Done. Zip and send:  $OUT"
}
