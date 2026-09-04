@echo off
rem Daily market brief wrapper for the Windows Task Scheduler.
rem Runs from the repository root with the project venv; extra args pass through
rem (e.g. --dry-run, --date 2026-09-03). Credentials come from the user's
rem environment variables, never from a file.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m quantpilot.jobs.run_market_brief %*
