@echo off
rem Daily market brief wrapper for the Windows Task Scheduler.
rem Runs from the repository root with the project venv; extra args pass through
rem (e.g. --dry-run, --date 2026-09-03). Credentials are loaded by
rem scripts\run-with-env.ps1 from %USERPROFILE%\.quantpilot-research.sources
rem (paths only; the secret values stay in the files that list points at)
rem and never from a file inside the repository.
rem "--%%" is PowerShell's stop-parsing token ("%" doubled for batch); without it
rem the job's own --out-dir/--dry-run flags are read as script parameters.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-with-env.ps1" --%% .venv\Scripts\python.exe -m quantpilot.jobs.run_market_brief %*
