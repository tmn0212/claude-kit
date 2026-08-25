@echo off
setlocal
rem Resolve the plugin root from this script's own location, quotes stripped.
for %%I in ("%~dp0..") do set "KIT=%%~fI"
rem Windows ships py.exe and python.exe but not python3. Prefer the launcher.
set "KITPY=python"
where py >nul 2>nul && set "KITPY=py"
"%KITPY%" "%KIT%\bin\brief" %*
