@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1

if not exist ".venv\Scripts\python.exe" (
    echo Media Studio requires a project virtual environment.
    echo Follow the setup instructions in README.md, then run this script again.
    popd
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m app %*
set "studio_exit_code=%errorlevel%"
if not "%studio_exit_code%"=="0" (
    echo.
    echo Media Studio exited with error code %studio_exit_code%.
    echo Check the message above and logs\media-studio.log for details.
    pause
)
popd
exit /b %studio_exit_code%
