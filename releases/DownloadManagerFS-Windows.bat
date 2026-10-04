@echo off
title DownloadManagerFS - Engine & Web UI
color 0A

echo ========================================================
echo        DownloadManagerFS - Windows Launcher
echo ========================================================
echo.

:: Check for Python
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    where py >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Python is not detected on your system.
        echo Please install Python 3.10+ from https://www.python.org/downloads/
        echo Make sure to check "Add Python to PATH" during installation.
        echo.
        pause
        exit /b 1
    ) else (
        set PY_CMD=py
    )
) else (
    set PY_CMD=python
)

echo [*] Checking and updating yt-dlp engine...
%PY_CMD% -m pip install --quiet --upgrade yt-dlp

echo [*] Starting DownloadManagerFS server on http://localhost:5000 ...
echo [*] Press Ctrl+C in this terminal to stop the server.
echo.

start "" http://localhost:5000
%PY_CMD% server.py

pause
