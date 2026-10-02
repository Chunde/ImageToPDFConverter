@echo off
REM Double-click launcher for the Image to PDF Converter.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on PATH.
  echo Install Python 3.10+ from https://www.python.org/downloads/ and try again.
  pause
  exit /b 1
)

python -c "import PIL, pypdf" >nul 2>nul
if errorlevel 1 (
  echo Installing required packages ^(Pillow, pypdf^)...
  python -m pip install --quiet --disable-pip-version-check Pillow pypdf
  if errorlevel 1 (
    echo Could not install dependencies. Run: python -m pip install Pillow pypdf
    pause
    exit /b 1
  )
)

python run.py
if errorlevel 1 pause
endlocal
