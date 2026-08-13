@echo off
rem Double-click launcher for running Rebind from this clone, no install required.
rem
rem The installed app and this are independent: the installer runs a frozen copy from
rem %LOCALAPPDATA%, this runs the code you are editing. Uninstalling Rebind does not
rem affect this launcher. Both bind the same port, so run only one at a time.
rem
rem This window IS the server. Closing it stops Rebind. The app also exits on its own a
rem short while after you close the browser tab.

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

echo Starting Rebind from %cd%
echo A browser tab will open in a moment. Keep this window open while you work.
echo.

uv run rebind serve

if errorlevel 1 (
    echo.
    echo Rebind exited with an error. The detail above is usually enough; the server's own
    echo log is in %%LOCALAPPDATA%%\Rebind\rebind.log.
    pause
)
