@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || goto :fail
echo.
echo Housekeeperr running at http://localhost:8765
python -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --reload
goto :eof
:fail
echo Failed to start. See messages above.
exit /b 1
