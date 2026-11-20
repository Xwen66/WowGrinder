@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=C:\Users\cluo0\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
"%PY%" main.py
if errorlevel 1 pause
