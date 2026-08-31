@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  push.bat - commit, push, and deploy in one step.
REM
REM  Pushing main fires .github/workflows/deploy.yml, which syncs the
REM  code to the VM, applies database migrations and restarts the app.
REM  That is the intent - this script just adds the checks worth having
REM  before it happens:
REM
REM    * pushes the branch you are ACTUALLY on (the old version always
REM      pushed main, so work done on a branch was silently never sent,
REM      while it still printed "Deployment triggered")
REM    * refuses to commit anything that looks like a credential, TLS
REM      key, the database, or student photos - the one mistake that
REM      cannot be undone by a later commit
REM    * runs the test suite and stops if it fails
REM
REM  Usage:
REM     push.bat              commit, push, deploy
REM     push.bat --dry-run    show what would happen, change nothing
REM     push.bat --no-tests   skip the test run (hotfixes only)
REM ============================================================

cd /d "%~dp0"

set "DRYRUN="
set "SKIPTESTS="
for %%a in (%*) do (
    if /i "%%a"=="--dry-run" set "DRYRUN=1"
    if /i "%%a"=="-n"        set "DRYRUN=1"
    if /i "%%a"=="--no-tests" set "SKIPTESTS=1"
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Not a git repository.
    goto :end
)

for /f "tokens=*" %%i in ('git branch --show-current') do set "BRANCH=%%i"
if "!BRANCH!"=="" (
    echo [ERROR] Detached HEAD - check out a branch first.
    goto :end
)

echo ============================================================
echo   Branch: !BRANCH!
if /i "!BRANCH!"=="main" (
    echo   Pushing this branch WILL deploy to the live VM.
) else (
    echo   NOTE: the deploy workflow only runs on main, so pushing
    echo         !BRANCH! will NOT update the VM.
)
if defined DRYRUN echo   DRY RUN - nothing will be committed or pushed
echo ============================================================
echo.

REM ---------- uncommitted work ----------
set "DIRTY="
for /f "tokens=*" %%i in ('git status --porcelain') do set "DIRTY=1"

if not defined DIRTY (
    echo No uncommitted changes.
    goto :pushstep
)

echo Uncommitted changes:
git status --short
echo.

set "CONFIRM="
set /p CONFIRM="Stage all of the above and commit? [y/N]: "
if /i not "!CONFIRM!"=="y" (
    echo Aborted - nothing staged.
    goto :end
)

git add -A

REM ---------- pick the interpreter ----------
set "PY=venv_win\Scripts\python.exe"
if not exist "!PY!" set "PY=python"

REM ---------- secret backstop ----------
REM Done in Python (deploy/check_staged.py) because findstr's `$` anchor does
REM not work, so it could not tell .env from .env.example.
"!PY!" deploy\check_staged.py
if errorlevel 1 (
    echo.
    if not defined DRYRUN git reset >nul
    goto :end
)

echo.
echo Staged:
git diff --cached --stat
echo.

set "COMMIT_MSG="
set /p COMMIT_MSG="Commit message: "
if "!COMMIT_MSG!"=="" (
    echo [ERROR] A commit message is required. Aborted.
    if not defined DRYRUN git reset >nul
    goto :end
)

REM ---------- tests ----------
if defined SKIPTESTS (
    echo Skipping tests ^(--no-tests^).
) else (
    echo Running tests ...
    "!PY!" -m pytest -q
    if errorlevel 1 (
        echo.
        echo [FAILED] Tests did not pass - nothing was committed.
        echo Fix them, or re-run with --no-tests if this is a hotfix.
        if not defined DRYRUN git reset >nul
        goto :end
    )
    echo Tests passed.
)
echo.

if defined DRYRUN (
    echo [dry run] Would commit: !COMMIT_MSG!
    git reset >nul
    goto :pushstep
)

git commit -m "!COMMIT_MSG!"
if errorlevel 1 (
    echo [ERROR] Commit failed.
    goto :end
)

:pushstep
REM ---------- anything to push? ----------
set "AHEAD="
git rev-parse --abbrev-ref "!BRANCH!@{u}" >nul 2>&1
if errorlevel 1 (
    echo Branch !BRANCH! has no upstream yet - it will be created.
) else (
    for /f "tokens=*" %%i in ('git rev-list --count "@{u}..HEAD"') do set "AHEAD=%%i"
    if "!AHEAD!"=="0" (
        echo Already up to date with the remote - nothing to push.
        goto :end
    )
    echo !AHEAD! commit^(s^) to push:
    git log --oneline "@{u}..HEAD"
    echo.
)

if /i "!BRANCH!"=="main" (
    echo This deploys to the live VM: code sync, database migrations,
    echo app restart. Students may be marking attendance right now.
    echo.
    set "GO="
    set /p GO="Push and deploy? [y/N]: "
    if /i not "!GO!"=="y" (
        echo Aborted - nothing pushed. Your commit is safe locally.
        goto :end
    )
)

if defined DRYRUN (
    echo [dry run] Would run: git push -u origin !BRANCH!
    goto :end
)

echo.
echo Pushing !BRANCH! ...
git push -u origin "!BRANCH!"
if errorlevel 1 (
    echo.
    echo [ERROR] Push failed - your commit is still here locally.
    goto :end
)

echo.
if /i "!BRANCH!"=="main" (
    echo Pushed. Deployment triggered - watch it here:
    echo   https://github.com/Nishad599/face-attendance-system/actions
    echo.
    echo The workflow fails if the app does not answer within 60s of
    echo the restart, so a green tick means it is genuinely serving.
) else (
    echo Pushed !BRANCH!. No deployment - the workflow only runs on main.
)

:end
echo.
pause
endlocal
