@echo off
setlocal enabledelayedexpansion

REM Bootstrap Windows PIXet sidecar runtime:
REM - conda env: eosdx_pixet
REM - Python: 3.12 x64
REM - SDK: PIXet Pro GUI/API 1.8.5 x64 downloaded from Advacam

set SCRIPT_DIR=%~dp0
for %%I in ("%SCRIPT_DIR%..\..\..") do set REPO_ROOT=%%~fI

set SIDECAR_ENV=%DIFRA_SIDECAR_ENV%
if "%SIDECAR_ENV%"=="" set SIDECAR_ENV=eosdx_pixet

set PIXET_SDK_URL=%DIFRA_PIXET_SDK_URL%
if "%PIXET_SDK_URL%"=="" set PIXET_SDK_URL=https://advacam.com/content/uploads/2026/03/PIXet_Pro_GUI_1.8.5_Windows_x64.zip

set PIXET_CACHE_ROOT=%DIFRA_PIXET_CACHE_ROOT%
if "%PIXET_CACHE_ROOT%"=="" (
  if exist D:\ (
    set PIXET_CACHE_ROOT=D:\API_PIXet_Pro_1.8.5_Windows_x64
  ) else (
    set PIXET_CACHE_ROOT=%LOCALAPPDATA%\DiFRA\pixet\API_PIXet_Pro_1.8.5_Windows_x64
  )
)

set PIXET_DOWNLOAD_DIR=%PIXET_CACHE_ROOT%\downloads
set PIXET_EXTRACT_ROOT=%PIXET_CACHE_ROOT%\sdk
set PIXET_ZIP=%PIXET_DOWNLOAD_DIR%\PIXet_Pro_GUI_1.8.5_Windows_x64.zip
set PIXET_ENV_YAML=%REPO_ROOT%\src\difra\environment-eosdx-pixet.yml
set PIXET_CONDA_PACKAGES=python=3.12 pip numpy

set CONDA_CMD=%DIFRA_CONDA_EXE%
if "%CONDA_CMD%"=="" set CONDA_CMD=conda
where %CONDA_CMD% >nul 2>&1
if errorlevel 1 (
  set CONDA_CMD=conda
  where conda >nul 2>&1
)
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

echo [INFO] Ensuring PIXet sidecar env=%SIDECAR_ENV% ^(Python 3.12 x64^)
%CONDA_CMD% run --no-capture-output -n %SIDECAR_ENV% python -c "import sys,platform; assert sys.version_info[:2]==(3,12); assert platform.architecture()[0]=='64bit'" >nul 2>&1
if errorlevel 1 (
  echo [INFO] Creating/updating %SIDECAR_ENV% from %PIXET_ENV_YAML%
  %CONDA_CMD% env list | findstr /I /R "\<%SIDECAR_ENV%\>" >nul 2>&1
  if errorlevel 1 (
    %CONDA_CMD% env create -f "%PIXET_ENV_YAML%"
  ) else (
    %CONDA_CMD% env update -n %SIDECAR_ENV% -f "%PIXET_ENV_YAML%" --prune
  )
  if errorlevel 1 (
    echo [ERROR] Failed to create/update PIXet sidecar env %SIDECAR_ENV%.
    exit /b 1
  )
)

set SIDECAR_PY=
for /f "usebackq delims=" %%V in (`%CONDA_CMD% run --no-capture-output -n %SIDECAR_ENV% python -c "import sys,platform; print(f'{sys.version_info[0]}.{sys.version_info[1]} {platform.architecture()[0]}')" 2^>nul`) do set SIDECAR_PY=%%V
if /I not "%SIDECAR_PY%"=="3.12 64bit" (
  echo [ERROR] Sidecar env '%SIDECAR_ENV%' must be Python 3.12 64-bit, found %SIDECAR_PY%.
  exit /b 1
)

echo [INFO] Ensuring PIXet sidecar Python packages: %PIXET_CONDA_PACKAGES%
%CONDA_CMD% run --no-capture-output -n %SIDECAR_ENV% python -c "import numpy" >nul 2>&1
if errorlevel 1 (
  %CONDA_CMD% install -y -n %SIDECAR_ENV% %PIXET_CONDA_PACKAGES%
  if errorlevel 1 (
    echo [ERROR] Failed to install PIXet sidecar packages in %SIDECAR_ENV%.
    exit /b 1
  )
)
%CONDA_CMD% run --no-capture-output -n %SIDECAR_ENV% python -c "import sys,platform,numpy; assert sys.version_info[:2]==(3,12); assert platform.architecture()[0]=='64bit'; print(numpy.__version__)" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] PIXet sidecar package verification failed in %SIDECAR_ENV%.
  exit /b 1
)

if not exist "%PIXET_DOWNLOAD_DIR%" mkdir "%PIXET_DOWNLOAD_DIR%"
if not exist "%PIXET_ZIP%" (
  echo [INFO] Downloading PIXet SDK from %PIXET_SDK_URL%
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PIXET_SDK_URL%' -OutFile '%PIXET_ZIP%'"
  if errorlevel 1 (
    echo [ERROR] Failed to download PIXet SDK: %PIXET_SDK_URL%
    exit /b 1
  )
) else (
  echo [INFO] PIXet SDK ZIP already cached: %PIXET_ZIP%
)

if not exist "%PIXET_EXTRACT_ROOT%" (
  echo [INFO] Unpacking PIXet SDK to %PIXET_EXTRACT_ROOT%
  mkdir "%PIXET_EXTRACT_ROOT%"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Expand-Archive -Force -Path '%PIXET_ZIP%' -DestinationPath '%PIXET_EXTRACT_ROOT%'"
  if errorlevel 1 (
    echo [ERROR] Failed to unpack PIXet SDK.
    exit /b 1
  )
)

set PIXET_DLL=
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "$dll=Get-ChildItem -Path '%PIXET_EXTRACT_ROOT%' -Recurse -Filter pxcore.dll -File | Select-Object -First 1; if($dll){ Write-Output $dll.DirectoryName }"`) do set PIXET_DLL=%%P
if "%PIXET_DLL%"=="" (
  echo [ERROR] pxcore.dll not found under %PIXET_EXTRACT_ROOT%.
  exit /b 1
)

echo [INFO] PIXet SDK path: %PIXET_DLL%
endlocal & set "DIFRA_SIDECAR_ENV=%SIDECAR_ENV%" & set "PIXET_SDK_PATH=%PIXET_DLL%" & set "DIFRA_PIXET_SDK_URL=%PIXET_SDK_URL%" & exit /b 0
