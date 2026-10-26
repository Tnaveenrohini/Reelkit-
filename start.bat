@echo off
cd /d "%~dp0"
echo.
echo  WaveRise - run on this computer
echo  Log in to Instagram in your browser first (Firefox works best).
echo.
set /p B=Which browser are you logged in with? [firefox] 
if "%B%"=="" set B=firefox
python run_local.py --browser %B%
pause
