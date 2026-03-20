@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_matrix_synapse_windows.ps1" %*
exit /b %errorlevel%
