@echo off
title Grammar Checker — Free Edition
echo.
echo  ✦ Grammar Checker (Free) — Setup
echo  ─────────────────────────────────────
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Install from python.org
    pause & exit /b 1
)

echo  [1/2] Installing dependencies...
pip install -r requirements.txt --quiet

echo  [2/2] Launching...
echo.
echo  ┌─────────────────────────────────────────────────┐
echo  │  App runs in system tray (bottom-right corner)  │
echo  │                                                  │
echo  │  HOW TO USE:                                     │
echo  │  1. Copy any text  (Ctrl+C)                      │
echo  │  2. Press  Ctrl+Shift+G                          │
echo  │  3. See corrections instantly!                   │
echo  │                                                  │
echo  │  Right-click tray icon → Settings for API key   │
echo  └─────────────────────────────────────────────────┘
echo.

start /b pythonw grammar_checker.py
timeout /t 2 >nul
echo  ✓ Grammar Checker is running in the background!
pause
