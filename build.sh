#!/bin/bash
set -e

cd ~/Desktop/vk_music_mac

echo "1. Применяю патч с кнопкой обновления..."
python3 -c "
import re
with open('vk_music_mac.py', 'r', encoding='utf-8') as f: code = f.read()
if 'APP_VERSION' not in code:
    code = code.replace('DEFAULT_DIR = os.path.join(os.path.expanduser(\"~\"), \"Music\", \"VK\")', 'DEFAULT_DIR = os.path.join(os.path.expanduser(\"~\"), \"Music\", \"VK\")\nAPP_VERSION = \"3.0.0\"\nGITHUB_REPO = \"SteepCanyon/vk-music-downloader\"')
if 'Проверить обновления' not in code:
    old_info = '        ttk.Label(info_frame, text=f\"VLC: {vlc_status}\").pack(anchor=\"w\")'
    new_info = '        ttk.Label(info_frame, text=f\"VLC: {vlc_status}\").pack(anchor=\"w\")\n        ttk.Button(info_frame, text=\"🔄 Проверить обновления\", command=self._check_updates).pack(anchor=\"w\", pady=(10, 0))'
    code = code.replace(old_info, new_info)
if '_check_updates' not in code:
    old_settings = '    def _save_settings(self):'
    new_settings = '''    def _check_updates(self):
        import threading
        def worker():
            try:
                url = f\"https://api.github.com/repos/{GITHUB_REPO}/releases/latest\"
                r = requests.get(url, timeout=10)
                if r.status_code != 200:
                    messagebox.showerror(\"Обновление\", f\"Не удалось проверить обновления (HTTP {r.status_code})\")
                    return
                data = r.json()
                latest = data.get(\"tag_name\", \"\").lstrip(\"v\")
                if latest and latest != APP_VERSION:
                    msg = f\"Доступна новая версия: {latest}\\\\nТекущая версия: {APP_VERSION}\\\\n\\\\nПерейти на страницу загрузки?\"
                    if messagebox.askyesno(\"Обновление\", msg):
                        import webbrowser
                        webbrowser.open(data.get(\"html_url\", \"\"))
                else:
                    messagebox.showinfo(\"Обновление\", f\"У вас последняя версия: {APP_VERSION}\")
            except Exception as e:
                messagebox.showerror(\"Обновление\", f\"Ошибка: {e}\")
        threading.Thread(target=worker, daemon=True).start()

    def _save_settings(self):'''
    code = code.replace(old_settings, new_settings)
with open('vk_music_mac.py', 'w', encoding='utf-8') as f: f.write(code)
"

echo "2. Собираю .app..."
rm -rf build dist
venv/bin/pyinstaller --windowed --noconfirm \
  --name "VKMusicDownloader" \
  --icon AppIcon.icns \
  --osx-bundle-identifier "com.suncho.vkmusic" \
  --collect-all vkpymusic \
  --add-data "vlc_bundle:vlc_bundle" \
  --add-data "ffmpeg:ffmpeg" \
  vk_music_mac.py

echo "3. Подписываю..."
codesign --force --deep --sign - "dist/VKMusicDownloader.app"

echo "4. Создаю .dmg..."
rm -f "dist/VKMusicDownloader.dmg"
hdiutil create -volname "VK Music Downloader" \
  -srcfolder "dist/VKMusicDownloader.app" \
  -ov -format UDZO \
  "dist/VKMusicDownloader.dmg"

echo "✅ Готово! Файлы в папке dist."
