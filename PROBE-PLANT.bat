@echo off
title Plant Data Collector - probe
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Probe
pause
