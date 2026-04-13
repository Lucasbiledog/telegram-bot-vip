@echo off
chcp 65001 >nul
echo ======================================================================
echo 🔍 VERIFICAR GRUPOS E IDs
echo ======================================================================
echo.
echo Este script vai listar TODOS os grupos que você participa.
echo Anote os IDs corretos para usar na transferência.
echo.
pause
echo.

python descobrir_ids.py

pause
