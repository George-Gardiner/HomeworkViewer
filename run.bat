@echo off
setlocal
cd /d "%~dp0"
if not exist "site\index.html" (
  py build_site.py
  if errorlevel 1 exit /b %errorlevel%
)
start "" "%~dp0site\index.html"
endlocal
