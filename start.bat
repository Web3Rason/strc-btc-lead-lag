@echo off
cd /d "%~dp0"
title STRC-BTC Lead-Lag

cd backend
start /B python app.py > nul 2>&1
