@echo off
title Plant Collector - install the desktop application
cd /d "%~dp0"
rem Creates the "Plant Collector" shortcut (Desktop + Start Menu) with its own icon and opens the app once.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\Install-DesktopShortcut.ps1"
if errorlevel 1 ( echo. & echo Could not create the shortcut. & pause & exit /b 1 )
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0app\launch.ps1"
echo.
echo Done. The "Plant Collector" icon is on the Desktop and in the Start Menu.
echo If the taskbar still shows an old icon for the running window: close the window, open it again from the icon;
echo if it still does, unpin the old one, then sign out of Windows and back in (icon cache).
pause
