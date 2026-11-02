@echo off
title CCI Plant Monitoring (Grafana demo)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\grafana.ps1" -Demo %*
pause
