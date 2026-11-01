# -*- coding: utf-8 -*-
"""Яндекс.Музыка — поиск и скачивание (мульти-стратегия для MP3 320 kbps)"""

import os
import re
import sys
import threading
import queue
import time
import base64
import hmac
import hashlib
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests
import logging

# ============================================================================
# ЛОГИРОВАНИЕ (в папке проекта)
# ============================================================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(APP_DIR, "ym_app.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("ym_app")

try:
    from yandex_music import Client
except ImportError:
    print("pip install yandex-music")
    sys.exit(1)

# Константы
SIGN_SALT = "XGRlBW9FXlekgbPrRHuSiA"
DEFAULT_SIGN_KEY = "p93jhgh689SBReK6ghtw62"

CONFIG_DIR = os.path.expanduser("~")
TOKEN_FILE = os.path.join(CONFIG_DIR, ".yandex_music_token")
DEFAULT_DIR = os.path.join(CONFIG_DIR, "Music", "YandexMusic")
os.makedirs(DEFAULT_DIR, exist_ok=True)


def enable_clipboard(widget):
    is_text = isinstance(widget, tk.Text)
    def _paste(e=None):
        try:
            data = widget.clipboard_get()
            if is_text:
                try:
                    if widget.tag_ranges("sel"): widget.delete("sel.first", "sel.last")
                except Exception: pass
                widget.insert(tk.INSERT, data)
            else:
                try: widget.delete("sel.first", "sel.last")
                except Exception: pass
                widget.insert(tk.INSERT, data)
        except Exception: pass
        return "break"
    def _copy(e=None):
        try:
            widget.clipboard_clear(); widget.clipboard_append(widget.selection_get())
        except Exception: pass
        return "break"
    def _cut(e=None):
        try:
            widget.clipboard_clear(); widget.clipboard_append(widget.selection_get())
            widget.delete("sel.first", "sel.last")
        except Exception: pass
        return "break"
    def _sel_all(e=None):
        try:
            if is_text: widget.tag_add("sel", "1.0", "end-1c")
            else: widget.select_range(0, "end")
        except Exception: pass
        return "break"
    for seq in ("<Command-v>", "<Control-v>"):
        try: widget.bind(seq, _paste)
        except Exception: pass
    for seq in ("<Command-c>", "<Control-c>"):
        try: widget.bind(seq, _copy)
        except Exception: pass
    for seq in ("<Command-x>", "<Control-x>"):
        try: widget.bind(seq, _cut)
        except Exception: pass
    for seq in ("<Command-a>", "<Control-a>"):
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
    widget.bind("<Button-2>", _popup)
    widget.bind("<Button-3>", _popup)


def load_token():
    try:
        with open(TOKEN_FILE) as f: return f.read().strip()
    except Exception: return ""


def save_token(token):
    try:
        with open(TOKEN_FILE, "w") as f: f.write(token)
    except Exception: pass


def _download_file(url, out_path, on_progress=None):
    if on_progress: on_progress(0, 100)
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    total = int(r.headers.get("Content-Length", 0))
    done = 0
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(65536):
            if chunk:
                f.write(chunk); done += len(chunk)
                if on_progress and total: on_progress(done, total)
    return out_path


def _get_file_info_url(track_id, token, quality, codecs, transport, label):
    """Универсальная функция для запроса /get-file-info с разными параметрами."""
    ts = int(time.time())
    sign_str = f"{ts}{track_id}{quality}{codecs}{transport}"
    digest = hmac.new(DEFAULT_SIGN_KEY.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256).digest()
    sign = base64.b64encode(digest).decode("utf-8")[:-1]

    url = "https://api.music.yandex.net/get-file-info"
    params = {"trackId": track_id, "quality": quality, "codecs": codecs,
              "transports": transport, "ts": ts, "sign": sign}
    headers = {"Authorization": f"OAuth {token}", "Accept": "application/json"}

    try:
        r = requests.get(url, params=params, headers=headers, timeout=30)
        if r.status_code != 200:
            log.warning("[%s] HTTP %s: %s", label, r.status_code, r.text[:150])
            return None
        data = r.json().get("result", {})
        for item in data.get("downloadInfo") or data.get("transports") or []:
            if not isinstance(item, dict): continue
            bitrate = item.get("bitrateInKbps", 0)
            if "mp3" in str(item.get("codec", "")).lower() and bitrate >= 320:
                direct_url = item.get("downloadInfoUrl") or item.get("url")
                if direct_url:
                    log.info("[%s] УСПЕХ: найден MP3 %s kbps", label, bitrate)
                    return direct_url
    except Exception as e:
        log.exception("[%s] Ошибка: %s", label, e)
    return None


def get_320_url_multistrategy(track_id, token):
    """
    Пробует несколько стратегий получения 320 kbps.
    """
    # Стратегия 1: quality="hq", codecs="mp3", transport="raw"
    url = _get_file_info_url(track_id, token, quality="hq", codecs="mp3",
                             transport="raw", label="HQ/MP3/RAW")
    if url: return url

    # Стратегия 2: quality="hq", codecs="mp3", transport="encraw"
    url = _get_file_info_url(track_id, token, quality="hq", codecs="mp3",
                             transport="encraw", label="HQ/MP3/ENCRAW")
    if url: return url

    # Стратегия 3: quality="lossless" (как было раньше, вдруг повезёт)
    url = _get_file_info_url(track_id, token, quality="lossless",
                             codecs="flac-mp4,flac,mp3", transport="encraw",
                             label="LOSSLESS")
    if url: return url

    log.info("Все стратегии /get-file-info исчерпаны.")
    return None


def download_via_download_info(track_id, token, out_path, on_progress=None):
    """Fallback: старый метод download-info (192 kbps)."""
    url = f"https://api.music.yandex.net/tracks/{track_id}/download-info"
    headers = {"Authorization": f"OAuth {token}", "Accept": "application/json"}
    resp = requests.get(url, headers=headers, params={"hq": 1}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"download-info HTTP {resp.status_code}")
    data = resp.json()
    streams = data.get("result", [])
    if not streams: raise RuntimeError("Нет потоков")

    mp3_streams = [x for x in streams if str(x.get("codec", "")).lower() == "mp3"]
    pick = max(mp3_streams or streams, key=lambda x: x.get("bitrateInKbps", 0))
    log.info("download-info: выбран %s kbps", pick.get("bitrateInKbps", "?"))

    info_url = pick.get("downloadInfoUrl")
    if not info_url: raise RuntimeError("Нет downloadInfoUrl")

    sep = "&" if "?" in info_url else "?"
    ir = requests.get(info_url + sep + "format=json", timeout=30)
    ir.raise_for_status()
    try: info = ir.json()
    except Exception:
        txt = ir.text
        def _g(tag):
            mm = re.search(r"<" + tag + r">(.*?)</" + tag + r">", txt)
            return mm.group(1) if mm else ""
        info = {"host": _g("host"), "path": _g("path"), "ts": _g("ts"), "s": _g("s")}

    host, path, ts, s = info.get("host"), info.get("path"), info.get("ts"), info.get("s")
    if not (host and path and ts and s):
        raise RuntimeError(f"download-info resolve failed: {info}")

    sign = hashlib.md5((SIGN_SALT + path[1:] + s).encode("utf-8")).hexdigest()
    final_url = f"https://{host}/get-mp3/{sign}/{ts}{path}"
    log.info("download-info: финальный URL %s", final_url[:80])
    _download_file(final_url, out_path, on_progress)
    return out_path


def download_via_web_album(track_id, album_id, token, out_path, on_progress=None):
    """Стратегия 4: альтернативный эндпоинт web-album.../download/m?hq=1"""
    if not album_id: return None
    url = f"https://music.yandex.ru/api/v2.1/handlers/track/{track_id}:{album_id}/web-album_track-track-track-main/download/m"
    headers = {"Authorization": f"OAuth {token}", "X-Retpath-Y": "https://music.yandex.ru/"}
    try:
        r = requests.get(url, params={"hq": 1}, headers=headers, timeout=30)
        if r.status_code != 200:
            log.warning("[WEB-ALBUM] HTTP %s", r.status_code); return None
        data = r.json()
        direct_url = data.get("downloadInfoUrl") or data.get("url")
        if direct_url:
            log.info("[WEB-ALBUM] УСПЕХ: получен URL")
            _download_file(direct_url, out_path, on_progress)
            return out_path
    except Exception as e:
        log.exception("[WEB-ALBUM] Ошибка: %s", e)
    return None


def download_track_320(track_id, token, out_path, on_progress=None, album_id=None, track_obj=None):
    """Основная функция: перебирает стратегии."""
    # Стратегия 0: встроенный метод библиотеки (если доступен)
    if track_obj is not None:
        try:
            if hasattr(track_obj, "get_file_info"):
                info = track_obj.get_file_info(quality="hq", codecs="mp3", transport="raw")
                if info and info.download_info and info.download_info.url:
                    log.info("Встроенный get_file_info: УСПЕХ")
                    _download_file(info.download_info.url, out_path, on_progress)
                    return out_path
        except Exception as e:
            log.debug("Встроенный get_file_info не сработал: %s", e)

    # Стратегии 1-3: /get-file-info
    url = get_320_url_multistrategy(track_id, token)
    if url:
        _download_file(url, out_path, on_progress)
        return out_path

    # Стратегия 4: web-album эндпоинт
    log.info("Пробуем web-album эндпоинт...")
    res = download_via_web_album(track_id, album_id, token, out_path, on_progress)
    if res: return res

    # Fallback: download-info
    log.info("Все стратегии 320 не сработали, fallback на download-info")
    return download_via_download_info(track_id, token, out_path, on_progress)


class App:
    def __init__(self, root):
        self.root = root
        root.title("Яндекс.Музыка — поиск и скачивание (MP3 320 kbps)")
        root.geometry("1100x720")
        self.client = None
        self.tracks = []
        self.q = queue.Queue()
        self.busy = False
        self._ui()
        root.after(80, self._pump)

    def _ui(self):
        top = ttk.LabelFrame(self.root, text="Токен Яндекс.Музыки", padding=10)
        top.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(top, text="Токен:").grid(row=0, column=0, sticky="w")
        tf = ttk.Frame(top)
        tf.grid(row=0, column=1, columnspan=2, sticky="we", padx=5)
        self.ent_token = tk.Text(tf, height=2, font=("Consolas", 9))
        self.ent_token.pack(fill=tk.X)
        enable_clipboard(self.ent_token)
        token = load_token()
        if token: self.ent_token.insert("1.0", token)
        ttk.Button(top, text="Подключиться", command=self.connect).grid(row=0, column=3, padx=5)
        ttk.Label(top, text="Токен: https://oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d",
                  foreground="#666", font=("", 9)).grid(row=1, column=0, columnspan=4, sticky="w", pady=(5, 0))
        top.columnconfigure(1, weight=1)

        self.lbl_status = ttk.Label(self.root, text="Не подключен", foreground="red")
        self.lbl_status.pack(anchor="w", padx=10)

        sf = ttk.LabelFrame(self.root, text="Поиск", padding=10)
        sf.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(sf, text="Запрос:").pack(side=tk.LEFT)
        self.var_query = tk.StringVar()
        ent = ttk.Entry(sf, textvariable=self.var_query, width=40)
        ent.pack(side=tk.LEFT, padx=5)
        ent.bind("<Return>", lambda e: self.do_search())
        enable_clipboard(ent)
        ttk.Button(sf, text="🔍 Найти", command=self.do_search).pack(side=tk.LEFT, padx=5)
        ttk.Label(sf, text="Папка:").pack(side=tk.LEFT, padx=(20, 5))
        self.var_dir = tk.StringVar(value=DEFAULT_DIR)
        ent2 = ttk.Entry(sf, textvariable=self.var_dir)
        ent2.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        enable_clipboard(ent2)
        ttk.Button(sf, text="…", width=3, command=self._pick_dir).pack(side=tk.LEFT)

        mid = ttk.Frame(self.root)
        mid.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        cols = ("num", "artist", "title", "duration", "album")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c, h, w in (("num", "#", 40), ("artist", "Исполнитель", 250),
                        ("title", "Название", 400), ("duration", "Длит.", 80), ("album", "Альбом", 250)):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w" if c in ("artist","title","album") else "center")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); sb.pack(side=tk.RIGHT, fill=tk.Y)

        bot = ttk.Frame(self.root, padding=10)
        bot.pack(fill=tk.X)
        self.btn_dl = ttk.Button(bot, text="⬇ Скачать выбранные (MP3 320)", command=self.download_selected)
        self.btn_dl.pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(bot, mode="determinate")
        self.progress.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=10)
        self.var_status = tk.StringVar(value="Готов")
        ttk.Label(self.root, textvariable=self.var_status, relief="sunken", anchor="w", padding=5).pack(fill=tk.X, side=tk.BOTTOM)

    def _pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.var_dir.get())
        if d: self.var_dir.set(d)

    def connect(self):
        token = self.ent_token.get("1.0", "end").strip()
        if not token:
            messagebox.showwarning("Токен", "Введите токен"); return
        self.var_status.set("Подключение...")
        def worker():
            try:
                client = Client(token).init()
                self.client = client
                save_token(token)
                self.q.put(("connected", None))
            except Exception as e:
                self.q.put(("error", f"Ошибка подключения: {e}"))
        threading.Thread(target=worker, daemon=True).start()

    def do_search(self):
        if self.busy or not self.client: return
        q = self.var_query.get().strip()
        if not q: return
        self.busy = True; self.var_status.set(f"Поиск: {q}…")
        def worker():
            try:
                result = self.client.search(q, type_='track')
                tracks = result.tracks.results if result and result.tracks else []
                self.q.put(("results", tracks))
            except Exception as e:
                self.q.put(("error", f"Ошибка поиска: {e}"))
        threading.Thread(target=worker, daemon=True).start()

    def _fill_results(self, tracks):
        self.tracks = tracks
        for iid in self.tree.get_children(): self.tree.delete(iid)
        for i, t in enumerate(tracks):
            artist = ", ".join([a.name for a in t.artists]) if t.artists else "?"
            dur = (t.duration_ms or 0) // 1000
            album = t.albums[0].title if t.albums else ""
            self.tree.insert("", "end", iid=f"t{i}", values=(i+1, artist, t.title, f"{dur//60}:{dur%60:02d}", album))
        self.var_status.set(f"Найдено: {len(tracks)}")

    def download_selected(self):
        if self.busy or not self.client: return
        sel = self.tree.selection()
        if not sel: return
        tracks = [self.tracks[int(iid[1:])] for iid in sel]
        out_dir = self.var_dir.get()
        os.makedirs(out_dir, exist_ok=True)
        self.busy = True; self.btn_dl.configure(state="disabled")
        self.var_status.set(f"Скачивание {len(tracks)} треков…")
        def worker():
            ok = 0; errs = []
            token = self.ent_token.get("1.0", "end").strip()
            for i, t in enumerate(tracks, 1):
                self.q.put(("status", f"[{i}/{len(tracks)}] {t.title}"))
                self.q.put(("progress", (i-1, len(tracks))))
                try:
                    artist = t.artists[0].name if t.artists else "Unknown"
                    safe = re.sub(r'[\\/:*?"<>|~]+', ' ', f"{t.title} - {artist}")[:150]
                    path = os.path.join(out_dir, safe + ".mp3")
                    if os.path.exists(path) and os.path.getsize(path) > 100000:
                        ok += 1; continue
                    album_id = str(t.albums[0].id) if t.albums else None
                    download_track_320(t.id, token, path,
                                        on_progress=lambda c, tt: self.q.put(("progress", (i-1+c/tt, len(tracks)))) if tt else None,
                                        album_id=album_id, track_obj=t)
                    ok += 1
                except Exception as e:
                    errs.append(f"{t.title}: {e}")
            msg = f"Скачано: {ok} из {len(tracks)}"
            if errs: msg += "\n\nОшибки:\n" + "\n".join(errs[:5])
            self.q.put(("progress", (len(tracks), len(tracks))))
            self.q.put(("done", msg))
        threading.Thread(target=worker, daemon=True).start()

    def _pump(self):
        try:
            while True:
                k, p = self.q.get_nowait()
                if k == "status": self.var_status.set(p)
                elif k == "progress":
                    c, t = p; self.progress["maximum"] = max(t, 1); self.progress["value"] = c
                elif k == "results": self.busy = False; self._fill_results(p)
                elif k == "connected":
                    self.busy = False
                    self.lbl_status.configure(text="Подключен к Яндекс.Музыке", foreground="green")
                    self.var_status.set("Подключено")
                elif k == "done":
                    self.busy = False; self.btn_dl.configure(state="normal")
                    self.progress["value"] = 0
                    messagebox.showinfo("Готово", p)
                elif k == "error":
                    self.busy = False; self.btn_dl.configure(state="normal")
                    self.progress["value"] = 0
                    messagebox.showerror("Ошибка", p)
        except queue.Empty: pass
        self.root.after(80, self._pump)


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        for t in ("aqua", "vista", "clam"):
            if t in style.theme_names(): style.theme_use(t); break
    except Exception: pass
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
