@echo off
echo =============================================
echo   EURL E-BUSINESS LAB  -  Facturation App
echo =============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERREUR: Python n'est pas installe.
    echo Telechargez Python sur https://python.org
    pause
    exit /b 1
)

REM Install / update dependencies silently
echo Installation des dependances...
pip install -r requirements.txt -q --disable-pip-version-check
echo OK.
echo.

echo Demarrage du serveur sur http://localhost:5050
echo Appuyez sur Ctrl+C pour arreter.
echo.

REM Open browser after a short delay
start /b cmd /c "timeout /t 2 >nul && start http://localhost:5050"

REM Start FastAPI
python -m uvicorn main:app --host 0.0.0.0 --port 5050 --reload
