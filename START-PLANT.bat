@echo off
title Plant Data Collector (real plant)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Plant %*
pause
