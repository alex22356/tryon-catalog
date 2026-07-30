@echo off
title Chrome for tryon (port 9222)
echo.
echo  This starts YOUR Chrome (your account, your bookmarks)
echo  with debug port 9222, so the tryon script can attach to it.
echo.
echo  All Chrome windows will be closed first.
echo.
pause

taskkill /F /IM chrome.exe >nul 2>&1
timeout /t 3 /nobreak >nul

set CHROME="%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

start "" %CHROME% --remote-debugging-port=9222 https://aistudio.google.com/prompts/new_chat

echo.
echo  DONE. Chrome is starting with AI Studio.
echo.
echo  1) check you are signed in to your Google account
echo  2) pick model: Gemini 2.5 Flash Image
echo  3) go back to the catalog menu and choose item 3
echo.
timeout /t 10 /nobreak >nul
