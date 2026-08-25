@echo off
setlocal
for %I in ("%~dp0..") do set "KIT=%~fI"
python "%KIT%\bin\prose" %*
