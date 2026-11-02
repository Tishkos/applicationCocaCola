@echo off
title Plant Data Collector - start SESSION.bat with Windows
cd /d "%~dp0"
rem Registers a Windows task that opens SESSION.bat in its window every time a user logs on to this PC.
rem Run it once with a right-click > "Run as administrator". SESSION-AUTOSTART-OFF.bat removes it.
rem A second window never doubles the work: a session already running makes the new one stop at once.
schtasks /Create /TN "Plant Data Collector - session" /TR "\"%~dp0SESSION.bat\"" /SC ONLOGON /RL HIGHEST /F
if errorlevel 1 (
  echo.
  echo Could not register the task. Right-click this file and choose "Run as administrator".
) else (
  echo.
  echo Done: SESSION.bat opens by itself at every logon. Make sure Windows logs on by itself after a restart.
)
pause
