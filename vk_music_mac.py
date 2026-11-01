# -*- coding: utf-8 -*-
"""
VK Music Downloader — macOS версия (4 вкладки: Настройки, Вход, Загрузчик, Плеер).
"""

import os
import re
import sys
import json
import time
import queue
import shutil
import subprocess
import threading
import logging
import webbrowser
from datetime import datetime
import tkinter as tk
import ttkbootstrap as ttk
from tkinter import messagebox, filedialog, scrolledtext

# ============================================================================
# ПОДГОТОВКА ПУТЕЙ К БИБЛИОТЕКАМ (VLC и FFmpeg)
# ============================================================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_VLC_DIR = os.path.join(APP_DIR, "vlc_bundle")
LOCAL_FFMPEG = os.path.join(APP_DIR, "ffmpeg", "ffmpeg")

# Настраиваем VLC до его импорта
if os.path.exists(os.path.join(LOCAL_VLC_DIR, "lib", "libvlc.dylib")):
    os.environ["DYLD_LIBRARY_PATH"] = os.path.join(LOCAL_VLC_DIR, "lib")
    os.environ["VLC_PLUGIN_PATH"] = os.path.join(LOCAL_VLC_DIR, "plugins")
else:
    for _vlc_candidate in ("/Applications/VLC.app/Contents/MacOS", "/Applications/MacPorts/VLC.app/Contents/MacOS"):
        if os.path.exists(f"{_vlc_candidate}/lib/libvlc.dylib"):
            os.environ["DYLD_LIBRARY_PATH"] = f"{_vlc_candidate}/lib"
            os.environ.setdefault("VLC_PLUGIN_PATH", f"{_vlc_candidate}/plugins")
            break

import requests
import vlc

try:
    from vkpymusic import Service
except ImportError:
    print("Нужен vkpymusic: pip install vkpymusic")
    sys.exit(1)

try:
    from mutagen.mp3 import MP3
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

# ============================================================================
# ЛОГИРОВАНИЕ
# ============================================================================
CONFIG = os.path.join(os.path.expanduser("~"), ".vk_music_downloader.json")
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Music", "VK")
LOG_DIR = os.path.join(APP_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"vk_music_{datetime.now():%Y%m%d_%H%M%S}.txt")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(filename)s(%(lineno)d) | %(funcName)s(...) | [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)]
)
_log = logging.getLogger("vkd")

# ============================================================================
# КОНСТАНТЫ
# ============================================================================
UA = "KateMobileAndroid/56.1 lite-487 (Android 10; SDK 29; arm64-v8a; samsung SM-G975F; ru; 1440x3040)"
API_HEADERS = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"}
API_BASE = "https://api.vk.com/method/"
API_VERSION = "5.131"

MIN_FILE_SIZE = 100_000
MAX_DOWNLOAD_RETRIES = 3
DELAY_BETWEEN_TRACKS = 0.4
LIVE_UPDATE_INTERVAL = 3
STALL_TIMEOUT = 60
WATCHDOG_INTERVAL = 3

def load_cfg():
    try:
        with open(CONFIG, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_cfg(c):
    try:
        with open(CONFIG, "w", encoding="utf-8") as f: json.dump(c, f, ensure_ascii=False, indent=2)
    except Exception: pass

def enable_clipboard(widget):
    is_text = isinstance(widget, (tk.Text, scrolledtext.ScrolledText))
    def _paste(e=None):
        try: data = widget.clipboard_get()
        except Exception: return "break"
        try:
            if is_text:
                try:
                    if widget.tag_ranges("sel"): widget.delete("sel.first", "sel.last")
                except Exception: pass
                widget.insert(tk.INSERT, data); widget.see(tk.INSERT)
            else:
                try: widget.delete("sel.first", "sel.last")
                except Exception: pass
                widget.insert(tk.INSERT, data)
        except Exception: pass
        return "break"
    def _copy(e=None):
        try:
            t = widget.selection_get()
            widget.clipboard_clear(); widget.clipboard_append(t)
        except Exception: pass
        return "break"
    def _cut(e=None):
        try:
            t = widget.selection_get()
            widget.clipboard_clear(); widget.clipboard_append(t)
            widget.delete("sel.first", "sel.last")
        except Exception: pass
        return "break"
    def _sel_all(e=None):
        try:
            if is_text:
                widget.tag_add("sel", "1.0", "end-1c"); widget.mark_set(tk.INSERT, "1.0"); widget.see(tk.INSERT)
            else:
                widget.select_range(0, "end"); widget.icursor("end")
        except Exception: pass
        return "break"
    for seq in ("<Command-v>", "<Command-V>", "<Control-v>", "<Control-V>"):
        try: widget.bind(seq, _paste)
        except Exception: pass
    for seq in ("<Command-c>", "<Command-C>", "<Control-c>", "<Control-C>"):
        try: widget.bind(seq, _copy)
        except Exception: pass
    for seq in ("<Command-x>", "<Command-X>", "<Control-x>", "<Control-X>"):
        try: widget.bind(seq, _cut)
        except Exception: pass
    for seq in ("<Command-a>", "<Command-A>", "<Control-a>", "<Control-A>"):
        try: widget.bind(seq, _sel_all)
        except Exception: pass
    menu = tk.Menu(widget, tearoff=0)
    menu.add_command(label="Вырезать", command=_cut)
    menu.add_command(label="Копировать", command=_copy)
    menu.add_command(label="Вставить", command=_paste)
    menu.add_separator()
    menu.add_command(label="Выделить всё", command=_sel_all)
    def _popup(e):
        try: menu.tk_popup(e.x_root, e.y_root)
        finally: menu.grab_release()
        return "break"
    widget.bind("<Button-2>", _popup); widget.bind("<Button-3>", _popup)

def resolve_music_dir(base_dir):
    if not base_dir or not os.path.isdir(base_dir): return base_dir
    try: has_base_mp3 = any(f.lower().endswith(".mp3") for f in os.listdir(base_dir))
    except OSError: has_base_mp3 = False
    sub = os.path.join(base_dir, "Music")
    has_sub_mp3 = False
    if os.path.isdir(sub):
        try: has_sub_mp3 = any(f.lower().endswith(".mp3") for f in os.listdir(sub))
        except OSError: pass
    if has_base_mp3 and not has_sub_mp3: return base_dir
    if has_sub_mp3: return sub
    return base_dir

def safe_filename(title, artist):
    s = f"{title} - {artist}"
    s = re.sub(r'[\\/:*?"<>|~]+', " ", s)
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s or "track")[:150] + ".mp3"

def find_existing_file(music_dir, title, artist, known_files=None):
    if not music_dir or not os.path.isdir(music_dir): return None
    title = (title or "").strip(); artist = (artist or "").strip()
    if not title: return None
    if known_files is not None: mp3s = list(known_files)
    else:
        try: files = os.listdir(music_dir)
        except OSError: return None
        mp3s = [f for f in files if f.lower().endswith(".mp3")]
    if not mp3s: return None
    candidates = set()
    if title and artist:
        for t, a in ((title, artist), (artist, title)):
            for sep in (" - ", " – ", " — ", "_", "-"):
                c = f"{t}{sep}{a}.mp3".lower(); candidates.add(c); candidates.add(c.replace(" ", ""))
    candidates.add(f"{title}.mp3".lower())
    for f in mp3s:
        if f.lower() in candidates:
            p = os.path.join(music_dir, f)
            try:
                if os.path.getsize(p) >= MIN_FILE_SIZE: return p
            except OSError: continue
    return None

def read_mp3_info(path, ffmpeg_bitrate=None):
    try: size = os.path.getsize(path)
    except OSError: size = 0
    bitrate, duration = None, None
    if HAS_MUTAGEN:
        try:
            mp3 = MP3(path); bitrate = int((mp3.info.bitrate or 0) / 1000); duration = int(mp3.info.length or 0)
        except Exception: pass
    if not bitrate and ffmpeg_bitrate: bitrate = ffmpeg_bitrate
    return bitrate, size, duration

def extract_ids(song):
    owner_id = getattr(song, "owner_id", None); audio_id = getattr(song, "track_id", None)
    if audio_id is None:
        for n in ("id", "audio_id", "audioId", "aid"):
            v = getattr(song, n, None)
            if v is not None: audio_id = v; break
    if owner_id is None:
        for n in ("ownerId", "oid"):
            v = getattr(song, n, None)
            if v is not None: owner_id = v; break
    try:
        if owner_id is not None: owner_id = int(owner_id)
        if audio_id is not None: audio_id = int(audio_id)
    except (TypeError, ValueError): pass
    return owner_id, audio_id

def fetch_audio_url(owner_id, audio_id, token, user_agent=UA):
    params = {"access_token": token, "v": API_VERSION, "lang": "ru", "audios": f"{owner_id}_{audio_id}"}
    headers = {"User-Agent": user_agent}
    _log.info(f"Запрос аудио: {owner_id}_{audio_id}")
    r = requests.post(API_BASE + "audio.getById", data=params, headers=headers, timeout=30)
    r.raise_for_status(); j = r.json()
    if "error" in j: raise RuntimeError(f"VK API [{j['error'].get('error_code')}]: {j['error'].get('error_msg')}")
    response = j.get("response")
    if not response: raise RuntimeError("Пустой ответ VK")
    track = response[0] if isinstance(response, list) else response
    url = track.get("url")
    if not url: raise RuntimeError("В ответе нет URL")
    return url, track

def parse_ffmpeg_bitrate(stderr_text):
    m = re.search(r'Audio:\s+\w+[^,]*,.*?(\d+)\s+kb/s', stderr_text)
    if m: return int(m.group(1))
    m = re.search(r'(\d+)\s+kb/s', stderr_text)
    if m: return int(m.group(1))
    return None

def download_hls_to_mp3(url, out_path, cancel_getter, on_progress=None, user_agent=UA):
    ffmpeg = LOCAL_FFMPEG if os.path.exists(LOCAL_FFMPEG) else shutil.which("ffmpeg")
    if not ffmpeg:
        for p in ("/opt/local/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"):
            if os.path.exists(p): ffmpeg = p; break
    if not ffmpeg: raise RuntimeError("ffmpeg не найден.")

    part_path = out_path + ".part"
    for attempt in range(1, MAX_DOWNLOAD_RETRIES + 1):
        if cancel_getter(): raise RuntimeError("Отменено пользователем")
        for p in (part_path, out_path):
            if os.path.exists(p):
                try: os.remove(p)
                except OSError: pass

        use_copy = (attempt == 1)
        if use_copy:
            cmd = [ffmpeg, "-y", "-loglevel", "info", "-user_agent", user_agent, "-i", url, "-c", "copy", "-f", "mp3", part_path]
        else:
            cmd = [ffmpeg, "-y", "-loglevel", "info", "-user_agent", user_agent, "-i", url, "-vn", "-c:a", "libmp3lame", "-b:a", "320k", "-f", "mp3", part_path]
            
        _log.info(f"ffmpeg попытка {attempt}/{MAX_DOWNLOAD_RETRIES} (copy={use_copy})")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stderr_lines = []; parsed_bitrate = [None]
        def read_stderr():
            try:
                for line in iter(p.stderr.readline, b""):
                    s = line.decode(errors="replace"); stderr_lines.append(s)
                    if parsed_bitrate[0] is None:
                        m = re.search(r'Audio:\s+\w+[^,]*,.*?(\d+)\s+kb/s', s)
                        if m: parsed_bitrate[0] = int(m.group(1))
            except Exception: pass

        t = threading.Thread(target=read_stderr, daemon=True); t.start()

        last_size, last_progress_ts, last_live_ts = 0, time.time(), 0
        cancelled = False

        while True:
            time.sleep(WATCHDOG_INTERVAL)
            if cancel_getter():
                cancelled = True; p.terminate()
                try: p.wait(timeout=3)
                except Exception: p.kill()
                try:
                    if os.path.exists(part_path): os.remove(part_path)
                except OSError: pass
                break
            if p.poll() is not None: break
            cur_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
            now = time.time()
            if on_progress and (now - last_live_ts) >= LIVE_UPDATE_INTERVAL:
                on_progress(cur_size, parsed_bitrate[0]); last_live_ts = now
            if cur_size > last_size:
                last_size, last_progress_ts = cur_size, now
            else:
                if now - last_progress_ts > STALL_TIMEOUT:
                    p.terminate()
                    try: p.wait(timeout=3)
                    except Exception: p.kill()
                    try:
                        if os.path.exists(part_path): os.remove(part_path)
                    except OSError: pass
                    break

        t.join(timeout=3)
        try:
            if p.stderr: p.stderr.close()
        except Exception: pass
        stderr_text = "".join(stderr_lines)
        if on_progress:
            final_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
            on_progress(final_size, parsed_bitrate[0])

        if cancelled: raise RuntimeError("Отменено пользователем")
        if p.returncode == 0 and os.path.exists(part_path) and os.path.getsize(part_path) > 1024:
            try: os.replace(part_path, out_path)
            except OSError as e: raise RuntimeError(f"Ошибка переименования: {e}")
            return parsed_bitrate[0] or parse_ffmpeg_bitrate(stderr_text)

        try:
            if os.path.exists(part_path): os.remove(part_path)
        except OSError: pass
        if attempt == MAX_DOWNLOAD_RETRIES:
            raise RuntimeError(f"ffmpeg не справился: {stderr_text[-400:]}")
    raise RuntimeError("Не удалось скачать")


class App:
    def __init__(self, root):
        self.root = root
        self.style = ttk.Style(theme="darkly")
        root.title("VK Music Downloader (macOS)")
        root.geometry("1150x750")
        root.minsize(1000, 600)

        self.cfg = load_cfg()
        self.service = None
        self.user_id = 0
        self.token = ""
        self.tracks = []
        self.selected = set()
        self.downloaded = {}
        self.q = queue.Queue()
        self.busy = False
        self.cancel_flag = False
        self.force = False
        self._fname_to_iid = {}
        self.user_agent = self.cfg.get("user_agent", UA)
        self._twofa_event = threading.Event()
        self._twofa_code = None
        
        # VLC для плеера (--no-video чтобы не открывал левые окна)
        self.vlc_instance = vlc.Instance("--no-video --quiet")
        self.vlc_player = self.vlc_instance.media_player_new()
        
        # Состояние плеера
        self.current_playlist = []
        self.current_track_index = -1
        self.is_playing = False
        self._seek_dragging = False

        self._create_ui()
        self._restore()
        root.after(80, self._pump)
        root.after(500, self._update_player_progress)

    def _create_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tab_settings = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_settings, text="⚙️ Настройки")
        self._build_settings_tab()

        self.tab_login = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_login, text="🔑 Вход")
        self._build_login_tab()

        self.tab_downloader = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_downloader, text="📥 Загрузчик")
        self._build_downloader_tab()

        self.tab_player = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_player, text="🎵 Плеер")
        self._build_player_tab()

        self.var_status = tk.StringVar(value="Готов")
        ttk.Label(self.root, textvariable=self.var_status, anchor="w", relief=tk.SUNKEN, padding=(6, 3)).pack(fill=tk.X, side=tk.BOTTOM)

    def _build_settings_tab(self):
        frame = ttk.LabelFrame(self.tab_settings, text="Общие настройки", padding=10)
        frame.pack(fill=tk.X, pady=5)

        ttk.Label(frame, text="Папка для загрузки:").grid(row=0, column=0, sticky="w", pady=5)
        self.var_dir = tk.StringVar(value=DEFAULT_DIR)
        ed = ttk.Entry(frame, textvariable=self.var_dir, width=50)
        ed.grid(row=0, column=1, padx=5, pady=5)
        enable_clipboard(ed)
        ttk.Button(frame, text="…", command=self._pick_dir, bootstyle="outline-secondary").grid(row=0, column=2, padx=5)

        ttk.Label(frame, text="Тема оформления:").grid(row=1, column=0, sticky="w", pady=5)
        self.var_theme = tk.StringVar(value="darkly")
        themes = ["darkly", "cyborg", "solar", "superhero", "vapor"]
        combo = ttk.Combobox(frame, textvariable=self.var_theme, values=themes, state="readonly")
        combo.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        ttk.Button(frame, text="Применить", command=self._apply_theme, bootstyle="outline-primary").grid(row=1, column=2, padx=5)

        ttk.Button(self.tab_settings, text="Сохранить настройки", command=self._save_settings, bootstyle="success").pack(pady=10)

        # --- Блок "О программе" ---
        info_frame = ttk.LabelFrame(self.tab_settings, text="О программе", padding=10)
        info_frame.pack(fill=tk.X, pady=10)
        
        vlc_status = "Локальный" if os.path.exists(os.path.join(LOCAL_VLC_DIR, "lib", "libvlc.dylib")) else "Системный"
        
        ttk.Label(info_frame, text="VK Music Downloader v2.5", font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 5))
        ttk.Label(info_frame, text="Автор: Suncho").pack(anchor="w")
        link = ttk.Label(info_frame, text="github.com/SteepCanyon", foreground="#00aaff", cursor="hand2")
        link.pack(anchor="w", pady=(0, 10))
        link.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/SteepCanyon"))
        
        ttk.Label(info_frame, text=f"VLC: {vlc_status}").pack(anchor="w")
        
        ttk.Button(info_frame, text="🔄 Проверить обновления", command=self._check_updates, bootstyle="info").pack(anchor="w", pady=(10, 0))

    def _check_updates(self):
        messagebox.showinfo("Обновления", "Проверка обновлений с GitHub пока в разработке.\nСледите за репозиторием: github.com/SteepCanyon")

    def _build_login_tab(self):
        frame = ttk.LabelFrame(self.tab_login, text="Авторизация", padding=10)
        frame.pack(fill=tk.X, pady=5)

        ttk.Label(frame, text="Токен (если есть готовый):").grid(row=0, column=0, sticky="nw")
        tf = ttk.Frame(frame)
        tf.grid(row=0, column=1, columnspan=2, sticky="we", padx=4)
        self.txt_token = tk.Text(tf, height=2, wrap="char", font=("Consolas", 9), undo=True, relief=tk.FLAT)
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.txt_token.yview)
        self.txt_token.configure(yscrollcommand=sb.set)
        self.txt_token.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        enable_clipboard(self.txt_token)

        login_frame = ttk.LabelFrame(self.tab_login, text="Вход по логину/паролю (получение токена)", padding=10)
        login_frame.pack(fill=tk.X, pady=10)

        ttk.Label(login_frame, text="Логин (телефон/email):").pack(side=tk.LEFT)
        self.var_login = tk.StringVar()
        e_login = ttk.Entry(login_frame, textvariable=self.var_login, width=22)
        e_login.pack(side=tk.LEFT, padx=4)
        enable_clipboard(e_login)

        ttk.Label(login_frame, text="Пароль:").pack(side=tk.LEFT, padx=(10, 0))
        self.var_password = tk.StringVar()
        e_pass = ttk.Entry(login_frame, textvariable=self.var_password, width=22, show="*")
        e_pass.pack(side=tk.LEFT, padx=4)
        enable_clipboard(e_pass)

        ttk.Button(login_frame, text="Войти и получить токен", command=self.login_vk, bootstyle="primary").pack(side=tk.LEFT, padx=10)

        self.list_progress = ttk.Progressbar(self.tab_login, mode="determinate", bootstyle="success-striped")
        self.list_progress.pack(fill=tk.X, pady=10)

    def _build_downloader_tab(self):
        # Верхняя панель: User ID и загрузка плейлиста
        top_bar = ttk.Frame(self.tab_downloader)
        top_bar.pack(fill=tk.X, pady=5)
        
        ttk.Label(top_bar, text="User ID:").pack(side=tk.LEFT)
        self.var_uid = tk.StringVar()
        e_uid = ttk.Entry(top_bar, textvariable=self.var_uid, width=15)
        e_uid.pack(side=tk.LEFT, padx=5)
        enable_clipboard(e_uid)
        ttk.Button(top_bar, text="⬇ Загрузить мой плейлист", command=self.load_playlist, bootstyle="success").pack(side=tk.LEFT, padx=5)


        # Панель поиска
        search_bar = ttk.Frame(self.tab_downloader)
        search_bar.pack(fill=tk.X, pady=5)
        ttk.Label(search_bar, text="🔍 Поиск в ВК:").pack(side=tk.LEFT)
        self.var_query = tk.StringVar()
        self.ent_query = ttk.Entry(search_bar, textvariable=self.var_query, width=40)
        self.ent_query.pack(side=tk.LEFT, padx=5)
        self.ent_query.bind("<Return>", lambda e: self.search_vk())
        enable_clipboard(self.ent_query)
        ttk.Button(search_bar, text="Найти", command=self.search_vk, bootstyle="info").pack(side=tk.LEFT)

        # Фильтры
        filt = ttk.Frame(self.tab_downloader)

        filt.pack(fill=tk.X, pady=5)
        self.var_hide_dl = tk.BooleanVar(value=False)
        ttk.Checkbutton(filt, text="Скрыть скачанные", variable=self.var_hide_dl, command=self._refresh_tree, bootstyle="round-toggle").pack(side=tk.LEFT)
        self.var_force = tk.BooleanVar(value=False)
        ttk.Checkbutton(filt, text="Перекачивать", variable=self.var_force, bootstyle="round-toggle").pack(side=tk.LEFT, padx=(16, 0))
        ttk.Button(filt, text="🔄 Обновить статус", command=self._rescan, bootstyle="outline-info").pack(side=tk.LEFT, padx=(16, 0))
        ttk.Label(filt, text="Фильтр:").pack(side=tk.LEFT, padx=(16, 4))
        self.var_search = tk.StringVar()
        self.var_search.trace_add("write", lambda *_: self._refresh_tree())
        es = ttk.Entry(filt, textvariable=self.var_search, width=25)
        es.pack(side=tk.LEFT)
        enable_clipboard(es)
        self.lbl_stats = ttk.Label(filt, text="")
        self.lbl_stats.pack(side=tk.RIGHT)

        # Таблица
        mid = ttk.LabelFrame(self.tab_downloader, text="Треки", padding=4)
        mid.pack(fill=tk.BOTH, expand=True, pady=5)
        cols = ("sel", "dl", "num", "artist", "title", "duration", "bitrate", "size")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c, h, w, a in (
            ("sel", "✓", 40, "center"), ("dl", "Скачан", 70, "center"), ("num", "#", 50, "e"),
            ("artist", "Исполнитель", 240, "w"), ("title", "Название", 350, "w"),
            ("duration", "Длит.", 70, "e"), ("bitrate", "Битрейт", 90, "e"), ("size", "Размер", 100, "e"),
        ):
            self.tree.heading(c, text=h); self.tree.column(c, width=w, anchor=a)
        sb2 = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb2.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", lambda e: self._toggle_sel())
        self.tree.tag_configure("downloading", background="#2a4a6a")

        # Кнопки управления
        bot = ttk.Frame(self.tab_downloader)
        bot.pack(fill=tk.X, pady=5)
        ttk.Button(bot, text="Выбрать всё", command=self.select_all, bootstyle="outline-secondary").pack(side=tk.LEFT, padx=2)
        ttk.Button(bot, text="Снять выбор", command=self.deselect_all, bootstyle="outline-secondary").pack(side=tk.LEFT, padx=2)
        self.btn_dl_sel = ttk.Button(bot, text="⬇ Скачать выбранные", command=lambda: self.start_dl(True), bootstyle="success")
        self.btn_dl_sel.pack(side=tk.LEFT, padx=(16, 2))
        self.btn_dl_all = ttk.Button(bot, text="⬇⬇ Скачать ВСЁ", command=lambda: self.start_dl(False), bootstyle="success")
        self.btn_dl_all.pack(side=tk.LEFT, padx=2)
        self.btn_cancel = ttk.Button(bot, text="✖ Отмена", command=self.cancel_dl, state="disabled", bootstyle="danger")
        self.btn_cancel.pack(side=tk.LEFT, padx=2)
        self.progress = ttk.Progressbar(bot, mode="determinate", bootstyle="success-striped")
        self.progress.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=8)

    def _build_player_tab(self):
        # Верхняя часть: Информация о треке
        info_frame = ttk.Frame(self.tab_player)
        info_frame.pack(fill=tk.X, pady=10)
        
        self.lbl_now_playing = ttk.Label(info_frame, text="Ничего не играет", font=("Helvetica", 16, "bold"), anchor="center")
        self.lbl_now_playing.pack(fill=tk.X)

        # Ползунок перемотки
        self.player_scale = ttk.Scale(self.tab_player, from_=0, to=100, orient=tk.HORIZONTAL, command=self._on_seek)
        self.player_scale.pack(fill=tk.X, pady=10)
        self.player_scale.bind("<ButtonPress-1>", lambda e: setattr(self, "_seek_dragging", True))
        self.player_scale.bind("<ButtonRelease-1>", lambda e: setattr(self, "_seek_dragging", False))

        # Панель управления плеером
        ctrl = ttk.Frame(self.tab_player)
        ctrl.pack(fill=tk.X, pady=5)
        
        ttk.Button(ctrl, text="⏮", command=self._player_prev, bootstyle="outline-secondary", width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="▶", command=self._player_play, bootstyle="success", width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="⏸", command=self._player_pause, bootstyle="warning", width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="⏹", command=self._player_stop, bootstyle="danger", width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="⏭", command=self._player_next, bootstyle="outline-secondary", width=3).pack(side=tk.LEFT, padx=2)
        
        # Кнопка повтора
        self.var_loop = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctrl, text="🔁 Цикл", variable=self.var_loop, bootstyle="round-toggle").pack(side=tk.LEFT, padx=(20, 0))

        ttk.Label(ctrl, text="Громкость:").pack(side=tk.LEFT, padx=(20, 5))
        self.vol_scale = ttk.Scale(ctrl, from_=0, to=100, orient=tk.HORIZONTAL, length=150, command=self._on_volume_change)
        self.vol_scale.set(70)
        self.vol_scale.pack(side=tk.LEFT)

        # Список файлов для плеера
        self.player_listbox = tk.Listbox(self.tab_player, bg="#1a1a1a", fg="#e0e0e0", selectbackground="#00cc66", selectforeground="#000", height=8, relief=tk.FLAT)
        self.player_listbox.pack(fill=tk.BOTH, expand=True, pady=10)
        self.player_listbox.bind('<Double-1>', lambda e: self._player_play())
        
        ttk.Button(self.tab_player, text="🔄 Обновить список из папки", command=self._load_player_files, bootstyle="info").pack(pady=5)

    def _apply_theme(self):
        self.style.theme_use(self.var_theme.get())
        _log.info(f"Тема изменена на: {self.var_theme.get()}")

    def _save_settings(self):
        self.cfg["download_dir"] = self.var_dir.get()
        self.cfg["theme"] = self.var_theme.get()
        save_cfg(self.cfg)
        messagebox.showinfo("Настройки", "Сохранено.")

    def _pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.var_dir.get() or os.path.expanduser("~"))
        if d:
            self.var_dir.set(d); self._rescan()

    def _restore(self):
        if self.cfg.get("token"):
            self.txt_token.insert("1.0", self.cfg["token"]); self.token = self.cfg["token"]
        if self.cfg.get("user_agent"): self.user_agent = self.cfg["user_agent"]
        if self.cfg.get("user_id"): self.var_uid.set(str(self.cfg["user_id"]))
        if self.cfg.get("download_dir"): self.var_dir.set(self.cfg["download_dir"])
        if self.cfg.get("theme"): self.var_theme.set(self.cfg["theme"]); self._apply_theme()
        self.root.after(200, self._rescan)
        self.root.after(300, self._load_player_files)

    def login_vk(self):
        if self.busy: return
        login = self.var_login.get().strip(); password = self.var_password.get().strip()
        if not login or not password:
            messagebox.showwarning("Вход", "Введите логин и пароль"); return
        self.busy = True; self._set_busy(True)
        self.var_status.set("Авторизация в VK…")
        threading.Thread(target=self._login_worker, args=(login, password), daemon=True).start()

    def _ask_2fa(self):
        self._twofa_event.clear(); self._twofa_code = None
        def show_dialog():
            from tkinter import simpledialog
            code = simpledialog.askstring(
                "Двухфакторная аутентификация",
                "VK может прислать SMS или позвонить с короткого номера.\n"
                "Если поступил звонок, введите последние 4 цифры номера, с которого звонили.\n\n"
                "Введите код:", parent=self.root)
            self._twofa_code = code; self._twofa_event.set()
        self.root.after(0, show_dialog)
        self._twofa_event.wait(timeout=180)
        if not self._twofa_code: raise RuntimeError("Код 2FA не введён")
        return self._twofa_code.strip()

    def _login_worker(self, login, password):
        try:
            from vkpymusic import TokenReceiver
            try: receiver = TokenReceiver(login=login, password=password, twofa_handler=self._ask_2fa)
            except TypeError:
                try: receiver = TokenReceiver(login=login, password=password)
                except TypeError: receiver = TokenReceiver(login, password)
            try: ok = receiver.auth(on_2fa=self._ask_2fa)
            except TypeError:
                self.q.put(("status", "Если VK запросит код 2FA — он появится в консоли"))
                ok = receiver.auth()
            if ok:
                token = receiver.get_token()
                ua = getattr(getattr(receiver, "client", None), "user_agent", UA) or UA
                self.q.put(("token_received", (token, ua)))
            else: self.q.put(("error", "Не удалось авторизоваться. Проверьте логин/пароль."))
        except Exception as e: self.q.put(("error", f"Ошибка входа: {e}"))
        finally: self.q.put(("busy", (False,)))

    def _set_busy(self, b, allow_cancel=False):
        self.busy = b
        st = "disabled" if b else "normal"
        for x in (self.btn_dl_sel, self.btn_dl_all): x.configure(state=st)
        self.btn_cancel.configure(state="normal" if (b and allow_cancel) else "disabled")

    def cancel_dl(self):
        self.cancel_flag = True; self.var_status.set("Отмена…")

    def _pump(self):
        try:
            while True:
                k, p = self.q.get_nowait()
                if k == "status": self.var_status.set(p)
                elif k == "progress":
                    c, t = p; self.progress["maximum"] = max(t, 1); self.progress["value"] = c
                elif k == "list_progress":
                    c, t = p; self.list_progress["maximum"] = max(t, 1); self.list_progress["value"] = c
                elif k == "playlist":
                    self.tracks = p; self._refresh_tree(); self._rescan()
                elif k == "rescan":
                    for fname, info in p.items(): self.downloaded[fname] = info
                    self._refresh_tree(); self._load_player_files()
                elif k == "busy": self._set_busy(p[0], p[1] if len(p) > 1 else False)
                elif k == "live_update": self._live_update(*p)
                elif k == "token_received":
                    token, ua = p
                    self.token = token; self.user_agent = ua
                    self.txt_token.delete("1.0", "end"); self.txt_token.insert("1.0", token)
                    self.cfg["token"] = token; self.cfg["user_agent"] = ua
                    save_cfg(self.cfg)
                    self.var_status.set("Токен получен и сохранён")
                    messagebox.showinfo("Успех", "Токен получен. Теперь можно загрузить плейлист.")
                elif k == "downloaded_one":
                    fname, info = p; self.downloaded[fname] = info; self._update_row(fname, info)
                elif k == "done":
                    self._set_busy(False); self.progress["value"] = 0
                    self._load_player_files()
                    messagebox.showinfo("Готово", p)
                elif k == "error":
                    self._set_busy(False); self.progress["value"] = 0
                    messagebox.showerror("Ошибка", p)
        except queue.Empty: pass
        self.root.after(80, self._pump)

    def _live_update(self, fname, size, bitrate):
        iid = self._fname_to_iid.get(fname)
        if not iid or not self.tree.exists(iid): return
        self.tree.set(iid, "dl", "↓")
        self.tree.set(iid, "bitrate", f"{bitrate} kbps" if bitrate else "…")
        self.tree.set(iid, "size", self._fmt_size(size) + "…")
        self.tree.item(iid, tags=("downloading",))

    def _refresh_tree(self):
        for iid in self.tree.get_children(): self.tree.delete(iid)
        self.selected.clear(); self._fname_to_iid.clear()
        hide = self.var_hide_dl.get(); q = self.var_search.get().strip().lower()
        total, shown, dlc = len(self.tracks), 0, 0

        music_dir = resolve_music_dir(self.var_dir.get().strip())
        disk_files = None
        if music_dir and os.path.isdir(music_dir):
            try: disk_files = {f for f in os.listdir(music_dir) if f.lower().endswith(".mp3")}
            except OSError: disk_files = None

        for i, s in enumerate(self.tracks):
            artist = (getattr(s, "artist", "") or "").strip()
            title = (getattr(s, "title", "") or "").strip()
            fname = safe_filename(title, artist)
            info = self._find_dl(s, music_dir, disk_files)
            if info:
                dlc += 1
                if hide: continue
            if q and q not in f"{artist} {title}".lower(): continue
            dur = int(getattr(s, "duration", 0) or 0)
            mark = "✓" if info else "—"
            bitrate = f"{info.get('bitrate')} kbps" if info and info.get('bitrate') else "—"
            size = self._fmt_size(info.get("size", 0)) if info else "—"
            iid = f"t{i}"
            self.tree.insert("", "end", iid=iid, values=("☐", mark, i + 1, artist, title, f"{dur // 60}:{dur % 60:02d}", bitrate, size))
            self._fname_to_iid[fname] = iid
            shown += 1
        self.lbl_stats.configure(text=f"Показано: {shown} / всего: {total} / скачано: {dlc}")

    def _find_dl(self, song, music_dir=None, disk_files=None):
        a = (getattr(song, "artist", "") or "").strip(); t = (getattr(song, "title", "") or "").strip()
        fname = safe_filename(t, a)
        info = self.downloaded.get(fname)
        if info and info.get("size", 0) > 0: return info
        if music_dir is None: music_dir = resolve_music_dir(self.var_dir.get().strip())
        p = find_existing_file(music_dir, t, a, known_files=disk_files)
        if p:
            br, sz, dur = read_mp3_info(p)
            entry = {"bitrate": br, "size": sz, "duration": dur, "path": p}
            self.downloaded[fname] = entry
            return entry
        return None

    def _update_row(self, fname, info):
        iid = self._fname_to_iid.get(fname)
        if iid and self.tree.exists(iid):
            br, size = info.get("bitrate"), info.get("size", 0)
            self.tree.set(iid, "dl", "✓" if (size > 0 or br) else "—")
            self.tree.set(iid, "bitrate", f"{br} kbps" if br else "—")
            self.tree.set(iid, "size", self._fmt_size(size) if size else "—")
            self.tree.item(iid, tags=())

    @staticmethod
    def _fmt_size(n):
        n = int(n or 0)
        if n < 1024: return f"{n} Б"
        if n < 1024*1024: return f"{n/1024:.1f} КБ"
        if n < 1024**3: return f"{n/1024/1024:.2f} МБ"
        return f"{n/1024**3:.2f} ГБ"

    def _on_click(self, e):
        if self.tree.identify("region", e.x, e.y) == "cell" and self.tree.identify_column(e.x) == "#1":
            iid = self.tree.identify_row(e.y)
            if iid: self._toggle_iid(iid); return "break"

    def _toggle_iid(self, iid):
        if iid in self.selected:
            self.selected.discard(iid); self.tree.set(iid, "sel", "☐")
        else:
            self.selected.add(iid); self.tree.set(iid, "sel", "☑")

    def _toggle_sel(self):
        for iid in self.tree.selection(): self._toggle_iid(iid)

    def select_all(self):
        for iid in self.tree.get_children(): self.selected.add(iid); self.tree.set(iid, "sel", "☑")

    def deselect_all(self):
        for iid in list(self.selected):
            if self.tree.exists(iid): self.tree.set(iid, "sel", "☐")
        self.selected.clear()

    def _rescan(self):
        base = self.var_dir.get().strip()
        if base: threading.Thread(target=self._rescan_worker, args=(base,), daemon=True).start()

    def _rescan_worker(self, base):
        self.q.put(("status", "Сканирование папки…"))
        music = resolve_music_dir(base); out = {}
        if music and os.path.isdir(music):
            for f in os.listdir(music):
                if f.endswith(".mp3.part"):
                    try: os.remove(os.path.join(music, f))
                    except OSError: pass
                    continue
                if not f.lower().endswith(".mp3"): continue
                p = os.path.join(music, f)
                if not os.path.isfile(p) or os.path.getsize(p) < MIN_FILE_SIZE: continue
                br, sz, dur = read_mp3_info(p)
                out[f] = {"bitrate": br, "size": sz, "duration": dur, "path": p}
        self.q.put(("rescan", out))
        self.q.put(("status", f"Найдено скачанных: {len(out)}"))

    def load_playlist(self):
        if self.busy: return
        token = self.txt_token.get("1.0", "end").strip(); uid_s = self.var_uid.get().strip()
        if not token: messagebox.showwarning("Токен", "Вставь токен"); return
        if not uid_s: messagebox.showwarning("User ID", "Укажи User ID"); return
        try: uid = int(uid_s)
        except ValueError: messagebox.showerror("User ID", "Должен быть числом"); return

        self.token = token; self.cfg["token"] = token; self.cfg["user_id"] = uid; save_cfg(self.cfg)
        self._rescan(); self.cancel_flag = False
        self._set_busy(True, allow_cancel=True)
        self.var_status.set("Загрузка плейлиста…")
        threading.Thread(target=self._load_worker, args=(token, uid), daemon=True).start()

    def _load_worker(self, token, uid):
        try:
            self.service = Service(self.user_agent, token)
            if not self.service.is_token_valid(): raise RuntimeError("Токен недействителен")
            songs = []; offset = 0
            while True:
                batch = self.service.get_songs_by_userid(uid, 200, offset)
                if not batch: break
                songs.extend(batch)
                if len(batch) < 200: break
                offset += 200
            if not songs: raise RuntimeError("Нет треков")
            self.user_id = uid
            self.q.put(("playlist", songs))
        except Exception as e: self.q.put(("error", f"Ошибка: {e}"))
        finally: self.q.put(("busy", (False,)))


    def search_vk(self):
        if self.busy: return
        token = self.txt_token.get("1.0", "end").strip()
        if not token:
            messagebox.showwarning("Токен", "Вставь токен (вкладка Вход)")
            return
        q = self.var_query.get().strip()
        if not q: return

        self.token = token
        self.busy = True
        self._set_busy(True)
        self.var_status.set(f"Поиск в ВК: {q}…")
        threading.Thread(target=self._search_worker, args=(token, q), daemon=True).start()

    def _search_worker(self, token, query):
        try:
            service = Service(self.user_agent, token)
            results = service.search_songs_by_text(query, count=100)
            self.q.put(("playlist", results))
            self.q.put(("status", f"Найдено в ВК: {len(results)}"))
            self.q.put(("done", f"Поиск по запросу «{query}» завершен."))
        except Exception as e:
            self.q.put(("error", f"Ошибка поиска: {e}"))
        finally:
            self.q.put(("busy", (False,)))

    def start_dl(self, only_sel):

        if self.busy or not self.token: return
        if only_sel:
            idxs = sorted(int(iid[1:]) for iid in self.selected)
            targets = [self.tracks[i] for i in idxs]
        else: targets = list(self.tracks)
        if not targets: return

        base_dir = self.var_dir.get().strip() or DEFAULT_DIR
        music_dir = resolve_music_dir(base_dir); os.makedirs(music_dir, exist_ok=True)
        self.cfg["download_dir"] = base_dir; save_cfg(self.cfg)

        self.cancel_flag = False; self.force = self.var_force.get()
        self._set_busy(True, allow_cancel=True)
        self.var_status.set(f"Скачивание {len(targets)} треков…")
        threading.Thread(target=self._dl_worker, args=(targets, music_dir), daemon=True).start()

    def _dl_worker(self, targets, music_dir):
        total = len(targets); ok = skipped = 0; errs = []
        for i, s in enumerate(targets, 1):
            if self.cancel_flag: break
            artist = (getattr(s, "artist", "") or "").strip(); title = (getattr(s, "title", "") or "").strip()
            fname = safe_filename(title, artist); out_path = os.path.join(music_dir, fname)
            self.q.put(("status", f"[{i}/{total}] {artist} — {title}")); self.q.put(("progress", (i - 1, total)))

            if not self.force:
                existing = find_existing_file(music_dir, title, artist)
                if existing:
                    skipped += 1
                    br, sz, dur = read_mp3_info(existing)
                    self.q.put(("downloaded_one", (fname, {"bitrate": br, "size": sz, "duration": dur, "path": existing})))
                    continue

            owner_id, audio_id = extract_ids(s)
            if owner_id is None or audio_id is None: errs.append(f"{artist} — {title}: нет ID"); continue

            try:
                url, _ = fetch_audio_url(owner_id, audio_id, self.token, self.user_agent)
                ff_br = download_hls_to_mp3(url, out_path, lambda: self.cancel_flag,
                                            on_progress=lambda sz, br: self.q.put(("live_update", (fname, sz, br))),
                                            user_agent=self.user_agent)
                br, sz, dur = read_mp3_info(out_path, ff_br)
                self.q.put(("downloaded_one", (fname, {"bitrate": br, "size": sz, "duration": dur, "path": out_path})))
                ok += 1
            except Exception as e:
                errs.append(f"{artist} — {title}: {e}")
                if not os.path.exists(out_path):
                    self.q.put(("downloaded_one", (fname, {"bitrate": None, "size": 0, "duration": 0, "path": ""})))
            time.sleep(DELAY_BETWEEN_TRACKS)

        msg = f"Скачано: {ok}, пропущено: {skipped}, из {total}"
        if errs: msg += f"\n\nОшибок: {len(errs)}\n" + "\n".join(errs[:5])
        self.q.put(("progress", (total, total))); self.q.put(("done", msg))

    # ========================================================================
    # ПЛЕЕР (VLC)
    # ========================================================================
    def _load_player_files(self):
        self.player_listbox.delete(0, tk.END)
        base_dir = self.var_dir.get().strip(); music_dir = resolve_music_dir(base_dir)
        if not music_dir or not os.path.isdir(music_dir): return
        self.current_playlist = sorted([f for f in os.listdir(music_dir) if f.lower().endswith(".mp3")])
        for f in self.current_playlist: 
            self.player_listbox.insert(tk.END, f)
        _log.info(f"Плеер: загружено {len(self.current_playlist)} файлов из {music_dir}")

    def _player_play(self):
        sel = self.player_listbox.curselection()
        if not sel and self.current_track_index == -1:
            if self.current_playlist:
                self.current_track_index = 0
                sel = (0,)
            else:
                messagebox.showwarning("Плеер", "Список треков пуст"); return
        elif sel:
            self.current_track_index = sel[0]
        
        filename = self.current_playlist[self.current_track_index]
        base_dir = self.var_dir.get().strip(); music_dir = resolve_music_dir(base_dir)
        filepath = os.path.join(music_dir, filename)
        
        if not os.path.exists(filepath): 
            messagebox.showerror("Плеер", f"Файл не найден: {filename}"); return

        media = self.vlc_instance.media_new(filepath)
        self.vlc_player.set_media(media)
        self.vlc_player.play()
        self.is_playing = True
        
        # Обновляем интерфейс
        self.lbl_now_playing.config(text=filename)
        self.player_listbox.selection_clear(0, tk.END)
        self.player_listbox.selection_set(self.current_track_index)
        self.var_status.set(f"▶ Воспроизведение: {filename}")
        self._on_volume_change(self.vol_scale.get())

    def _player_pause(self):
        if self.vlc_player.is_playing():
            self.vlc_player.pause()
            self.is_playing = False
        else:
            self.vlc_player.play()
            self.is_playing = True

    def _player_stop(self):
        self.vlc_player.stop()
        self.is_playing = False
        self.current_track_index = -1
        self.player_scale.set(0)
        self.lbl_now_playing.config(text="Ничего не играет")
        self.var_status.set("⏹ Остановлено")

    def _player_next(self):
        if not self.current_playlist: return
        self.current_track_index = (self.current_track_index + 1) % len(self.current_playlist)
        self.player_listbox.selection_clear(0, tk.END)
        self.player_listbox.selection_set(self.current_track_index)
        self._player_play()

    def _player_prev(self):
        if not self.current_playlist: return
        self.current_track_index = (self.current_track_index - 1) % len(self.current_playlist)
        self.player_listbox.selection_clear(0, tk.END)
        self.player_listbox.selection_set(self.current_track_index)
        self._player_play()

    def _on_seek(self, val):
        if not self._seek_dragging: return
        try:
            length = self.vlc_player.get_length()
            if length > 0:
                seek_time = int((float(val) / 100.0) * length)
                self.vlc_player.set_time(seek_time)
        except Exception: pass

    def _update_player_progress(self):
        try:
            if self.vlc_player.is_playing() and not self._seek_dragging:
                length = self.vlc_player.get_length()
                time = self.vlc_player.get_time()
                if length > 0:
                    pos = (time / length) * 100.0
                    self.player_scale.set(pos)
            
            # Логика повтора
            if self.vlc_player.get_state() == vlc.State.Ended:
                if self.var_loop.get():
                    self._player_next()
                else:
                    self._player_stop()
        except Exception: pass
        self.root.after(500, self._update_player_progress)

    def _on_volume_change(self, val):
        try: self.vlc_player.audio_set_volume(int(float(val)))
        except Exception: pass

def main():
    root = ttk.Window(themename="darkly")
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
