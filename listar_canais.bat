@echo off
chcp 65001 > nul
title Listar Canais do Bot

echo.
echo ====================================
echo   LISTAR CANAIS/GRUPOS DO BOT
echo ====================================
echo.

REM Tentar diferentes comandos Python
py listar_canais_bot.py 2>nul
if %errorlevel% neq 0 (
    python listar_canais_bot.py 2>nul
    if %errorlevel% neq 0 (
        python3 listar_canais_bot.py 2>nul
        if %errorlevel% neq 0 (
            echo ERRO: Python não encontrado!
            echo.
            echo Certifique-se de ter o Python instalado.
            pause
            exit /b 1
        )
    )
)

echo.
pause
