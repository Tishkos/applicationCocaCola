@echo off
rem Plant Collector as an application window (starts the collector first if it is not running).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0app\launch.ps1" %*
