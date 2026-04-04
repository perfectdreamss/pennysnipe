@echo off
title Deal Finder
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
pip install -r requirements.txt -q
python deal_finder.py
pause
