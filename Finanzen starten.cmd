@echo off
chcp 65001 >nul
cd /d "%~dp0"
title JU Hamburg Finanzen
py -3 app.py
if errorlevel 1 (
  echo.
  echo Start fehlgeschlagen. Laeuft das Programm bereits?
  echo Falls Python fehlt: Python 3.11 oder neuer installieren und
  echo   py -3 -m pip install -r requirements.txt
  echo ausfuehren.
  pause
)
