@echo off
REM ============================================================
REM  Profinet Gateway - one-click Windows EXE builder
REM  Double-click this file (or run it from a cmd window).
REM  Output:  dist\ProfinetGateway\ProfinetGateway.exe
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo ==== Profinet Gateway : build EXE ====
echo.

REM --- 1. locate Python -------------------------------------------------
where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY=python"
    ) else (
        echo [ERROR] Python not found. Install Python 3.9+ from python.org
        echo         and tick "Add Python to PATH".
        pause
        exit /b 1
    )
)
echo Using Python: %PY%
%PY% --version

REM --- 2. virtual environment ------------------------------------------
if not exist ".venv\" (
    echo Creating virtual environment .venv ...
    %PY% -m venv .venv
)
call ".venv\Scripts\activate.bat"

REM --- 3. dependencies -------------------------------------------------
echo Installing dependencies ...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

REM --- 4. clean previous build ----------------------------------------
if exist "build\"  rmdir /s /q "build"
if exist "dist\"   rmdir /s /q "dist"

REM --- 5. build --------------------------------------------------------
echo.
echo Building EXE ...
pyinstaller --noconfirm build.spec
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Build failed. See the messages above.
    pause
    exit /b 1
)

echo.
echo ==== DONE ====
echo EXE: %cd%\dist\ProfinetGateway\ProfinetGateway.exe
echo (Ship the whole dist\ProfinetGateway folder - not just the .exe.)
echo Run it as Administrator so raw Ethernet / Npcap works.
echo.
pause
endlocal
