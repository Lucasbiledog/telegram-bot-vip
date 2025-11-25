@echo off
echo ======================================
echo   INSTALANDO DEPENDENCIAS DO BOT
echo ======================================
echo.

echo Procurando Python instalado...
echo.

REM Tentar diferentes caminhos do Python
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo Python encontrado: python
    python --version
    echo.
    echo Instalando dependencias...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    goto :fim
)

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo Python encontrado: py
    py --version
    echo.
    echo Instalando dependencias...
    py -m pip install --upgrade pip
    py -m pip install -r requirements.txt
    goto :fim
)

REM Tentar caminho comum do Python
if exist "%LOCALAPPDATA%\Programs\Python\" (
    for /d %%i in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
        if exist "%%i\python.exe" (
            echo Python encontrado em: %%i
            "%%i\python.exe" --version
            echo.
            echo Instalando dependencias...
            "%%i\python.exe" -m pip install --upgrade pip
            "%%i\python.exe" -m pip install -r requirements.txt
            goto :fim
        )
    )
)

echo.
echo ERRO: Python nao encontrado!
echo.
echo Por favor, instale Python de:
echo https://www.python.org/downloads/
echo.
echo Ou verifique se Python esta no PATH do Windows
pause
exit /b 1

:fim
echo.
echo ======================================
echo   INSTALACAO CONCLUIDA!
echo ======================================
echo.
echo Agora voce pode executar:
echo   - diagnostico_bot.py
echo   - reativar_bot.py
echo   - main.py
echo.
pause
