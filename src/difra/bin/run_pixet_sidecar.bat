@echo off
setlocal enabledelayedexpansion

REM Determine repository root (three levels up: bin -> difra -> src -> root)
set SCRIPT_DIR=%~dp0
for %%I in ("%SCRIPT_DIR%..\..\..") do set REPO_ROOT=%%~fI
set MAIN_CONFIG_PATH=%REPO_ROOT%\src\difra\resources\config\main_win.json

set SIDECAR_ENV=%DIFRA_SIDECAR_ENV%
if "%SIDECAR_ENV%"=="" call :read_main_sidecar_env SIDECAR_ENV
if "%SIDECAR_ENV%"=="" set SIDECAR_ENV=eosdx_pixet

set SIDECAR_HOST=%PIXET_SIDECAR_HOST%
if "%SIDECAR_HOST%"=="" set SIDECAR_HOST=127.0.0.1

set SIDECAR_PORT=%PIXET_SIDECAR_PORT%
if "%SIDECAR_PORT%"=="" set SIDECAR_PORT=51001

set CONDA_CMD=conda
where conda >nul 2>&1
if errorlevel 1 (
  echo [INFO] 'conda' not found in PATH, searching common installation locations...

  set CONDA_PATHS[0]=%USERPROFILE%\anaconda3
  set CONDA_PATHS[1]=%USERPROFILE%\miniconda3
  set CONDA_PATHS[2]=C:\ProgramData\Anaconda3
  set CONDA_PATHS[3]=C:\ProgramData\Miniconda3
  set CONDA_PATHS[4]=C:\Anaconda3
  set CONDA_PATHS[5]=C:\Miniconda3
  set CONDA_PATHS[6]=C:\Users\Ulster\anaconda3

  set CONDA_FOUND=0
  for /L %%i in (0,1,6) do (
    if defined CONDA_PATHS[%%i] (
      set CONDA_PATH=!CONDA_PATHS[%%i]!
      if exist "!CONDA_PATH!\Scripts\conda.exe" (
        set CONDA_CMD="!CONDA_PATH!\Scripts\conda.exe"
        echo [INFO] Found conda at: !CONDA_PATH!
        set CONDA_FOUND=1
        goto :conda_found
      )
    )
  )

  :conda_found
  if !CONDA_FOUND!==0 (
    echo [ERROR] Could not find conda installation.
    exit /b 1
  )
)

cd /d %REPO_ROOT%
call "%REPO_ROOT%\src\difra\bin\ensure_pixet_sidecar_runtime.bat"
if errorlevel 1 exit /b 1

set PYTHONPATH=%REPO_ROOT%\src;%PYTHONPATH%
set PYTHONUNBUFFERED=1

set SIDECAR_PY=
for /f "usebackq delims=" %%V in (`%CONDA_CMD% run --live-stream --no-capture-output -n %SIDECAR_ENV% python -c "import sys,platform; print(f'{sys.version_info[0]}.{sys.version_info[1]} {platform.architecture()[0]}')" 2^>nul`) do set SIDECAR_PY=%%V
if "%SIDECAR_PY%"=="" (
  echo [ERROR] Sidecar env '%SIDECAR_ENV%' is not available.
  exit /b 1
)
if /I not "%SIDECAR_PY%"=="3.12 64bit" (
  echo [ERROR] Sidecar env '%SIDECAR_ENV%' must be Python 3.12 64-bit, found %SIDECAR_PY%.
  exit /b 1
)

echo [INFO] Starting PIXet sidecar in env: %SIDECAR_ENV%
echo [INFO] Sidecar endpoint: %SIDECAR_HOST%:%SIDECAR_PORT%
echo [INFO] PIXet SDK path: %PIXET_SDK_PATH%
if "%DIFRA_SIDECAR_LOG_PATH%"=="" set "DIFRA_SIDECAR_LOG_PATH=%LOCALAPPDATA%\DiFRA\logs\pixet_sidecar.log"
for %%I in ("%DIFRA_SIDECAR_LOG_PATH%") do if not exist "%%~dpI" mkdir "%%~dpI"
echo [INFO] Sidecar log: %DIFRA_SIDECAR_LOG_PATH%

%CONDA_CMD% run --live-stream --no-capture-output -n %SIDECAR_ENV% python -u "%REPO_ROOT%\src\difra\scripts\pixet_sidecar_server.py" --host %SIDECAR_HOST% --port %SIDECAR_PORT%
set "SIDECAR_EXIT_CODE=%ERRORLEVEL%"

endlocal & exit /b %SIDECAR_EXIT_CODE%

:read_main_sidecar_env
setlocal
set "SIDE_ENV_VALUE="
for /f "usebackq delims=" %%E in (`powershell -NoProfile -Command "$ErrorActionPreference='SilentlyContinue'; $p=$env:MAIN_CONFIG_PATH; if(Test-Path $p){ $j=Get-Content -Raw $p | ConvertFrom-Json; [string]$j.sidecar_conda }"`) do set "SIDE_ENV_VALUE=%%E"
endlocal & set "%~1=%SIDE_ENV_VALUE%" & exit /b 0
