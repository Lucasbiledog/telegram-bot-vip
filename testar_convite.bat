@echo off
chcp 65001 > nul
title Testar Convite do Canal VIP

echo.
echo ====================================
echo   TESTAR CONVITE DO CANAL VIP
echo ====================================
echo.

py testar_convite_canal.py 2>nul
if %errorlevel% neq 0 (
    python testar_convite_canal.py 2>nul
    if %errorlevel% neq 0 (
        python3 testar_convite_canal.py 2>nul
    )
)

echo.
pause
