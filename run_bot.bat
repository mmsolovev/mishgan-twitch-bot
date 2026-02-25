@echo off
REM =============================
REM Twitch Bot Launcher (Windows)
REM =============================

REM получаем директорию батника
SET BASE_DIR=%~dp0

REM активируем виртуальное окружение
call "%BASE_DIR%venv\Scripts\activate.bat"

REM запуск бота
python "%BASE_DIR%bot.py"

REM оставляем окно открытым для логов
pause
