@echo off
cd /d "%~dp0"
if not exist "data\evidence" mkdir "data\evidence"
start "" explorer "%~dp0data\evidence"
