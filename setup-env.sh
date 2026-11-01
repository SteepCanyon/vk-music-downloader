#!/bin/bash
set -e
cd "$(dirname "$0")"

PYBIN=/opt/local/bin/python3.12
if [ ! -x "$PYBIN" ]; then
    echo "✗ $PYBIN не найден. Поставь: sudo port install python312 py312-tkinter"
    exit 1
fi

if [ ! -d venv ]; then
    echo "→ Создаю venv…"
    "$PYBIN" -m venv venv
else
    echo "→ venv уже есть"
fi

source venv/bin/activate

echo "→ Обновляю pip…"
python -m pip install --upgrade pip --quiet

echo "→ Ставлю зависимости…"
pip install --quiet vkpymusic mutagen requests

echo "→ Проверка импортов…"
python - <<'PY'
import sys
try:
    import vkpymusic, mutagen, requests, tkinter
    print("  vkpymusic:", getattr(vkpymusic, "__version__", "?"))
    print("  mutagen:", mutagen.version_string)
    print("  requests:", requests.__version__)
    print("  tkinter:", tkinter.TkVersion)
    print("✓ зависимости OK")
except Exception as e:
    print("✗ Ошибка импорта:", e)
    sys.exit(1)
PY

echo "→ Проверка ffmpeg…"
if command -v /opt/local/bin/ffmpeg >/dev/null; then
    /opt/local/bin/ffmpeg -version | head -1
else
    echo "⚠ ffmpeg не найден в /opt/local/bin — установи: sudo port install ffmpeg"
fi

echo "Готово."
