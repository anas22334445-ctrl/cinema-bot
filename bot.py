
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from flask import Flask
import requests
import telebot
from telebot import types
import yt_dlp

BOT_TOKEN = os.getenv("BOT_TOKEN", "8956783825:AAHS01rPwSdVztYF-k1jfdk9zGIBnfTqcgw")
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else None

DATA_FILE = "series_state.json"
DOWNLOAD_DIR = "downloads"
DEFAULT_COVER_FILE = "cover.jpg"

MAX_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
MAX_THUMB_SOURCE_BYTES = 750 * 1024 * 1024
CHUNK_SIZE = 1024 * 512  # 512 KB

FALLBACK_HEIGHTS = [1080, 720, 480, 360, 240]

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
state_lock = threading.Lock()
pending_tasks = {}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

VIDEO_EXT_MAP = {
    "video/mp4": ".mp4",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/mp2t": ".ts",
}


class FileTooLargeError(Exception):
    pass


@app.route("/")
def home():
    return "Direct Video Bot (Multi-Quality Buttons + 2GB) is alive!", 200


def safe_send_message(chat_id, text, parse_mode=None, reply_to_message_id=None, reply_markup=None):
    try:
        return bot.send_message(
            chat_id,
            text,
            parse_mode=parse_mode,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
        )
    except Exception:
        try:
            return bot.send_message(
                chat_id,
                text,
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
            )
        except Exception:
            return None


def safe_edit_message(chat_id, message_id, text, parse_mode=None, reply_markup=None):
    try:
        return bot.edit_message_text(
            text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
    except Exception:
        try:
            return bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
        except Exception:
            return None


def load_state():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "series": data.get("series", "أمنية حب"),
                    "next_ep": int(data.get("next_ep", 1)),
                    "quality": data.get("quality", "720p"),
                }
        except Exception:
            pass
    return {
        "series": "أمنية حب",
        "next_ep": 1,
        "quality": "720p",
    }


def save_state(state_data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(state_data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


state = load_state()


def is_authorized_user(user_id):
    if ADMIN_ID is None:
        return True
    return int(user_id) == ADMIN_ID


def is_authorized_message(message):
    return bool(message.from_user and is_authorized_user(message.from_user.id))


def extract_urls_from_text(text):
    if not text:
        return []
    found = re.findall(r"https?://[^\s<>\"']+", text.strip())
    cleaned = []
    for u in found:
        u = u.rstrip(".,);!?]}>")
        if u and u not in cleaned:
            cleaned.append(u)
    return cleaned


def expand_url(url):
    try:
        resp = requests.get(
            url, headers=BROWSER_HEADERS, allow_redirects=True, stream=True, timeout=20
        )
        final_url = resp.url or url
        content_type = (resp.headers.get("Content-Type") or "").lower()
        content_len_raw = resp.headers.get("Content-Length")
        content_len = (
            int(content_len_raw)
            if content_len_raw and content_len_raw.isdigit()
            else 0
        )
        resp.close()
        return final_url, content_type, content_len
    except Exception:
        return url, "", 0


def format_ep(num):
    num_int = int(num)
    return f"{num_int:02d}" if num_int < 100 else str(num_int)


def format_duration_mins(seconds):
    if not seconds or seconds <= 0:
        return None
    mins = max(1, round(float(seconds) / 60.0))
    return f"{mins} د"


def build_caption(series_name, ep_num, quality, duration_sec=None):
    base = f"{series_name} — الحلقة ({format_ep(ep_num)}) — الجودة ({quality})"
    dur_txt = format_duration_mins(duration_sec)
    if dur_txt:
        return f"{base} — المدة ({dur_txt})"
    return base


def cleanup_file(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


def build_progress_bar(percent):
    pct = max(0.0, min(100.0, float(percent)))
    filled = int(pct // 10)
    return "▓" * filled + "░" * (10 - filled) + f" {pct:.1f}%"


def format_speed(speed):
    if not speed:
        return "—"
    if speed >= 1024 * 1024:
        return f"{speed / (1024 * 1024):.2f} MB/s"
    if speed >= 1024:
        return f"{speed / 1024:.1f} KB/s"
    return f"{speed:.0f} B/s"


def guess_extension(url, content_type):
    clean_path = url.split("?", 1)[0].lower()
    for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".ts"):
        if clean_path.endswith(ext):
            return ext
    for mime, ext in VIDEO_EXT_MAP.items():
        if mime in content_type:
            return ext
    return ".mp4"


def is_hls_or_stream_url(url, content_type):
    low_url = url.lower().split("?", 1)[0]
    return (
        low_url.endswith(".m3u8")
        or "mpegurl" in content_type
        or "application/vnd.apple.mpegurl" in content_type
    )


def probe_media_info(file_path):
    duration_sec = 0
    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    has_ffmpeg = shutil.which("ffmpeg") is not None
    has_ffprobe = shutil.which("ffprobe") is not None

    if has_ffprobe and file_size <= MAX_THUMB_SOURCE_BYTES:
        try:
            
