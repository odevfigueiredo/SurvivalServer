@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  set "PYTHON=py -3"
) else (
  set "PYTHON=python"
)
%PYTHON% server.py init --if-needed
if errorlevel 1 (pause & exit /b 1)
echo.
echo === Iniciando servidor na rede local ===
%PYTHON% server.py run --lan
pause
