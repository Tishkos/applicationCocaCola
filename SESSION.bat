@echo off
title QS Collector - SAU watch (keep this window open)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Session
pause
