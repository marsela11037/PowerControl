"""
Скрипт для компиляции Power Control в один EXE файл
Запуск: python build_exe.py
"""

import os
import sys
import subprocess
from pathlib import Path

def check_pyinstaller():
    """Проверить установлен ли PyInstaller"""
    try:
        import PyInstaller
        return True
    except ImportError:
        return False

def install_pyinstaller():
    """Установить PyInstaller"""
    print("📦 Установка PyInstaller...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
    print("✅ PyInstaller установлен")

def build_exe():
    """Собрать EXE файл"""
    print("=" * 60)
    print("🔨 Компиляция Power Control в EXE")
    print("=" * 60)
    print()
    
    # Проверяем PyInstaller
    if not check_pyinstaller():
        print("⚠️  PyInstaller не найден")
        install_pyinstaller()
    
    # Создаем иконку если её нет
    icon_path = Path("app_icon.ico")
    if not icon_path.exists():
        print("🎨 Создание иконки...")
        try:
            subprocess.run([sys.executable, "create_icon.py"], check=True)
            print("✅ Иконка создана")
        except Exception as e:
            print(f"⚠️  Не удалось создать иконку: {e}")
            print("   Компиляция продолжится без иконки")
    
    # Параметры сборки
    script_name = "power_app.py"
    exe_name = "PowerControl"
    
    # Создаем команду PyInstaller
    cmd = [
        "pyinstaller",
        "--onefile",                    # Один файл
        "--windowed",                   # Без консоли
        "--name", exe_name,             # Имя EXE
        "--add-data", "templates;templates",  # Добавляем templates
        "--add-data", "static;static",        # Добавляем static
        "--hidden-import", "pystray._win32",
        "--hidden-import", "PIL._tkinter_finder",
        "--clean",                      # Очистить кэш
        script_name
    ]
    
    # Добавляем иконку если она есть
    if icon_path.exists():
        cmd.insert(6, "--icon")
        cmd.insert(7, str(icon_path))
    
    print("🔧 Запуск PyInstaller...")
    print(f"   Команда: {' '.join(cmd)}")
    print()
    
    try:
        subprocess.run(cmd, check=True)
        
        print()
        print("=" * 60)
        print("✅ Компиляция завершена успешно!")
        print("=" * 60)
        print()
        print(f"📁 EXE файл находится в папке: dist/{exe_name}.exe")
        print()
        print("💡 Для запуска:")
        print(f"   1. Скопируйте dist/{exe_name}.exe куда угодно")
        print(f"   2. Дважды кликните на {exe_name}.exe")
        print(f"   3. Сервер запустится автоматически!")
        print()
        
    except subprocess.CalledProcessError as e:
        print()
        print("❌ Ошибка компиляции!")
        print(f"   {e}")
        return False
    
    return True

if __name__ == "__main__":
    build_exe()
