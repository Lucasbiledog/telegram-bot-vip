@echo off
chcp 65001 > nul
title Verificar Configuração

echo.
echo ====================================
echo   VERIFICAÇÃO DE CONFIGURAÇÃO
echo ====================================
echo.

REM Tentar diferentes comandos Python
py verificar_config_canais.py 2>nul
if %errorlevel% neq 0 (
    python verificar_config_canais.py 2>nul
    if %errorlevel% neq 0 (
        python3 verificar_config_canais.py 2>nul
        if %errorlevel% neq 0 (
            echo ERRO: Python não encontrado!
            echo.
            pause
            exit /b 1
        )
    )
)

echo.
pause
