#!/bin/bash

# Переходим в папку, где лежит этот скрипт
cd "$(dirname "$0")"

# Проверяем, есть ли виртуальное окружение
if [ ! -d "venv" ]; then
    echo "📦 Создаю виртуальное окружение на Python 3.12…"
    /opt/local/bin/python3.12 -m venv venv
fi

# Активируем окружение
source venv/bin/activate

# Устанавливаем зависимости, если их нет
if ! python -c "import webview" 2>/dev/null; then
    echo "📦 Устанавливаю зависимости (pywebview, requests, mutagen, vkpymusic)…"
    pip install --upgrade pip --quiet
    pip install pywebview requests mutagen vkpymusic --quiet
fi

# Запускаем программу
echo "🚀 Запуск VK Music Downloader…"
python vk_music_mac.py

# Чтобы окно терминала не закрывалось при ошибке
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Программа завершилась с ошибкой. Нажмите Enter, чтобы закрыть…"
    read
else
    echo ""
    echo "✅ Программа завершена. Нажмите Enter, чтобы закрыть…"
    read
fi
