@echo off

cd /d D:\GoogleScholarCrawler-master

set LOG_FILE=run_%date:~0,4%%date:~5,2%%date:~8,2%.log

echo ===== START %date% %time% =====

powershell -NoProfile -Command "& 'C:\Users\15728\AppData\Local\Microsoft\WindowsApps\python3.11.exe' -u nature_play.py 2>&1 | Tee-Object -FilePath '%LOG_FILE%'"

echo ===== END %date% %time% =====