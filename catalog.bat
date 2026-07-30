@echo off
chcp 65001 >nul
title Tryon Catalog
cd /d "%~dp0"
python scripts\menu.py
pause
