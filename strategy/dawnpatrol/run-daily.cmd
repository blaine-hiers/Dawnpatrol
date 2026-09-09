@echo off
rem  run-daily.cmd -- what the scheduled task actually runs.
rem
rem  This exists because `schtasks /TR` will not take a compound command:
rem  an `&&` in there is rejected outright ("Invalid argument/option - '&&'"),
rem  and there is no working-directory option to use instead. So the task
rem  points at this one file and this file does the two steps.
rem
rem  Edit the last line to change how the collection runs:
rem     --no-summary   skip the claude briefing (saves your Claude allowance)
rem     --days N       widen or narrow the window, 1 to 14
rem     (drop --quiet) to leave console output in the task history

cd /d "%~dp0"
py "%~dp0app.py" --once --quiet
exit /b %ERRORLEVEL%
