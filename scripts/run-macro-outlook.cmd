@echo off
rem Weekly macro outlook wrapper for the Windows Task Scheduler (Sunday evening).
rem Runs from the repository root with the project venv; extra args pass through
rem (e.g. --dry-run, --skip-macro). Credentials (ECOS_API_KEY, FRED_API_KEY, news,
rem Slack) are loaded by scripts\run-with-env.ps1 from
rem %USERPROFILE%\.quantpilot-research.sources and never from a file inside the
rem repository. "--%%" is PowerShell's stop-parsing token ("%" doubled for batch).
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-with-env.ps1" --%% .venv\Scripts\python.exe -m quantpilot.jobs.run_macro_outlook %*
