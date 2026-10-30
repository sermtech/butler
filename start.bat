@echo off
REM Start the Butler daemon: TLS on 8140 (localhost, or every interface when the
REM lan_access setting is on, docs/remote-access.md), plus
REM plain HTTP on 127.0.0.1:8141 (this machine) and 127.0.0.1:8142 (for
REM `tailscale serve`). Stop it with stop.bat.
REM
REM Everything else -- the data root (including the registry read), the
REM not-migrated refusal, clearing a stale port, the certificate, appending
REM to butler.log -- lives in scripts\launch.py, the ONE launcher the desktop
REM app runs too (docs/desktop-app.md DA-9), so the two cannot drift.
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 scripts\launch.py %*
if errorlevel 1 pause
