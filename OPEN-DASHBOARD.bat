@echo off
cd /d "%~dp0"
set URL=http://localhost:8080/
if exist "data\dashboard.url" set /p URL=<"data\dashboard.url"
start "" "%URL%"
