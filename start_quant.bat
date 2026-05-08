@echo off
setlocal

cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "D:\tcl\tcl8.6\init.tcl" set "TCL_LIBRARY=D:\tcl\tcl8.6"
if exist "D:\tcl\tk8.6\tk.tcl" set "TK_LIBRARY=D:\tcl\tk8.6"

if not exist "%VENV_PY%" (
    echo [ERROR] Project virtual environment was not found.
    echo.
    echo Expected:
    echo   %VENV_PY%
    echo.
    echo Please create and install the project .venv first.
    echo Do not use global Python or global pip for this project.
    echo.
    pause
    exit /b 1
)

"%VENV_PY%" "%~dp0launcher.py" %*
if errorlevel 1 (
    echo.
    echo [ERROR] Launcher failed to start or exited with an error.
    echo Please read the error message above.
    echo.
    pause
    exit /b 1
)

endlocal
