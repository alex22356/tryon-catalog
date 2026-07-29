@echo off
chcp 65001 >nul
title Каталог примерочной
cd /d "%~dp0"

:menu
cls
echo ============================================================
echo                 КАТАЛОГ ПРИМЕРОЧНОЙ
echo ============================================================
echo.
echo   1  Собрать ссылки      - откроет инструкцию-граббер в браузере
echo   2  Вставить ссылки     - откроет shein_links.txt (Ctrl+V, сохрани)
echo   3  ОБНОВИТЬ КАТАЛОГ    - разберёт новые ссылки, опубликует примерки
echo   4  Примерка (Gemini)   - оденет модель в новые вещи
echo   5  Выложить всем       - публикация в сеть + git push
echo.
echo   0  Выход
echo.
set /p choice="Выбери пункт: "

if "%choice%"=="1" goto grab
if "%choice%"=="2" goto links
if "%choice%"=="3" goto update
if "%choice%"=="4" goto tryon
if "%choice%"=="5" goto deploy
if "%choice%"=="0" exit
goto menu

:grab
start "" "%~dp0tools\link_grabber.html"
echo.
echo Открыл инструкцию. Собери ссылки в браузере, потом пункт 2.
pause
goto menu

:links
start "" notepad "%~dp0shein_links.txt"
echo.
echo Вставь ссылки (Ctrl+V), сохрани (Ctrl+S), закрой. Потом пункт 3.
pause
goto menu

:update
echo.
python scripts\run_pipeline.py
echo.
pause
goto menu

:tryon
echo.
echo Откроется браузер. Войди в Google, открой AI Studio,
echo выбери модель "Gemini 2.5 Flash Image", вернись и нажми Enter.
echo.
python scripts\gemini_browser_runner.py
echo.
pause
goto menu

:deploy
echo.
python scripts\publish_remote.py
echo.
git add -A
git commit -m "catalog update"
git push
echo.
echo Если push прошёл - каталог уже у всех клиентов.
pause
goto menu
