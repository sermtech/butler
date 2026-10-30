@echo off
REM Stop the Butler daemon: kill whatever is listening on port 8140, and its
REM children (the kokoro sidecar, any coding-agent child). Safe to run twice.
set _found=
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8140 " ^| findstr LISTENING') do (
  taskkill /F /T /PID %%p >nul 2>&1
  if not errorlevel 1 set _found=1
)
if defined _found (echo Butler stopped.) else (echo Butler was not running on port 8140.)
