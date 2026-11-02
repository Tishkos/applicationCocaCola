@echo off
title Plant Data Collector - stop starting SESSION.bat with Windows
schtasks /Delete /TN "Plant Data Collector - session" /F
pause
