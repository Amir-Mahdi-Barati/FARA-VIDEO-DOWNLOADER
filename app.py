import os
import re
import sys
import time
import shutil
import threading
import datetime
import tkinter as tk
from tkinter import filedialog, messagebox
from io import BytesIO
from urllib.request import Request, urlopen

import customtkinter as ctk
from PIL import Image
import yt_dlp


# =========================================================
# CONFIG
# =========================================================

APP_NAME = "FARA VIDEO DOWNLOADER"
DEFAULT_FOLDER = os.path.join(os.getcwd(), "downloads")
os.makedirs(DEFAULT_FOLDER, exist_ok=True)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# =========================================================
# HELPERS
# =========================================================

def clean_url(url):
    return url.strip()


def human_size(size):
    if not size:
        return "--"
    try:
        size = float(size)
    except Exception:
        return "--"

    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_duration(seconds):
    if not seconds:
        return "Unknown"
    try:
        seconds = int(seconds)
    except Exception:
        return "Unknown"

    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def find_ffmpeg():
    candidates = [
        shutil.which("ffmpeg"),
        os.path.join(os.getcwd(), "ffmpeg.exe"),
        os.path.join(os.getcwd(), "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(os.getcwd(), "bin", "ffmpeg.exe"),
    ]

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def timestamp():
    return datetime.datetime.now().strftime("%H:%M:%S")


# =========================================================
# LOGGER BRIDGE (yt-dlp -> UI log panel)
# =========================================================

class UILogger:
    """
    Bridges yt-dlp's internal logger to the on-screen log panel.
    Every debug/info/warning/error line yt-dlp emits gets pushed
    into the GUI so the user can see exactly what is happening
    (format negotiation, retries, merging, extractor messages, etc).
    """

    def __init__(self, app):
        self.app = app

    def debug(self, msg):
        # yt-dlp routes some "info" lines through debug() prefixed with [debug]
        if msg.startswith("[debug] "):
            return
        self.app.log(msg)

    def info(self, msg):
        self.app.log(msg)

    def warning(self, msg):
        self.app.log(f"WARNING: {msg}", level="warn")

    def error(self, msg):
        self.app.log(f"ERROR: {msg}", level="error")


# =========================================================
# APPLICATION
# =========================================================

class VideoDownloader(ctk.CTk):

    def __init__(self):
        super().__init__()

        self.title(APP_NAME)
        self.geometry("1200x860")
        self.minsize(1000, 720)

        self.video_info = None
        self.formats = []
        self.quality_options = []   # list of dicts: {label, height, format_id, kind}
        self.download_folder = DEFAULT_FOLDER
        self.cancel_requested = False
        self.ffmpeg_path = find_ffmpeg()

        self.build_ui()
        self.log(f"{APP_NAME} started.")
        self.log(f"FFmpeg: {'found at ' + self.ffmpeg_path if self.ffmpeg_path else 'NOT FOUND - merging high quality streams may fail'}")

    # =====================================================
    # UI
    # =====================================================

    def build_ui(self):

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ---------------- HEADER ----------------

        header = ctk.CTkFrame(self, height=75, corner_radius=0)
        header.grid(row=0, column=0, columnspan=2, sticky="nsew")

        title = ctk.CTkLabel(
            header,
            text=APP_NAME,
            font=("Segoe UI", 25, "bold")
        )
        title.pack(side="left", padx=30)

        subtitle = ctk.CTkLabel(
            header,
            text="Universal yt-dlp downloader — any site yt-dlp supports",
            font=("Segoe UI", 13)
        )
        subtitle.pack(side="left")

        # ---------------- SIDEBAR ----------------

        sidebar = ctk.CTkFrame(self, width=260, corner_radius=0)
        sidebar.grid(row=1, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        sidebar_title = ctk.CTkLabel(
            sidebar,
            text="DOWNLOAD",
            font=("Segoe UI", 15, "bold")
        )
        sidebar_title.pack(anchor="w", padx=25, pady=(30, 15))

        self.folder_button = ctk.CTkButton(
            sidebar,
            text="📁 Download Folder",
            height=42,
            command=self.choose_folder
        )
        self.folder_button.pack(padx=20, pady=8, fill="x")

        self.folder_label = ctk.CTkLabel(
            sidebar,
            text=self.download_folder,
            wraplength=210,
            justify="left",
            font=("Segoe UI", 11)
        )
        self.folder_label.pack(padx=20, pady=5)

        self.ffmpeg_status = (
            "Available" if self.ffmpeg_path else "Not Found"
        )

        self.engine_label = ctk.CTkLabel(
            sidebar,
            text=(
                "Engine: yt-dlp\n"
                f"FFmpeg: {self.ffmpeg_status}\n\n"
                "Automatic quality:\n"
                "Highest available quality\n"
                "is used when nothing is\n"
                "explicitly selected."
            ),
            wraplength=215,
            justify="left",
            font=("Segoe UI", 11)
        )
        self.engine_label.pack(padx=20, pady=18)

        self.cookie_label = ctk.CTkLabel(
            sidebar,
            text=(
                "Content you're logged into:\n"
                "Pick your browser below to\n"
                "reuse its session cookies.\n\n"
                "Note: this only works for\n"
                "content your account can\n"
                "already open in that browser.\n"
                "DRM-protected or paywalled\n"
                "streams cannot be unlocked."
            ),
            wraplength=215,
            justify="left",
            font=("Segoe UI", 11)
        )
        self.cookie_label.pack(side="bottom", padx=20, pady=25)

        # ---------------- MAIN ----------------

        main = ctk.CTkFrame(self, corner_radius=0)
        main.grid(row=1, column=1, sticky="nsew", padx=15, pady=15)

        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(11, weight=1)

        # URL

        url_label = ctk.CTkLabel(
            main,
            text="Video URL",
            font=("Segoe UI", 15, "bold")
        )
        url_label.grid(row=0, column=0, sticky="w", padx=25, pady=(20, 6))

        url_frame = ctk.CTkFrame(main, fg_color="transparent")
        url_frame.grid(row=1, column=0, sticky="ew", padx=25)
        url_frame.grid_columnconfigure(0, weight=1)

        self.url_entry = ctk.CTkEntry(
            url_frame,
            height=45,
            placeholder_text="Paste any video URL here (YouTube, Instagram, TikTok, X/Twitter, Vimeo, etc.)"
        )
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.url_entry.bind("<Control-v>", self.handle_paste)
        self.url_entry.bind("<Button-3>", self.show_context_menu)

        self.paste_button = ctk.CTkButton(
            url_frame,
            text="📋 Paste",
            width=100,
            height=45,
            command=self.paste_url
        )
        self.paste_button.grid(row=0, column=1, padx=(0, 8))

        self.analyze_button = ctk.CTkButton(
            url_frame,
            text="Analyze",
            width=130,
            height=45,
            command=self.analyze
        )
        self.analyze_button.grid(row=0, column=2)

        # OPTIONS

        options_frame = ctk.CTkFrame(main, fg_color="transparent")
        options_frame.grid(row=2, column=0, sticky="ew", padx=25, pady=(12, 0))
        options_frame.grid_columnconfigure(3, weight=1)

        self.browser_var = tk.StringVar(value="None")

        ctk.CTkLabel(
            options_frame,
            text="Use my session from:"
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))

        self.browser_combo = ctk.CTkComboBox(
            options_frame,
            values=["None", "Chrome", "Edge", "Firefox", "Brave", "Opera"],
            variable=self.browser_var,
            width=140
        )
        self.browser_combo.grid(row=0, column=1, sticky="w")

        self.audio_only_var = tk.BooleanVar(value=False)
        self.audio_only_check = ctk.CTkCheckBox(
            options_frame,
            text="Audio only (extract best audio)",
            variable=self.audio_only_var
        )
        self.audio_only_check.grid(row=0, column=2, sticky="w", padx=25)

        # VIDEO INFO

        self.info_frame = ctk.CTkFrame(main, height=165)
        self.info_frame.grid(
            row=3, column=0, sticky="ew",
            padx=25, pady=15
        )

        self.thumbnail = ctk.CTkLabel(
            self.info_frame,
            text="No Preview",
            width=220,
            height=125
        )
        self.thumbnail.pack(side="left", padx=20, pady=20)

        details = ctk.CTkFrame(
            self.info_frame,
            fg_color="transparent"
        )
        details.pack(
            side="left",
            fill="both",
            expand=True,
            padx=10
        )

        self.title_label = ctk.CTkLabel(
            details,
            text="Video title will appear here",
            font=("Segoe UI", 18, "bold"),
            anchor="w",
            justify="left",
            wraplength=680
        )
        self.title_label.pack(anchor="w", pady=(20, 6))

        self.duration_label = ctk.CTkLabel(details, text="Duration: --")
        self.duration_label.pack(anchor="w")

        self.source_label = ctk.CTkLabel(details, text="Source: --")
        self.source_label.pack(anchor="w")

        self.formats_label = ctk.CTkLabel(details, text="Formats: --")
        self.formats_label.pack(anchor="w")

        # QUALITY

        quality_label = ctk.CTkLabel(
            main,
            text="Available Quality",
            font=("Segoe UI", 15, "bold")
        )
        quality_label.grid(row=4, column=0, sticky="w", padx=25, pady=(2, 6))

        self.quality_combo = ctk.CTkComboBox(
            main,
            values=["Analyze a video first"],
            height=42
        )
        self.quality_combo.grid(row=5, column=0, sticky="ew", padx=25)
        self.quality_combo.set("Automatic — Highest Available")

        # DOWNLOAD

        action_frame = ctk.CTkFrame(main, fg_color="transparent")
        action_frame.grid(row=6, column=0, sticky="ew", padx=25, pady=16)
        action_frame.grid_columnconfigure(0, weight=1)

        self.download_button = ctk.CTkButton(
            action_frame,
            text="DOWNLOAD",
            height=50,
            font=("Segoe UI", 15, "bold"),
            command=self.start_download
        )
        self.download_button.grid(row=0, column=0, sticky="ew")

        self.cancel_button = ctk.CTkButton(
            action_frame,
            text="Cancel",
            width=120,
            height=50,
            fg_color="transparent",
            border_width=1,
            command=self.cancel_download
        )
        self.cancel_button.grid(row=0, column=1, padx=(10, 0))

        # PROGRESS

        self.progress = ctk.CTkProgressBar(main, height=12)
        self.progress.grid(row=7, column=0, sticky="ew", padx=25)
        self.progress.set(0)

        # STATUS

        self.status_label = ctk.CTkLabel(
            main,
            text="Ready",
            font=("Segoe UI", 12, "bold")
        )
        self.status_label.grid(row=8, column=0, sticky="w", padx=25, pady=(10, 2))

        self.stats_label = ctk.CTkLabel(
            main,
            text="Speed: --   |   ETA: --   |   Size: --"
        )
        self.stats_label.grid(row=9, column=0, sticky="w", padx=25)

        # LIVE LOG PANEL

        log_label = ctk.CTkLabel(
            main,
            text="Live Log",
            font=("Segoe UI", 13, "bold")
        )
        log_label.grid(row=10, column=0, sticky="w", padx=25, pady=(14, 4))

        log_frame = ctk.CTkFrame(main)
        log_frame.grid(row=11, column=0, sticky="nsew", padx=25, pady=(0, 10))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)

        self.log_box = ctk.CTkTextbox(
            log_frame,
            font=("Consolas", 11),
            wrap="word"
        )
        self.log_box.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.log_box.configure(state="disabled")

        clear_log_btn = ctk.CTkButton(
            log_frame,
            text="Clear Log",
            width=100,
            height=28,
            command=self.clear_log
        )
        clear_log_btn.grid(row=1, column=0, sticky="e", padx=8, pady=(0, 8))

    # =====================================================
    # LOG PANEL
    # =====================================================

    def log(self, message, level="info"):
        """
        Thread-safe append to the on-screen live log.
        Safe to call from any worker thread.
        """
        prefix = {
            "info": "•",
            "warn": "⚠",
            "error": "✖",
            "ok": "✓",
        }.get(level, "•")

        line = f"[{timestamp()}] {prefix} {message}\n"

        def append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        if threading.current_thread() is threading.main_thread():
            append()
        else:
            self.after(0, append)

    def clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    # =====================================================
    # PASTE
    # =====================================================

    def paste_url(self):
        try:
            clipboard = self.clipboard_get().strip()
            if not clipboard:
                raise tk.TclError

            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, clipboard)
            self.url_entry.focus()

            self.status_label.configure(text="URL pasted successfully")
            self.log("URL pasted from clipboard.")

        except tk.TclError:
            self.status_label.configure(text="Clipboard is empty")

    def handle_paste(self, event=None):
        self.paste_url()
        return "break"

    def show_context_menu(self, event):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Paste", command=self.paste_url)
        menu.add_command(
            label="Clear",
            command=lambda: self.url_entry.delete(0, "end")
        )
        menu.tk_popup(event.x_root, event.y_root)

    # =====================================================
    # FOLDER
    # =====================================================

    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.download_folder = folder
            self.folder_label.configure(text=folder)
            self.log(f"Download folder set to: {folder}")

    # =====================================================
    # YT-DLP OPTIONS
    # =====================================================

    def base_options(self):
        options = {
            "quiet": True,
            "no_warnings": False,
            "noplaylist": True,
            "logger": UILogger(self),

            # Robust retry behavior — helps with flaky hosts / rate limits.
            "retries": 20,
            "fragment_retries": 20,
            "extractor_retries": 8,
            "file_access_retries": 8,
            "socket_timeout": 30,

            "continuedl": True,
            "concurrent_fragment_downloads": 4,

            # Do not reuse stale temporary URLs.
            "cachedir": False,

            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },

            "progress_hooks": [self.progress_hook],
        }

        if self.ffmpeg_path:
            options["ffmpeg_location"] = os.path.dirname(self.ffmpeg_path)

        browser = self.browser_var.get()
        if browser and browser != "None":
            browser_map = {
                "Chrome": "chrome",
                "Edge": "edge",
                "Firefox": "firefox",
                "Brave": "brave",
                "Opera": "opera",
            }
            options["cookiesfrombrowser"] = (browser_map[browser],)
            self.log(f"Using cookies from your {browser} session.")

        return options

    # =====================================================
    # ANALYZE
    # =====================================================

    def analyze(self):
        url = clean_url(self.url_entry.get())

        if not url:
            messagebox.showwarning("Missing URL", "Please paste or enter a video URL.")
            return

        self.video_info = None
        self.formats = []
        self.quality_options = []

        self.analyze_button.configure(state="disabled", text="Analyzing...")
        self.status_label.configure(text="Extracting video information...")
        self.log(f"Analyzing URL: {url}")

        self.quality_combo.configure(values=["Analyzing..."])
        self.quality_combo.set("Analyzing...")

        threading.Thread(target=self.analyze_worker, args=(url,), daemon=True).start()

    def analyze_worker(self, url):
        options = self.base_options()
        options.update({
            "skip_download": True,
            "extract_flat": False,
        })

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                raise RuntimeError("yt-dlp returned no video information.")

            # Some extractors (playlists mistakenly matched, etc.) return
            # an 'entries' list even with noplaylist — take the first entry.
            if "entries" in info and info.get("entries"):
                entries = [e for e in info["entries"] if e]
                if entries:
                    info = entries[0]

            self.video_info = info
            self.formats = info.get("formats") or []
            self.quality_options = self.build_quality_options(self.formats)

            self.log(f"Extractor used: {info.get('extractor', 'unknown')}")
            self.log(f"Found {len(self.formats)} raw format(s), "
                      f"{len(self.quality_options)} selectable quality option(s).")

            self.after(0, self.show_video_info)

        except Exception as e:
            error_text = str(e)
            self.log(f"Analysis failed: {error_text}", level="error")
            self.after(0, lambda err=error_text: self.analysis_error(err))

        finally:
            self.after(
                0,
                lambda: self.analyze_button.configure(state="normal", text="Analyze")
            )

    def build_quality_options(self, formats):
        """
        Build a clean, de-duplicated list of quality choices,
        highest first, based on real available heights.
        Falls back gracefully when no explicit video heights exist
        (e.g. audio-only sources, some live streams).
        """
        seen_heights = {}

        for f in formats:
            height = f.get("height")
            vcodec = f.get("vcodec")
            if not height or not vcodec or vcodec == "none":
                continue
            try:
                height = int(height)
            except (TypeError, ValueError):
                continue

            fps = f.get("fps")
            label = f"{height}p" + (f"{int(fps)}" if fps and fps > 30 else "")

            # Keep the best (largest) entry we've seen for this label.
            if label not in seen_heights or height > seen_heights[label]["height"]:
                seen_heights[label] = {"label": label, "height": height}

        options = sorted(seen_heights.values(), key=lambda x: x["height"], reverse=True)
        return options

    def analysis_error(self, error):
        self.status_label.configure(text="Analysis failed")

        lower = error.lower()

        if "login" in lower or "sign in" in lower or "private" in lower:
            message = (
                "This content requires you to be logged in.\n\n"
                "If it's your own account or you're otherwise authorized, "
                "pick your browser under 'Use my session from' and try again.\n\n"
                "This program cannot bypass DRM, paywalls, or access controls "
                "you don't already have permission for."
            )
        elif "drm" in lower:
            message = (
                "This content is DRM-protected.\n\n"
                "DRM-protected streams (widevine, playready, fairplay, etc.) "
                "cannot be downloaded by this or any yt-dlp based tool — "
                "the decryption keys are intentionally withheld by the platform."
            )
        elif "403" in lower:
            message = (
                "The server rejected the request (HTTP 403).\n\n"
                "Update yt-dlp, verify the URL is current, or use your own "
                "authenticated browser session if the content requires login."
            )
        elif "410" in lower or "gone" in lower:
            message = (
                "The server returned HTTP 410 Gone.\n\n"
                "The resource may no longer exist, or the site's extractor "
                "may need an update."
            )
        elif "unsupported url" in lower:
            message = (
                "This site or URL format isn't recognized by yt-dlp.\n\n"
                "Double check the URL, or the site may not be supported yet."
            )
        else:
            message = f"Could not analyze this URL.\n\n{error}"

        messagebox.showerror("Analysis Error", message)

    # =====================================================
    # SHOW INFO
    # =====================================================

    def show_video_info(self):
        info = self.video_info or {}

        title = info.get("title") or "Unknown title"
        duration = info.get("duration")
        uploader = info.get("uploader") or info.get("channel") or "Unknown"

        self.title_label.configure(text=title)
        self.duration_label.configure(text=f"Duration: {format_duration(duration)}")
        self.source_label.configure(text=f"Source: {uploader}")

        if self.quality_options:
            quality_text = ", ".join(o["label"] for o in self.quality_options)
            self.formats_label.configure(text=f"Formats: {quality_text}")

            quality_values = ["Automatic — Highest Available"] + [
                o["label"] for o in self.quality_options
            ]

            self.quality_combo.configure(values=quality_values)
            self.quality_combo.set("Automatic — Highest Available")

            highest = self.quality_options[0]["label"]
            self.status_label.configure(
                text=(
                    f"Video analyzed • {len(self.quality_options)} "
                    f"quality level(s) • Highest: {highest}"
                )
            )
            self.log(f"Ready to download. Highest quality available: {highest}", level="ok")

        else:
            self.formats_label.configure(text="Formats: No explicit resolution list")
            self.quality_combo.configure(values=["Automatic — Best Available"])
            self.quality_combo.set("Automatic — Best Available")
            self.status_label.configure(
                text="No explicit qualities listed • automatic mode enabled"
            )
            self.log("No explicit quality list found — will grab the best stream automatically.")

        thumbnail_url = info.get("thumbnail")
        if thumbnail_url:
            threading.Thread(
                target=self.load_thumbnail, args=(thumbnail_url,), daemon=True
            ).start()

    # =====================================================
    # THUMBNAIL
    # =====================================================

    def load_thumbnail(self, url):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
                    )
                }
            )

            data = urlopen(request, timeout=15).read()
            image = Image.open(BytesIO(data)).convert("RGB")
            image.thumbnail((220, 125))

            photo = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)

            self.after(0, lambda p=photo: self.thumbnail.configure(image=p, text=""))

        except Exception as e:
            self.log(f"Could not load thumbnail: {e}", level="warn")

    # =====================================================
    # QUALITY
    # =====================================================

    def get_selected_height(self):
        selection = self.quality_combo.get()

        if selection.startswith("Automatic"):
            return None

        match = re.search(r"(\d+)p", selection)
        if not match:
            return None

        try:
            return int(match.group(1))
        except ValueError:
            return None

    def build_format_selector(self, selected_height, audio_only):
        """
        Selection strategy:

        Audio only:
            Best available audio stream.

        Automatic (video):
            Prefer best video + best audio, merged.
            Fall back to the single best combined stream.

        Selected quality:
            Prefer the best video stream at or below the requested
            resolution + best audio. Fall back to a combined stream
            at or below the requested resolution, then to the best
            available stream of any resolution so a download always
            succeeds.
        """
        if audio_only:
            return "bestaudio/best"

        if selected_height is None:
            return "bestvideo*+bestaudio/best"

        return (
            f"bestvideo*[height<={selected_height}]+bestaudio/"
            f"best[height<={selected_height}]/"
            "best"
        )

    # =====================================================
    # DOWNLOAD
    # =====================================================

    def start_download(self):
        url = clean_url(self.url_entry.get())

        if not url:
            messagebox.showwarning("Missing URL", "Please enter a video URL.")
            return

        selected_height = self.get_selected_height()
        audio_only = self.audio_only_var.get()

        self.cancel_requested = False
        self.progress.set(0)

        self.download_button.configure(state="disabled", text="DOWNLOADING...")
        self.status_label.configure(text="Refreshing formats...")
        self.log(f"Starting download for: {url}")
        if audio_only:
            self.log("Mode: audio only")
        elif selected_height:
            self.log(f"Requested quality: up to {selected_height}p")
        else:
            self.log("Requested quality: automatic (highest available)")

        threading.Thread(
            target=self.download_worker,
            args=(url, selected_height, audio_only),
            daemon=True
        ).start()

    def download_worker(self, url, selected_height, audio_only):
        primary_error = None

        try:
            self.after(0, lambda: self.status_label.configure(
                text="Checking current video formats..."
            ))

            extract_options = self.base_options()
            extract_options.update({"skip_download": True})

            with yt_dlp.YoutubeDL(extract_options) as ydl:
                fresh_info = ydl.extract_info(url, download=False)

            if not fresh_info:
                raise RuntimeError("Could not refresh video information.")

            if "entries" in fresh_info and fresh_info.get("entries"):
                entries = [e for e in fresh_info["entries"] if e]
                if entries:
                    fresh_info = entries[0]

            # If a requested quality no longer exists, fall back to the
            # highest currently available quality not exceeding it.
            if selected_height is not None and not audio_only:
                available = []
                for f in fresh_info.get("formats") or []:
                    h = f.get("height")
                    vc = f.get("vcodec")
                    if h and vc and vc != "none":
                        try:
                            h = int(h)
                            if h <= selected_height:
                                available.append(h)
                        except (TypeError, ValueError):
                            pass

                if available:
                    new_height = max(available)
                    if new_height != selected_height:
                        self.log(
                            f"Requested {selected_height}p no longer available, "
                            f"using {new_height}p instead.",
                            level="warn"
                        )
                    selected_height = new_height
                else:
                    self.log(
                        "Requested quality no longer available, "
                        "switching to automatic best.",
                        level="warn"
                    )
                    selected_height = None

            format_selector = self.build_format_selector(selected_height, audio_only)
            self.log(f"Format selector: {format_selector}")

            if audio_only:
                output = os.path.join(self.download_folder, "%(title)s.%(ext)s")
            else:
                output = os.path.join(self.download_folder, "%(title)s.%(ext)s")

            options = self.base_options()
            options.update({
                "format": format_selector,
                "outtmpl": output,
                "noplaylist": True,
                "overwrites": False,
                "continuedl": True,
                "retries": 20,
                "fragment_retries": 20,
                "extractor_retries": 8,
                "file_access_retries": 8,
                "retry_sleep": {
                    "http": [1, 2, 4, 8, 15],
                    "fragment": [1, 2, 4, 8, 15],
                },
                "windowsfilenames": True,
                "cachedir": False,
            })

            if audio_only:
                options["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",  # best
                }]
            else:
                options["merge_output_format"] = "mp4"

            self.after(0, lambda: self.status_label.configure(
                text=(
                    "Downloading audio..." if audio_only else
                    "Downloading highest available quality..." if selected_height is None
                    else f"Downloading up to {selected_height}p..."
                )
            ))

            with yt_dlp.YoutubeDL(options) as ydl:
                result = ydl.download([url])

            if self.cancel_requested:
                self.log("Download cancelled by user.", level="warn")
                return

            if result not in (None, 0):
                raise RuntimeError(f"yt-dlp returned exit status {result}")

            self.log("Download completed successfully.", level="ok")
            self.after(0, self.download_finished)
            return

        except Exception as e:
            primary_error = str(e)
            self.log(f"Primary attempt failed: {primary_error}", level="error")

        if self.cancel_requested:
            return

        # =====================================================
        # FALLBACK: best single/combined stream, any resolution
        # =====================================================

        try:
            self.log("Retrying with best overall compatible stream...", level="warn")
            self.after(0, lambda: self.status_label.configure(
                text="Primary format failed • trying best compatible stream..."
            ))

            fallback_output = os.path.join(self.download_folder, "%(title)s.%(ext)s")

            fallback_options = self.base_options()
            fallback_options.update({
                "format": "best",
                "outtmpl": fallback_output,
                "noplaylist": True,
                "continuedl": True,
                "overwrites": False,
                "retries": 20,
                "fragment_retries": 20,
                "extractor_retries": 8,
                "cachedir": False,
                "windowsfilenames": True,
            })

            with yt_dlp.YoutubeDL(fallback_options) as ydl:
                result = ydl.download([url])

            if self.cancel_requested:
                self.log("Download cancelled by user.", level="warn")
                return

            if result not in (None, 0):
                raise RuntimeError(f"Fallback returned exit status {result}")

            self.log("Download completed successfully via fallback.", level="ok")
            self.after(0, self.download_finished)
            return

        except Exception as fallback_error:
            fallback_text = str(fallback_error)
            self.log(f"Fallback also failed: {fallback_text}", level="error")

        self.after(
            0,
            lambda: self.download_error(primary_error or "Unknown error", fallback_text)
        )

    # =====================================================
    # PROGRESS
    # =====================================================

    def progress_hook(self, data):
        if self.cancel_requested:
            raise Exception("Download cancelled by user.")

        status = data.get("status")

        if status == "downloading":
            percent = data.get("_percent_float", 0) or 0
            speed = data.get("_speed_str", "--")
            eta = data.get("_eta_str", "--")

            total = (
                data.get("total_bytes")
                or data.get("total_bytes_estimate")
                or 0
            )

            try:
                percent = float(percent)
            except (TypeError, ValueError):
                percent = 0

            self.after(0, lambda p=percent: self.progress.set(max(0, min(1, p / 100))))
            self.after(0, lambda p=percent: self.status_label.configure(
                text=f"Downloading... {p:.1f}%"
            ))
            self.after(0, lambda s=speed, e=eta, t=total: self.stats_label.configure(
                text=f"Speed: {s}   |   ETA: {e}   |   Size: {human_size(t)}"
            ))

            # Throttle log spam: only log every ~10% step.
            step = int(percent // 10)
            last_step = getattr(self, "_last_logged_step", -1)
            if step != last_step:
                self._last_logged_step = step
                self.log(f"Progress {percent:.0f}% • speed {speed} • ETA {eta}")

        elif status == "finished":
            self._last_logged_step = -1
            self.log("Stream finished downloading, finalizing file (merging/converting)...")
            self.after(0, lambda: self.status_label.configure(
                text="Stream finished • finalizing file..."
            ))

    # =====================================================
    # CANCEL
    # =====================================================

    def cancel_download(self):
        self.cancel_requested = True
        self.status_label.configure(text="Cancelling...")
        self.log("Cancel requested by user.", level="warn")

    # =====================================================
    # ERROR
    # =====================================================

    def download_error(self, error, fallback_error=""):
        combined = f"{error}\n\nFallback:\n{fallback_error}"
        lower = combined.lower()

        if "login" in lower or "sign in" in lower or "private" in lower:
            message = (
                "This content requires you to be logged in.\n\n"
                "If you are authorized to access it, select the browser "
                "containing your logged-in session and retry.\n\n"
                "DRM/access-control protection cannot be bypassed."
            )
        elif "drm" in lower:
            message = (
                "This content is DRM-protected and cannot be downloaded.\n\n"
                "This applies to all yt-dlp based tools, not just this app — "
                "the decryption keys are withheld by design."
            )
        elif "ffmpeg" in lower:
            message = (
                "FFmpeg is required to merge separate video/audio streams "
                "for many high-quality downloads.\n\n"
                "Put ffmpeg.exe next to this program or add FFmpeg to PATH.\n\n"
                f"Details:\n{combined}"
            )
        elif "403" in lower:
            message = (
                "The server rejected the request (HTTP 403).\n\n"
                "The page may require authentication, the temporary media "
                "URL may have expired, or the site's extractor may need "
                "an update.\n\n"
                f"Details:\n{combined}"
            )
        elif "410" in lower or "gone" in lower:
            message = (
                "HTTP 410 Gone was returned.\n\n"
                "The requested resource may have expired or been removed.\n\n"
                f"Details:\n{combined}"
            )
        else:
            message = f"Download failed after automatic fallback attempts.\n\n{combined}"

        self.status_label.configure(text="Download failed")
        messagebox.showerror("Download Error", message)

    # =====================================================
    # FINISHED
    # =====================================================

    def download_finished(self):
        self.progress.set(1)
        self.status_label.configure(text="✓ Download completed successfully")
        self.stats_label.configure(text="Video saved successfully")

        self.download_button.configure(state="normal", text="DOWNLOAD")

        messagebox.showinfo(
            "Download Complete",
            f"The file has been saved to:\n{self.download_folder}"
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    app = VideoDownloader()
    app.mainloop()# =========================================================
# REQUIREMENTS
#   pip install yt-dlp customtkinter pillow requests beautifulsoup4 streamlink
#   FFmpeg must also be installed / placed next to this script for
#   merging separate video+audio streams into a single file.
#
# streamlink and requests/beautifulsoup4 are optional: if they are
# not installed, this app simply skips those fallback tiers and logs
# that they were skipped, instead of crashing.
# =========================================================

import os
import re
import sys
import time
import shutil
import threading
import datetime
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox
from io import BytesIO
from urllib.request import Request, urlopen
from urllib.parse import urlparse

import customtkinter as ctk
from PIL import Image
import yt_dlp


# =========================================================
# CONFIG
# =========================================================

APP_NAME = "FARA VIDEO DOWNLOADER"
DEFAULT_FOLDER = os.path.join(os.getcwd(), "downloads")
os.makedirs(DEFAULT_FOLDER, exist_ok=True)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# =========================================================
# HELPERS
# =========================================================

def clean_url(url):
    return url.strip()


def human_size(size):
    if not size:
        return "--"
    try:
        size = float(size)
    except Exception:
        return "--"

    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_duration(seconds):
    if not seconds:
        return "Unknown"
    try:
        seconds = int(seconds)
    except Exception:
        return "Unknown"

    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def find_ffmpeg():
    candidates = [
        shutil.which("ffmpeg"),
        os.path.join(os.getcwd(), "ffmpeg.exe"),
        os.path.join(os.getcwd(), "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(os.getcwd(), "bin", "ffmpeg.exe"),
    ]

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def timestamp():
    return datetime.datetime.now().strftime("%H:%M:%S")


def domain_of(url):
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/"
    except Exception:
        return None


# =========================================================
# LOGGER BRIDGE (yt-dlp -> UI log panel)
# =========================================================

class UILogger:
    """
    Bridges yt-dlp's internal logger to the on-screen log panel.
    Every debug/info/warning/error line yt-dlp emits gets pushed
    into the GUI so the user can see exactly what is happening
    (format negotiation, retries, merging, extractor messages, etc).
    """

    def __init__(self, app):
        self.app = app

    def debug(self, msg):
        # yt-dlp routes some "info" lines through debug() prefixed with [debug]
        if msg.startswith("[debug] "):
            return
        self.app.log(msg)

    def info(self, msg):
        self.app.log(msg)

    def warning(self, msg):
        self.app.log(f"WARNING: {msg}", level="warn")

    def error(self, msg):
        self.app.log(f"ERROR: {msg}", level="error")


# =========================================================
# APPLICATION
# =========================================================

class VideoDownloader(ctk.CTk):

    def __init__(self):
        super().__init__()

        self.title(APP_NAME)
        self.geometry("1200x860")
        self.minsize(1000, 720)

        self.video_info = None
        self.formats = []
        self.quality_options = []   # list of dicts: {label, height, format_id, kind}
        self.download_folder = DEFAULT_FOLDER
        self.cancel_requested = False
        self.ffmpeg_path = find_ffmpeg()

        self.build_ui()
        self.log(f"{APP_NAME} started.")
        self.log(f"FFmpeg: {'found at ' + self.ffmpeg_path if self.ffmpeg_path else 'NOT FOUND - merging high quality streams may fail'}")

    # =====================================================
    # UI
    # =====================================================

    def build_ui(self):

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ---------------- HEADER ----------------

        header = ctk.CTkFrame(self, height=75, corner_radius=0)
        header.grid(row=0, column=0, columnspan=2, sticky="nsew")

        title = ctk.CTkLabel(
            header,
            text=APP_NAME,
            font=("Segoe UI", 25, "bold")
        )
        title.pack(side="left", padx=30)

        subtitle = ctk.CTkLabel(
            header,
            text="Universal yt-dlp downloader — any site yt-dlp supports",
            font=("Segoe UI", 13)
        )
        subtitle.pack(side="left")

        # ---------------- SIDEBAR ----------------

        sidebar = ctk.CTkFrame(self, width=260, corner_radius=0)
        sidebar.grid(row=1, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        sidebar_title = ctk.CTkLabel(
            sidebar,
            text="DOWNLOAD",
            font=("Segoe UI", 15, "bold")
        )
        sidebar_title.pack(anchor="w", padx=25, pady=(30, 15))

        self.folder_button = ctk.CTkButton(
            sidebar,
            text="📁 Download Folder",
            height=42,
            command=self.choose_folder
        )
        self.folder_button.pack(padx=20, pady=8, fill="x")

        self.folder_label = ctk.CTkLabel(
            sidebar,
            text=self.download_folder,
            wraplength=210,
            justify="left",
            font=("Segoe UI", 11)
        )
        self.folder_label.pack(padx=20, pady=5)

        self.ffmpeg_status = (
            "Available" if self.ffmpeg_path else "Not Found"
        )

        self.engine_label = ctk.CTkLabel(
            sidebar,
            text=(
                "Engine: yt-dlp\n"
                f"FFmpeg: {self.ffmpeg_status}\n\n"
                "Automatic quality:\n"
                "Highest available quality\n"
                "is used when nothing is\n"
                "explicitly selected."
            ),
            wraplength=215,
            justify="left",
            font=("Segoe UI", 11)
        )
        self.engine_label.pack(padx=20, pady=18)

        self.cookie_label = ctk.CTkLabel(
            sidebar,
            text=(
                "Content you're logged into:\n"
                "Pick your browser below to\n"
                "reuse its session cookies.\n\n"
                "Note: this only works for\n"
                "content your account can\n"
                "already open in that browser.\n"
                "DRM-protected or paywalled\n"
                "streams cannot be unlocked."
            ),
            wraplength=215,
            justify="left",
            font=("Segoe UI", 11)
        )
        self.cookie_label.pack(side="bottom", padx=20, pady=(0, 10))

        self.update_button = ctk.CTkButton(
            sidebar,
            text="⟳ Update yt-dlp",
            height=36,
            fg_color="transparent",
            border_width=1,
            command=self.update_ytdlp
        )
        self.update_button.pack(side="bottom", padx=20, pady=(0, 10), fill="x")

        # ---------------- MAIN ----------------

        main = ctk.CTkFrame(self, corner_radius=0)
        main.grid(row=1, column=1, sticky="nsew", padx=15, pady=15)

        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(11, weight=1)

        # URL

        url_label = ctk.CTkLabel(
            main,
            text="Video URL",
            font=("Segoe UI", 15, "bold")
        )
        url_label.grid(row=0, column=0, sticky="w", padx=25, pady=(20, 6))

        url_frame = ctk.CTkFrame(main, fg_color="transparent")
        url_frame.grid(row=1, column=0, sticky="ew", padx=25)
        url_frame.grid_columnconfigure(0, weight=1)

        self.url_entry = ctk.CTkEntry(
            url_frame,
            height=45,
            placeholder_text="Paste any video URL here (YouTube, Instagram, TikTok, X/Twitter, Vimeo, etc.)"
        )
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.url_entry.bind("<Control-v>", self.handle_paste)
        self.url_entry.bind("<Button-3>", self.show_context_menu)

        self.paste_button = ctk.CTkButton(
            url_frame,
            text="📋 Paste",
            width=100,
            height=45,
            command=self.paste_url
        )
        self.paste_button.grid(row=0, column=1, padx=(0, 8))

        self.analyze_button = ctk.CTkButton(
            url_frame,
            text="Analyze",
            width=130,
            height=45,
            command=self.analyze
        )
        self.analyze_button.grid(row=0, column=2)

        # OPTIONS

        options_frame = ctk.CTkFrame(main, fg_color="transparent")
        options_frame.grid(row=2, column=0, sticky="ew", padx=25, pady=(12, 0))
        options_frame.grid_columnconfigure(3, weight=1)

        self.browser_var = tk.StringVar(value="None")

        ctk.CTkLabel(
            options_frame,
            text="Use my session from:"
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))

        self.browser_combo = ctk.CTkComboBox(
            options_frame,
            values=["None", "Chrome", "Edge", "Firefox", "Brave", "Opera"],
            variable=self.browser_var,
            width=140
        )
        self.browser_combo.grid(row=0, column=1, sticky="w")

        self.audio_only_var = tk.BooleanVar(value=False)
        self.audio_only_check = ctk.CTkCheckBox(
            options_frame,
            text="Audio only (extract best audio)",
            variable=self.audio_only_var
        )
        self.audio_only_check.grid(row=0, column=2, sticky="w", padx=25)

        # VIDEO INFO

        self.info_frame = ctk.CTkFrame(main, height=165)
        self.info_frame.grid(
            row=3, column=0, sticky="ew",
            padx=25, pady=15
        )

        self.thumbnail = ctk.CTkLabel(
            self.info_frame,
            text="No Preview",
            width=220,
            height=125
        )
        self.thumbnail.pack(side="left", padx=20, pady=20)

        details = ctk.CTkFrame(
            self.info_frame,
            fg_color="transparent"
        )
        details.pack(
            side="left",
            fill="both",
            expand=True,
            padx=10
        )

        self.title_label = ctk.CTkLabel(
            details,
            text="Video title will appear here",
            font=("Segoe UI", 18, "bold"),
            anchor="w",
            justify="left",
            wraplength=680
        )
        self.title_label.pack(anchor="w", pady=(20, 6))

        self.duration_label = ctk.CTkLabel(details, text="Duration: --")
        self.duration_label.pack(anchor="w")

        self.source_label = ctk.CTkLabel(details, text="Source: --")
        self.source_label.pack(anchor="w")

        self.formats_label = ctk.CTkLabel(details, text="Formats: --")
        self.formats_label.pack(anchor="w")

        # QUALITY

        quality_label = ctk.CTkLabel(
            main,
            text="Available Quality",
            font=("Segoe UI", 15, "bold")
        )
        quality_label.grid(row=4, column=0, sticky="w", padx=25, pady=(2, 6))

        self.quality_combo = ctk.CTkComboBox(
            main,
            values=["Analyze a video first"],
            height=42
        )
        self.quality_combo.grid(row=5, column=0, sticky="ew", padx=25)
        self.quality_combo.set("Automatic — Highest Available")

        # DOWNLOAD

        action_frame = ctk.CTkFrame(main, fg_color="transparent")
        action_frame.grid(row=6, column=0, sticky="ew", padx=25, pady=16)
        action_frame.grid_columnconfigure(0, weight=1)

        self.download_button = ctk.CTkButton(
            action_frame,
            text="DOWNLOAD",
            height=50,
            font=("Segoe UI", 15, "bold"),
            command=self.start_download
        )
        self.download_button.grid(row=0, column=0, sticky="ew")

        self.cancel_button = ctk.CTkButton(
            action_frame,
            text="Cancel",
            width=120,
            height=50,
            fg_color="transparent",
            border_width=1,
            command=self.cancel_download
        )
        self.cancel_button.grid(row=0, column=1, padx=(10, 0))

        # PROGRESS

        self.progress = ctk.CTkProgressBar(main, height=12)
        self.progress.grid(row=7, column=0, sticky="ew", padx=25)
        self.progress.set(0)

        # STATUS

        self.status_label = ctk.CTkLabel(
            main,
            text="Ready",
            font=("Segoe UI", 12, "bold")
        )
        self.status_label.grid(row=8, column=0, sticky="w", padx=25, pady=(10, 2))

        self.stats_label = ctk.CTkLabel(
            main,
            text="Speed: --   |   ETA: --   |   Size: --"
        )
        self.stats_label.grid(row=9, column=0, sticky="w", padx=25)

        # LIVE LOG PANEL

        log_label = ctk.CTkLabel(
            main,
            text="Live Log",
            font=("Segoe UI", 13, "bold")
        )
        log_label.grid(row=10, column=0, sticky="w", padx=25, pady=(14, 4))

        log_frame = ctk.CTkFrame(main)
        log_frame.grid(row=11, column=0, sticky="nsew", padx=25, pady=(0, 10))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)

        self.log_box = ctk.CTkTextbox(
            log_frame,
            font=("Consolas", 11),
            wrap="word"
        )
        self.log_box.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.log_box.configure(state="disabled")

        clear_log_btn = ctk.CTkButton(
            log_frame,
            text="Clear Log",
            width=100,
            height=28,
            command=self.clear_log
        )
        clear_log_btn.grid(row=1, column=0, sticky="e", padx=8, pady=(0, 8))

    # =====================================================
    # LOG PANEL
    # =====================================================

    def log(self, message, level="info"):
        """
        Thread-safe append to the on-screen live log.
        Safe to call from any worker thread.
        """
        prefix = {
            "info": "•",
            "warn": "⚠",
            "error": "✖",
            "ok": "✓",
        }.get(level, "•")

        line = f"[{timestamp()}] {prefix} {message}\n"

        def append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        if threading.current_thread() is threading.main_thread():
            append()
        else:
            self.after(0, append)

    def clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    # =====================================================
    # PASTE
    # =====================================================

    def paste_url(self):
        try:
            clipboard = self.clipboard_get().strip()
            if not clipboard:
                raise tk.TclError

            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, clipboard)
            self.url_entry.focus()

            self.status_label.configure(text="URL pasted successfully")
            self.log("URL pasted from clipboard.")

        except tk.TclError:
            self.status_label.configure(text="Clipboard is empty")

    def handle_paste(self, event=None):
        self.paste_url()
        return "break"

    def show_context_menu(self, event):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Paste", command=self.paste_url)
        menu.add_command(
            label="Clear",
            command=lambda: self.url_entry.delete(0, "end")
        )
        menu.tk_popup(event.x_root, event.y_root)

    # =====================================================
    # FOLDER
    # =====================================================

    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.download_folder = folder
            self.folder_label.configure(text=folder)
            self.log(f"Download folder set to: {folder}")

    # =====================================================
    # UPDATE YT-DLP
    # =====================================================

    def update_ytdlp(self):
        """
        Most 'this site stopped working' cases are fixed by a newer
        yt-dlp release, not by changing this app's code. This pulls
        the latest version in the background.
        """
        self.update_button.configure(state="disabled", text="Updating...")
        self.log("Updating yt-dlp to the latest version...")
        threading.Thread(target=self._update_ytdlp_worker, daemon=True).start()

    def _update_ytdlp_worker(self):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                self.log("yt-dlp updated successfully. Restart the app to use it.", level="ok")
            else:
                self.log(f"yt-dlp update failed: {result.stderr.strip()[:300]}", level="error")
        except Exception as e:
            self.log(f"Could not run yt-dlp updater: {e}", level="error")
        finally:
            self.after(0, lambda: self.update_button.configure(
                state="normal", text="⟳ Update yt-dlp"
            ))

    # =====================================================
    # YT-DLP OPTIONS
    # =====================================================

    def base_options(self, url=None, force_generic=False):
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }

        # Many hosts (CDNs, hotlink-protected media) reject requests
        # without a matching Referer header. Setting it to the page's
        # own domain fixes a large share of otherwise-403 failures.
        if url:
            ref = domain_of(url)
            if ref:
                headers["Referer"] = ref

        options = {
            "quiet": True,
            "no_warnings": False,
            "noplaylist": True,
            "logger": UILogger(self),

            # Robust retry behavior — helps with flaky hosts / rate limits.
            "retries": 20,
            "fragment_retries": 20,
            "extractor_retries": 8,
            "file_access_retries": 8,
            "socket_timeout": 30,

            "continuedl": True,
            "concurrent_fragment_downloads": 4,

            # Ignore region locks where the extractor supports it
            # (standard yt-dlp feature — not a DRM bypass).
            "geo_bypass": True,

            # Some smaller/self-hosted sites present broken or
            # self-signed certificates; don't hard-fail on that alone.
            "nocheckcertificate": True,

            # Do not reuse stale temporary URLs.
            "cachedir": False,

            "http_headers": headers,

            "progress_hooks": [self.progress_hook],
        }

        if force_generic:
            # Last-resort mode: ignore site-specific extractors and try
            # to pull any playable media (mp4/m3u8/mpd) directly out of
            # the page HTML. Helps with obscure/unsupported sites.
            options["force_generic_extractor"] = True

        if self.ffmpeg_path:
            options["ffmpeg_location"] = os.path.dirname(self.ffmpeg_path)

        browser = self.browser_var.get()
        if browser and browser != "None":
            browser_map = {
                "Chrome": "chrome",
                "Edge": "edge",
                "Firefox": "firefox",
                "Brave": "brave",
                "Opera": "opera",
            }
            options["cookiesfrombrowser"] = (browser_map[browser],)
            self.log(f"Using cookies from your {browser} session.")

        return options

    # =====================================================
    # ANALYZE
    # =====================================================

    def analyze(self):
        url = clean_url(self.url_entry.get())

        if not url:
            messagebox.showwarning("Missing URL", "Please paste or enter a video URL.")
            return

        self.video_info = None
        self.formats = []
        self.quality_options = []

        self.analyze_button.configure(state="disabled", text="Analyzing...")
        self.status_label.configure(text="Extracting video information...")
        self.log(f"Analyzing URL: {url}")

        self.quality_combo.configure(values=["Analyzing..."])
        self.quality_combo.set("Analyzing...")

        threading.Thread(target=self.analyze_worker, args=(url,), daemon=True).start()

    def analyze_worker(self, url):
        info = None
        last_error = None

        # Tier 1: normal, site-specific extraction.
        try:
            options = self.base_options(url=url)
            options.update({"skip_download": True, "extract_flat": False})

            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)

        except Exception as e:
            last_error = str(e)
            self.log(f"Standard analysis failed: {last_error}", level="warn")

        # Tier 2: generic extractor fallback — for sites without a
        # dedicated yt-dlp extractor, this tries to pull direct media
        # links (mp4/m3u8/mpd) straight out of the page HTML.
        if not info:
            try:
                self.log("Trying generic extractor fallback...", level="warn")
                options = self.base_options(url=url, force_generic=True)
                options.update({"skip_download": True, "extract_flat": False})

                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(url, download=False)

            except Exception as e:
                last_error = str(e)
                self.log(f"Generic extractor also failed: {last_error}", level="error")

        try:
            if not info:
                raise RuntimeError(last_error or "yt-dlp returned no video information.")

            # Some extractors (playlists mistakenly matched, etc.) return
            # an 'entries' list even with noplaylist — take the first entry.
            if "entries" in info and info.get("entries"):
                entries = [e for e in info["entries"] if e]
                if entries:
                    info = entries[0]

            self.video_info = info
            self.formats = info.get("formats") or []
            self.quality_options = self.build_quality_options(self.formats)

            self.log(f"Extractor used: {info.get('extractor', 'unknown')}")
            self.log(f"Found {len(self.formats)} raw format(s), "
                      f"{len(self.quality_options)} selectable quality option(s).")

            self.after(0, self.show_video_info)

        except Exception as e:
            error_text = str(e)
            self.log(f"Analysis failed: {error_text}", level="error")
            self.after(0, lambda err=error_text: self.analysis_error(err))

        finally:
            self.after(
                0,
                lambda: self.analyze_button.configure(state="normal", text="Analyze")
            )

    def build_quality_options(self, formats):
        """
        Build a clean, de-duplicated list of quality choices,
        highest first, based on real available heights.
        Falls back gracefully when no explicit video heights exist
        (e.g. audio-only sources, some live streams).
        """
        seen_heights = {}

        for f in formats:
            height = f.get("height")
            vcodec = f.get("vcodec")
            if not height or not vcodec or vcodec == "none":
                continue
            try:
                height = int(height)
            except (TypeError, ValueError):
                continue

            fps = f.get("fps")
            label = f"{height}p" + (f"{int(fps)}" if fps and fps > 30 else "")

            # Keep the best (largest) entry we've seen for this label.
            if label not in seen_heights or height > seen_heights[label]["height"]:
                seen_heights[label] = {"label": label, "height": height}

        options = sorted(seen_heights.values(), key=lambda x: x["height"], reverse=True)
        return options

    def analysis_error(self, error):
        self.status_label.configure(text="Analysis failed")

        lower = error.lower()

        if "login" in lower or "sign in" in lower or "private" in lower:
            message = (
                "This content requires you to be logged in.\n\n"
                "If it's your own account or you're otherwise authorized, "
                "pick your browser under 'Use my session from' and try again.\n\n"
                "This program cannot bypass DRM, paywalls, or access controls "
                "you don't already have permission for."
            )
        elif "drm" in lower:
            message = (
                "This content is DRM-protected.\n\n"
                "DRM-protected streams (widevine, playready, fairplay, etc.) "
                "cannot be downloaded by this or any yt-dlp based tool — "
                "the decryption keys are intentionally withheld by the platform."
            )
        elif "403" in lower:
            message = (
                "The server rejected the request (HTTP 403).\n\n"
                "Update yt-dlp, verify the URL is current, or use your own "
                "authenticated browser session if the content requires login."
            )
        elif "410" in lower or "gone" in lower:
            message = (
                "The server returned HTTP 410 Gone.\n\n"
                "The resource may no longer exist, or the site's extractor "
                "may need an update."
            )
        elif "unsupported url" in lower:
            message = (
                "This site or URL format isn't recognized by yt-dlp.\n\n"
                "Double check the URL, or the site may not be supported yet."
            )
        else:
            message = f"Could not analyze this URL.\n\n{error}"

        messagebox.showerror("Analysis Error", message)

    # SHOW INFO

    def show_video_info(self):
        info = self.video_info or {}

        title = info.get("title") or "Unknown title"
        duration = info.get("duration")
        uploader = info.get("uploader") or info.get("channel") or "Unknown"

        self.title_label.configure(text=title)
        self.duration_label.configure(text=f"Duration: {format_duration(duration)}")
        self.source_label.configure(text=f"Source: {uploader}")

        if self.quality_options:
            quality_text = ", ".join(o["label"] for o in self.quality_options)
            self.formats_label.configure(text=f"Formats: {quality_text}")

            quality_values = ["Automatic — Highest Available"] + [
                o["label"] for o in self.quality_options
            ]

            self.quality_combo.configure(values=quality_values)
            self.quality_combo.set("Automatic — Highest Available")

            highest = self.quality_options[0]["label"]
            self.status_label.configure(
                text=(
                    f"Video analyzed • {len(self.quality_options)} "
                    f"quality level(s) • Highest: {highest}"
                )
            )
            self.log(f"Ready to download. Highest quality available: {highest}", level="ok")

        else:
            self.formats_label.configure(text="Formats: No explicit resolution list")
            self.quality_combo.configure(values=["Automatic — Best Available"])
            self.quality_combo.set("Automatic — Best Available")
            self.status_label.configure(
                text="No explicit qualities listed • automatic mode enabled"
            )
            self.log("No explicit quality list found — will grab the best stream automatically.")

        thumbnail_url = info.get("thumbnail")
        if thumbnail_url:
            threading.Thread(
                target=self.load_thumbnail, args=(thumbnail_url,), daemon=True
            ).start()

    # THUMBNAIL

    def load_thumbnail(self, url):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
                    )
                }
            )

            data = urlopen(request, timeout=15).read()
            image = Image.open(BytesIO(data)).convert("RGB")
            image.thumbnail((220, 125))

            photo = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)

            self.after(0, lambda p=photo: self.thumbnail.configure(image=p, text=""))

        except Exception as e:
            self.log(f"Could not load thumbnail: {e}", level="warn")

    # QUALITY

    def get_selected_height(self):
        selection = self.quality_combo.get()

        if selection.startswith("Automatic"):
            return None

        match = re.search(r"(\d+)p", selection)
        if not match:
            return None

        try:
            return int(match.group(1))
        except ValueError:
            return None

    def build_format_selector(self, selected_height, audio_only):

        if audio_only:
            return "bestaudio/best"

        if selected_height is None:
            return "bestvideo*+bestaudio/best"

        return (
            f"bestvideo*[height<={selected_height}]+bestaudio/"
            f"best[height<={selected_height}]/"
            "best"
        )

    # DOWNLOAD

    def start_download(self):
        url = clean_url(self.url_entry.get())

        if not url:
            messagebox.showwarning("Missing URL", "Please enter a video URL.")
            return

        selected_height = self.get_selected_height()
        audio_only = self.audio_only_var.get()

        self.cancel_requested = False
        self.progress.set(0)

        self.download_button.configure(state="disabled", text="DOWNLOADING...")
        self.status_label.configure(text="Refreshing formats...")
        self.log(f"Starting download for: {url}")
        if audio_only:
            self.log("Mode: audio only")
        elif selected_height:
            self.log(f"Requested quality: up to {selected_height}p")
        else:
            self.log("Requested quality: automatic (highest available)")

        threading.Thread(
            target=self.download_worker,
            args=(url, selected_height, audio_only),
            daemon=True
        ).start()

    def download_worker(self, url, selected_height, audio_only):

        errors = []

        try:
            self.log("Tier 1: yt-dlp (site-specific extractor)...")
            if self.run_ytdlp_download(url, selected_height, audio_only, force_generic=False):
                self.log("Download completed successfully (Tier 1).", level="ok")
                self.after(0, self.download_finished)
                return
        except Exception as e:
            msg = str(e)
            errors.append(f"Tier 1 (yt-dlp): {msg}")
            self.log(f"Tier 1 failed: {msg}", level="warn")

        if self.cancel_requested:
            self.log("Download cancelled by user.", level="warn")
            return

        try:
            self.log("Tier 2: yt-dlp (generic extractor — scanning page HTML)...", level="warn")
            if self.run_ytdlp_download(url, selected_height, audio_only, force_generic=True):
                self.log("Download completed successfully (Tier 2 - generic).", level="ok")
                self.after(0, self.download_finished)
                return
        except Exception as e:
            msg = str(e)
            errors.append(f"Tier 2 (generic extractor): {msg}")
            self.log(f"Tier 2 failed: {msg}", level="warn")

        if self.cancel_requested:
            self.log("Download cancelled by user.", level="warn")
            return

        try:
            self.log("Tier 3: streamlink (alternate stream engine)...", level="warn")
            if self.run_streamlink_download(url):
                self.log("Download completed successfully (Tier 3 - streamlink).", level="ok")
                self.after(0, self.download_finished)
                return
        except Exception as e:
            msg = str(e)
            errors.append(f"Tier 3 (streamlink): {msg}")
            self.log(f"Tier 3 failed: {msg}", level="warn")

        if self.cancel_requested:
            self.log("Download cancelled by user.", level="warn")
            return

        
        try:
            self.log("Tier 4: scanning raw page HTML for a direct media URL...", level="warn")
            if self.run_raw_scrape_download(url):
                self.log("Download completed successfully (Tier 4 - direct link).", level="ok")
                self.after(0, self.download_finished)
                return
        except Exception as e:
            msg = str(e)
            errors.append(f"Tier 4 (raw scrape): {msg}")
            self.log(f"Tier 4 failed: {msg}", level="error")

        combined = "\n\n".join(errors) if errors else "Unknown error"
        self.after(0, lambda: self.download_error(combined))

    # TIER 1 / 2 — yt-dlp (normal or generic extractor)

    def run_ytdlp_download(self, url, selected_height, audio_only, force_generic=False):
        self.after(0, lambda: self.status_label.configure(
            text="Checking current video formats..."
        ))

        extract_options = self.base_options(url=url, force_generic=force_generic)
        extract_options.update({"skip_download": True})

        with yt_dlp.YoutubeDL(extract_options) as ydl:
            fresh_info = ydl.extract_info(url, download=False)

        if not fresh_info:
            raise RuntimeError("Could not extract video information.")

        if "entries" in fresh_info and fresh_info.get("entries"):
            entries = [e for e in fresh_info["entries"] if e]
            if entries:
                fresh_info = entries[0]

        # If a requested quality no longer exists, fall back to the
        # highest currently available quality not exceeding it.
        if selected_height is not None and not audio_only:
            available = []
            for f in fresh_info.get("formats") or []:
                h = f.get("height")
                vc = f.get("vcodec")
                if h and vc and vc != "none":
                    try:
                        h = int(h)
                        if h <= selected_height:
                            available.append(h)
                    except (TypeError, ValueError):
                        pass

            if available:
                new_height = max(available)
                if new_height != selected_height:
                    self.log(
                        f"Requested {selected_height}p no longer available, "
                        f"using {new_height}p instead.",
                        level="warn"
                    )
                selected_height = new_height
            else:
                self.log(
                    "Requested quality no longer available, "
                    "switching to automatic best.",
                    level="warn"
                )
                selected_height = None

        format_selector = self.build_format_selector(selected_height, audio_only)
        self.log(f"Format selector: {format_selector}")

        output = os.path.join(self.download_folder, "%(title)s.%(ext)s")

        options = self.base_options(url=url, force_generic=force_generic)
        options.update({
            "format": format_selector,
            "outtmpl": output,
            "noplaylist": True,
            "overwrites": False,
            "continuedl": True,
            "retries": 20,
            "fragment_retries": 20,
            "extractor_retries": 8,
            "file_access_retries": 8,
            "retry_sleep": {
                "http": [1, 2, 4, 8, 15],
                "fragment": [1, 2, 4, 8, 15],
            },
            "windowsfilenames": True,
            "cachedir": False,
        })

        if audio_only:
            options["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            }]
        else:
            options["merge_output_format"] = "mp4"

        self.after(0, lambda: self.status_label.configure(
            text=(
                "Downloading audio..." if audio_only else
                "Downloading highest available quality..." if selected_height is None
                else f"Downloading up to {selected_height}p..."
            )
        ))

        with yt_dlp.YoutubeDL(options) as ydl:
            result = ydl.download([url])

        if self.cancel_requested:
            return False

        if result not in (None, 0):
            raise RuntimeError(f"yt-dlp returned exit status {result}")

        return True

    # TIER 3 — streamlink
    
    def run_streamlink_download(self, url):
        try:
            import streamlink
        except ImportError:
            self.log("streamlink is not installed — skipping this tier "
                      "(pip install streamlink to enable it).", level="warn")
            return False

        self.after(0, lambda: self.status_label.configure(
            text="Trying streamlink engine..."
        ))

        session = streamlink.Streamlink()
        try:
            streams = session.streams(url)
        except Exception as e:
            raise RuntimeError(f"streamlink could not read this URL: {e}")

        if not streams:
            raise RuntimeError("streamlink found no playable streams on this page.")

        # Prefer the best quality streamlink found.
        stream = streams.get("best") or list(streams.values())[0]
        self.log(f"streamlink found {len(streams)} stream(s), using best.")

        title = self.video_info.get("title") if self.video_info else None
        title = title or "video"
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)
        out_path = os.path.join(self.download_folder, f"{safe_title}.mp4")

        fd = stream.open()
        total_written = 0
        try:
            with open(out_path, "wb") as out_file:
                while True:
                    if self.cancel_requested:
                        raise Exception("Download cancelled by user.")
                    chunk = fd.read(1024 * 256)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    total_written += len(chunk)
                    self.after(0, lambda t=total_written: self.stats_label.configure(
                        text=f"Speed: --   |   ETA: --   |   Size: {human_size(t)}"
                    ))
        finally:
            fd.close()

        if total_written == 0:
            raise RuntimeError("streamlink produced an empty file.")

        return True

    # TIER 4 — raw HTML scrape requests + BeautifulSoup

    def run_raw_scrape_download(self, url):
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError:
            self.log("requests/beautifulsoup4 not installed — skipping this tier "
                      "(pip install requests beautifulsoup4 to enable it).", level="warn")
            return False

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
            ),
            "Referer": domain_of(url) or url,
        }

        self.log("Fetching raw page HTML...")
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        candidates = []

        # <video src="..."> and <video><source src="..."></video>
        for video_tag in soup.find_all("video"):
            if video_tag.get("src"):
                candidates.append(video_tag["src"])
            for source_tag in video_tag.find_all("source"):
                if source_tag.get("src"):
                    candidates.append(source_tag["src"])

        # og:video meta tags (used by most social platforms for previews)
        for meta_name in ("og:video", "og:video:url", "og:video:secure_url"):
            tag = soup.find("meta", property=meta_name)
            if tag and tag.get("content"):
                candidates.append(tag["content"])

        # Raw .mp4 / .m3u8 / .mpd URLs anywhere in the page source
        raw_links = re.findall(
            r'https?://[^\s"\'<>]+\.(?:mp4|m3u8|mpd)(?:\?[^\s"\'<>]*)?',
            resp.text
        )
        candidates.extend(raw_links)

        # De-duplicate while preserving order.
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c and c not in seen:
                seen.add(c)
                unique_candidates.append(c)

        if not unique_candidates:
            raise RuntimeError("No direct media URL found in the page HTML.")

        media_url = unique_candidates[0]
        self.log(f"Found candidate media URL: {media_url}")

        # If it's an HLS/DASH manifest, hand it back to yt-dlp — it
        # already knows how to download and mux those correctly.
        if media_url.endswith(".m3u8") or media_url.endswith(".mpd"):
            self.log("Candidate is a streaming manifest — downloading via yt-dlp.")
            output = os.path.join(self.download_folder, "%(title)s.%(ext)s")
            options = self.base_options(url=url)
            options.update({
                "format": "best",
                "outtmpl": output,
                "merge_output_format": "mp4",
            })
            with yt_dlp.YoutubeDL(options) as ydl:
                result = ydl.download([media_url])
            return result in (None, 0)

        # Otherwise download the file directly over plain HTTP.
        title = self.video_info.get("title") if self.video_info else None
        title = title or "video"
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)
        ext = os.path.splitext(media_url.split("?")[0])[1] or ".mp4"
        out_path = os.path.join(self.download_folder, f"{safe_title}{ext}")

        with requests.get(media_url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            written = 0
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if self.cancel_requested:
                        raise Exception("Download cancelled by user.")
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
                        pct = (written / total * 100) if total else 0
                        self.after(0, lambda p=pct: self.progress.set(max(0, min(1, p / 100))))
                        self.after(0, lambda w=written, t=total: self.stats_label.configure(
                            text=f"Speed: --   |   ETA: --   |   Size: {human_size(w)}"
                            + (f" / {human_size(t)}" if t else "")
                        ))

        if written == 0:
            raise RuntimeError("Downloaded file was empty.")

        return True

    # PROGRESS

    def progress_hook(self, data):
        if self.cancel_requested:
            raise Exception("Download cancelled by user.")

        status = data.get("status")

        if status == "downloading":
            percent = data.get("_percent_float", 0) or 0
            speed = data.get("_speed_str", "--")
            eta = data.get("_eta_str", "--")

            total = (
                data.get("total_bytes")
                or data.get("total_bytes_estimate")
                or 0
            )

            try:
                percent = float(percent)
            except (TypeError, ValueError):
                percent = 0

            self.after(0, lambda p=percent: self.progress.set(max(0, min(1, p / 100))))
            self.after(0, lambda p=percent: self.status_label.configure(
                text=f"Downloading... {p:.1f}%"
            ))
            self.after(0, lambda s=speed, e=eta, t=total: self.stats_label.configure(
                text=f"Speed: {s}   |   ETA: {e}   |   Size: {human_size(t)}"
            ))

            # Throttle log spam: only log every ~10% step.
            step = int(percent // 10)
            last_step = getattr(self, "_last_logged_step", -1)
            if step != last_step:
                self._last_logged_step = step
                self.log(f"Progress {percent:.0f}% • speed {speed} • ETA {eta}")

        elif status == "finished":
            self._last_logged_step = -1
            self.log("Stream finished downloading, finalizing file (merging/converting)...")
            self.after(0, lambda: self.status_label.configure(
                text="Stream finished • finalizing file..."
            ))

    # CANCEL

    def cancel_download(self):
        self.cancel_requested = True
        self.status_label.configure(text="Cancelling...")
        self.log("Cancel requested by user.", level="warn")

    # ERROR

    def download_error(self, error, fallback_error=None):
        combined = error if fallback_error is None else f"{error}\n\nFallback:\n{fallback_error}"
        lower = combined.lower()

        if "login" in lower or "sign in" in lower or "private" in lower:
            message = (
                "This content requires you to be logged in.\n\n"
                "If you are authorized to access it, select the browser "
                "containing your logged-in session and retry.\n\n"
                "DRM/access-control protection cannot be bypassed."
            )
        elif "drm" in lower:
            message = (
                "This content is DRM-protected and cannot be downloaded.\n\n"
                "This applies to all yt-dlp based tools, not just this app — "
                "the decryption keys are withheld by design."
            )
        elif "ffmpeg" in lower:
            message = (
                "FFmpeg is required to merge separate video/audio streams "
                "for many high-quality downloads.\n\n"
                "Put ffmpeg.exe next to this program or add FFmpeg to PATH.\n\n"
                f"Details:\n{combined}"
            )
        elif "403" in lower:
            message = (
                "The server rejected the request (HTTP 403).\n\n"
                "The page may require authentication, the temporary media "
                "URL may have expired, or the site's extractor may need "
                "an update.\n\n"
                f"Details:\n{combined}"
            )
        elif "410" in lower or "gone" in lower:
            message = (
                "HTTP 410 Gone was returned.\n\n"
                "The requested resource may have expired or been removed.\n\n"
                f"Details:\n{combined}"
            )
        else:
            message = (
                "All 4 download methods failed (yt-dlp, generic extractor, "
                "streamlink, direct HTML scrape).\n\n"
                f"Details:\n{combined}"
            )

        self.status_label.configure(text="Download failed")
        messagebox.showerror("Download Error", message)

    # FINISHED

    def download_finished(self):
        self.progress.set(1)
        self.status_label.configure(text="✓ Download completed successfully")
        self.stats_label.configure(text="Video saved successfully")

        self.download_button.configure(state="normal", text="DOWNLOAD")

        messagebox.showinfo(
            "Download Complete",
            f"The file has been saved to:\n{self.download_folder}"
        )

if __name__ == "__main__":
    app = VideoDownloader()
    app.mainloop()
