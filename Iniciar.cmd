@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto error
)
".venv\Scripts\python.exe" -c "import pymupdf, flask" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto error
)
".venv\Scripts\python.exe" server.py --open --port 0
if errorlevel 1 goto error
exit /b 0
:error
echo No se pudo iniciar. Verifica que Python este instalado y que la primera ejecucion tenga Internet.
pause
exit /b 1
