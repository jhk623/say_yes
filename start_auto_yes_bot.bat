@echo off
title auto_yes_bot
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher\start_auto_yes_bot.ps1" %*
echo.
pause
