@echo off
setlocal
cd /d "%~dp0"

echo [1/4] Installing Python dependencies...
python -m pip install --upgrade pip
python -m pip install pyinstaller requests mutagen vkpymusic python-vlc
if errorlevel 1 goto :fail

echo [2/4] Checking bundled resources...
if not exist "ffmpeg\ffmpeg.exe" (
  echo ERROR: ffmpeg\ffmpeg.exe not found.
  echo Put ffmpeg.exe into the ffmpeg folder next to this script.
  goto :fail
)
if not exist "vlc_bundle\libvlc.dll" echo WARNING: vlc_bundle\libvlc.dll not found - installed VLC will be used.

echo [3/4] Building EXE with PyInstaller...
set EXTRA=
if exist "vlc_bundle\libvlc.dll" set EXTRA=%EXTRA% --add-data "vlc_bundle;vlc_bundle"

pyinstaller --noconfirm --windowed --onedir --name VKMusicDownloader --icon AppIcon.ico ^
  --collect-all vkpymusic ^
  --add-data "ffmpeg;ffmpeg" %EXTRA% vk_music_win.py
if errorlevel 1 goto :fail

echo [4/4] Done: dist\VKMusicDownloader\VKMusicDownloader.exe
goto :eof

:fail
echo BUILD FAILED
exit /b 1
