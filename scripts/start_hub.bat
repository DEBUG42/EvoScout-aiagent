@echo off
rem EvoScout AI Agent supervise startup script. Run this file from any location.
cd /d %~dp0\..
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
if not exist data\logs mkdir data\logs
.venv\Scripts\python.exe run.py --supervise >> data\logs\service.log 2>&1
