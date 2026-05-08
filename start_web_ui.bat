@echo off
setlocal

cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "STREAMLIT_EXE=%~dp0.venv\Scripts\streamlit.exe"

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

"%VENV_PY%" -c "import streamlit" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Streamlit is not installed in the project .venv.
    echo.
    echo Please run:
    echo   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if exist "%STREAMLIT_EXE%" (
    "%STREAMLIT_EXE%" run app.py
) else (
    "%VENV_PY%" -m streamlit run app.py
)

if errorlevel 1 (
    echo.
    echo [ERROR] Streamlit UI exited with an error.
    echo.
    pause
    exit /b 1
)

endlocal
