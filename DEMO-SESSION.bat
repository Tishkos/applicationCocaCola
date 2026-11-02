@echo off
title QS Collector - DEMO (simulated plant)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -SessionDemo
pause
