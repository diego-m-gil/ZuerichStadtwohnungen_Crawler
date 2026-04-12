@echo off
REM SSH test to production VPS (paths relative to this script's folder).
cd /d "%~dp0.."

echo Testing SSH connection to server...
echo.

echo Testing network connectivity...
ping -n 2 91.214.190.28

echo.
echo Attempting SSH connection...
ssh -o ConnectTimeout=30 -o ServerAliveInterval=60 -i C:\Users\info\.ssh\id_rsa ubuntu@91.214.190.28 "pwd && ls -la && echo 'Connection successful'"

pause
