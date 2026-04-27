@echo off
set /p commit_msg="Enter commit message: "
if "%commit_msg%"=="" set commit_msg="Update UI and stabilize local dev"

echo Staging changes...
git add .

echo Committing changes...
git commit -m "%commit_msg%"

echo Pushing to production...
git push origin main

echo Done! Deployment triggered.
pause
