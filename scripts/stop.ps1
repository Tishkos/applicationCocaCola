# Stops the background collector (the Windows task and any leftover process). Data is kept.
Stop-ScheduledTask -TaskName "PlantDataCollector" -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*plantcollector.cli*run*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "[collector] stopped. Start again with START.bat / START-PLANT.bat" -ForegroundColor Cyan
