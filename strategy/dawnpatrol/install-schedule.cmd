@echo off
setlocal
rem  install-schedule.cmd -- run Dawnpatrol every morning at 06:00.
rem
rem      install-schedule.cmd          register it
rem      install-schedule.cmd remove   take it back out
rem
rem  Deliberate choices, so nobody has to guess later:
rem
rem    * Runs as you, only when you are logged in. No stored password, no
rem      SYSTEM account. A scheduled task holding client data under SYSTEM is
rem      not something this practice should own.
rem    * Does NOT wake the machine. A laptop that turns itself on at 6am to
rem      read the news is a laptop you unplug. Miss a morning and it catches
rem      up at the next login.
rem    * 06:00, against the 6:30am call block in the outreach playbook. The
rem      report should already be there when you sit down.
rem    * It points at run-daily.cmd rather than at a command line, because
rem      schtasks refuses a compound command and has no working-directory
rem      option. Change how it runs by editing that file, not this one.
rem
rem  It verifies the task afterwards by asking Windows for it back, rather
rem  than trusting an exit code. The first version of this script printed
rem  "Done." over the top of a failure, which is the one outcome worth
rem  engineering against.

set "TASKNAME=Plan B - Dawnpatrol"
set "HERE=%~dp0"
set "RUNNER=%HERE%run-daily.cmd"

if /I "%~1"=="remove" goto :remove

where py >nul 2>&1
if errorlevel 1 (
  echo   Could not find "py" on the PATH. Install Python, or edit
  echo   run-daily.cmd to use the full path to python.exe.
  exit /b 1
)

if not exist "%RUNNER%" (
  echo   Cannot find "%RUNNER%".
  echo   run-daily.cmd must sit next to this script.
  exit /b 1
)

echo   Registering "%TASKNAME%" for 06:00 daily...
echo.
schtasks /Create /TN "%TASKNAME%" /TR "\"%RUNNER%\"" /SC DAILY /ST 06:00 /RL LIMITED /IT /F

echo.
echo   Checking Windows actually has it...
schtasks /Query /TN "%TASKNAME%" >nul 2>&1
if errorlevel 1 (
  echo.
  echo   NOT REGISTERED. Windows does not have a task by that name.
  echo   Nothing was scheduled. The error above says why.
  exit /b 1
)

echo.
schtasks /Query /TN "%TASKNAME%" /FO LIST | findstr /I "TaskName Next Status"
echo.
echo   Registered. It runs at 06:00, as you, only while you are logged in.
echo.
echo   Run it now: schtasks /Run    /TN "%TASKNAME%"
echo   Remove it:  install-schedule.cmd remove
echo.
echo   It calls the claude CLI each morning for the written briefing, which
echo   uses your Claude allowance. Add --no-summary in run-daily.cmd to turn
echo   that off and keep the ranked list.
exit /b 0

:remove
echo   Removing "%TASKNAME%"...
schtasks /Delete /TN "%TASKNAME%" /F
schtasks /Query /TN "%TASKNAME%" >nul 2>&1
if not errorlevel 1 (
  echo   STILL THERE. It could not be removed -- the error above says why.
  exit /b 1
)
echo   Gone. Your reports and database were not touched.
exit /b 0
