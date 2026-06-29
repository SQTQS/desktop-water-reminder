@echo off
set "APP_DIR=%~dp0"

where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw "%APP_DIR%water_reminder.pyw"
  exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%APP_DIR%water_reminder.pyw"
  exit /b 0
)

python "%APP_DIR%water_reminder.pyw"
