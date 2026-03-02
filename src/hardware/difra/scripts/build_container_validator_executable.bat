@echo off
setlocal

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo PyInstaller is not installed. Install it first with:
  echo   python -m pip install pyinstaller
  exit /b 2
)

set SCRIPT_DIR=%~dp0
for %%I in ("%SCRIPT_DIR%..\..\..\..") do set REPO_ROOT=%%~fI

pushd "%REPO_ROOT%"
python -m PyInstaller ^
  --clean ^
  --noconfirm ^
  --onefile ^
  --name difra-container-validate-windows ^
  --paths src ^
  --collect-all h5py ^
  src\hardware\difra\scripts\validate_container.py
popd

echo Built executable: %REPO_ROOT%\dist\difra-container-validate-windows.exe
