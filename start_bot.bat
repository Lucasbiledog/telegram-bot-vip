@echo off
title Bot Telegram - Supervisor
echo.
echo ========================================
echo   BOT TELEGRAM - SUPERVISOR ROBUSTO
echo ========================================
echo.
echo 🤖 Iniciando supervisor do bot...
echo 📱 Para parar: Feche esta janela ou Ctrl+C (duas vezes)
echo 🔄 O bot será reiniciado automaticamente em caso de falha
echo.

cd /d "%~dp0"
python start_bot.py

echo.
echo ⏹️ Supervisor do bot finalizado
pause