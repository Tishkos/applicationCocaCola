@echo off
title Plant Data Collector - trace (read only)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Trace
pause
