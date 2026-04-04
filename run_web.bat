@echo off
title Pennysnipe — Web Server
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
pip install -r requirements.txt -q
echo.
echo  Starting Pennysnipe at http://localhost:5000
echo  Press Ctrl+C to stop.
echo.
python app.py
pause
