# DownloadManagerFS - Windows PowerShell Launcher
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "       DownloadManagerFS - Windows Launcher" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) {
    $python = (Get-Command py -ErrorAction SilentlyContinue)
}

if (-not $python) {
    Write-Host "[ERROR] Python was not found in PATH." -ForegroundColor Red
    Write-Host "Please install Python 3.10+ from https://www.python.org/downloads/ and add it to PATH."
    Read-Host "Press Enter to exit..."
    Exit 1
}

Write-Host "[*] Checking and updating yt-dlp..." -ForegroundColor Yellow
& $python.Source -m pip install --quiet --upgrade yt-dlp

Write-Host "[*] Launching DownloadManagerFS Web UI at http://localhost:5000 ..." -ForegroundColor Green
Start-Process "http://localhost:5000"

& $python.Source server.py
