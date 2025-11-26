@echo off
chcp 65001 > nul
title Teste Completo do Fluxo de Pagamento

echo.
echo ========================================
echo   TESTE COMPLETO - FLUXO DE PAGAMENTO
echo ========================================
echo.

py testar_fluxo_completo.py 2>nul
if %errorlevel% neq 0 (
    python testar_fluxo_completo.py 2>nul
    if %errorlevel% neq 0 (
        python3 testar_fluxo_completo.py 2>nul
    )
)

echo.
pause
