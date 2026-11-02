@echo off
title Plant Data Collector (in this window)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Foreground %*
pause
