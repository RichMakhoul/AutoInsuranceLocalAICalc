@echo off
REM Start the Auto Rate Explorer on Windows.
setlocal
cd /d "%~dp0"
if "%PORT%"=="" set PORT=5000

echo == Auto Rate Explorer ==

curl -s -f http://localhost:11434/api/tags >nul 2>&1
if %errorlevel%==0 (
  echo [ok]   ollama running
  ollama list 2>nul | findstr /i /c:"llama3.2:3b" >nul || (
    echo [..]   pulling llama3.2:3b ^(one time, ~2GB^)
    ollama pull llama3.2:3b
  )
) else (
  echo [warn] ollama not reachable - app will use deterministic fallbacks
  echo        start the Ollama app, or run: ollama serve
)

set PY=venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo [ok]   serving on http://localhost:%PORT%
echo.
"%PY%" backend\app.py
endlocal
