@echo off
chcp 65001 > nul
setlocal

cd /d "%~dp0\.."

set "PYTHON_EXE="

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    where py >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=py -3.12"
    ) else (
        set "PYTHON_EXE=python"
    )
)

"%PYTHON_EXE%" scripts\crawl_wenku.py %*

endlocal
