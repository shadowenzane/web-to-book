@echo off
rem Build 网页成书.exe with PyInstaller (run on Windows, Python 3.10+)
setlocal
cd /d "%~dp0\..\.."
python -m pip install --upgrade pip
python -m pip install reportlab pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "网页成书" ^
  --add-data "core;core" ^
  --collect-submodules reportlab ^
  --paths core ^
  desktop\windows\app.py
echo built: dist\网页成书.exe
