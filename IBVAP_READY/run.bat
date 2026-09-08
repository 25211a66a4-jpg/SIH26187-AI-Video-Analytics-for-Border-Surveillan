@echo off
cd /d "%~dp0"
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -c "import fastapi,uvicorn,cv2" >nul 2>&1
if errorlevel 1 (
  echo Installing required packages for the first run...
  python -m pip install -r requirements.txt
)
echo.
echo ==================================================
echo   IBVAP AI BORDER SURVEILLANCE - V4 SECURED
 echo  Stopping any old server on port 8000...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do taskkill /PID %%P /F >nul 2>&1
echo   Starting the V4 server...
echo   Dashboard: http://127.0.0.1:8000
echo   Login: admin / IBVAP@2026
echo   Press CTRL+C in the server window to stop.
echo ==================================================
start "IBVAP V4 Backend" cmd /k "cd /d "%~dp0" && call .venv\Scripts\activate.bat && python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8000/login
