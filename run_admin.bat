@echo off
powershell -Command "Start-Process python -ArgumentList 'E:\smart-mic-guardian\main.py' -Verb runAs"
pause