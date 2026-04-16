@echo off
chcp 65001 >nul
title Компиляция Power Control

echo ========================================
echo Компиляция Power Control в EXE
echo ========================================
echo.

REM Проверка Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python не найден!
    pause
    exit /b 1
)

REM Создание иконки если её нет
if not exist "app_icon.ico" (
    echo 🎨 Создание иконки...
    python create_icon.py
    echo.
)

REM Установка PyInstaller если нужно
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo 📦 Установка PyInstaller...
    pip install pyinstaller
)

echo.
echo 🔨 Компиляция...
echo.

pyinstaller --onefile --windowed --name PowerControl --icon=app_icon.ico --add-data "templates;templates" --add-data "static;static" --clean power_app.py

if errorlevel 1 (
    echo.
    echo ❌ Ошибка компиляции!
    pause
    exit /b 1
)

echo.
echo ========================================
echo ✅ Готово!
echo ========================================
echo.
echo 📁 EXE файл: dist\PowerControl.exe
echo.
echo 💡 Скопируйте PowerControl.exe куда угодно
echo    и запускайте двойным кликом!
echo.
pause
