@echo off
title PLC lab (model only, touches nothing)
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (set PY=.venv\Scripts\python.exe) else (set PY=python)
start "" http://localhost:8090
%PY% tools\plc_lab.py
pause
