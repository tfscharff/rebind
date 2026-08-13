@echo off
rem Double-click launcher for running Rebind from this clone, no install required.
rem
rem The installed app and this are independent: the installer runs a frozen copy from
rem %LOCALAPPDATA%, this runs the code you are editing. Uninstalling Rebind does not
rem affect this launcher.
rem
rem This window IS the server. Closing it stops Rebind. The app also exits on its own about
rem two and a half minutes after you close the browser tab.

cd /d "%~dp0"

where uv >nul 2>&1
if errorlevel 1 (
    echo uv is not on PATH. Rebind runs on Python 3.12 through uv -- the system Python
    echo is 3.14 and lacks wheels for parts of the OCR stack.
    echo Install it from https://docs.astral.sh/uv/ and run this again.
    echo.
    pause
    exit /b 1
)

rem An instance already listening is the ordinary case, not an error: the server keeps running for
rem a couple of minutes after its tab closes, and starting a second one during that window used to
rem fail twice over -- uv cannot reinstall the project while the running server holds
rem .venv\Scripts\rebind.exe, and the port is taken anyway. Open the browser at the one that is
rem already there instead, and say so.
netstat -ano -p tcp | findstr /r /c:"LISTENING" | findstr /c:"127.0.0.1:8756" >nul 2>&1
if not errorlevel 1 (
    echo Rebind is already running. Opening it.
    echo.
    echo   If you are here to pick up new code, stop that one first: close its window,
    echo   or wait for it to exit on its own, then run this again.
    start "" "http://127.0.0.1:8756/"
    timeout /t 6 >nul
    exit /b 0
)

echo Starting Rebind from %cd%
echo A browser tab will open in a moment. Keep this window open while you work.
echo.

uv run rebind serve

rem Never close on a failure without showing it: this window is the only place the reason appears,
rem and a launcher that vanishes leaves nothing to go on but a browser tab that cannot connect.
if errorlevel 1 (
    echo.
    echo Rebind exited with an error -- the detail is above.
    echo.
    pause
)
