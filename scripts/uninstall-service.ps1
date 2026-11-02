# Removes the Windows task created by START.bat. Needs administrator rights. Data in data\ is kept.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell -Verb RunAs -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"") -Wait
    exit
}
Stop-ScheduledTask -TaskName "PlantDataCollector" -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*plantcollector.cli*run*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Unregister-ScheduledTask -TaskName "PlantDataCollector" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[collector] Windows task removed. Data in data\ is kept." -ForegroundColor Cyan
Start-Sleep -Seconds 2
