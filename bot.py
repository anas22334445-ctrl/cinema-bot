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

TARGET_HEIGHTS = [468, 360, 288, 240, 144]

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
    return "Video Transcoder Bot (468/360/288/240/144) is alive!", 200


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
                    "quality": data.get("quality", "360p"),
                }
        except Exception:
            pass
    return {
        "series": "أمنية حب",
        "next_ep": 1,
        "quality": "360p",
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


def build_target_qualities_keyboard(task_id):
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = [
        types.InlineKeyboardButton(text=f"{h}p", callback_data=f"cvt|{task_id}|{h}")
        for h in TARGET_HEIGHTS
    ]
    markup.add(*buttons)
    return markup


def probe_media_info(file_path):
    duration_sec = 0
    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    has_ffmpeg = shutil.which("ffmpeg") is not None
    has_ffprobe = shutil.which("ffprobe") is not None

    if has_ffprobe and file_size <= MAX_THUMB_SOURCE_BYTES:
        try:
            res = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    file_path,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            val = float(res.stdout.strip())
            if val > 0:
                duration_sec = int(round(val))
        except Exception:
            pass

    thumb_path = None
    if has_ffmpeg and file_size <= MAX_THUMB_SOURCE_BYTES:
        candidate_thumb = f"{file_path}_thumb.jpg"
        seek_pos = "00:00:05" if duration_sec > 10 else "00:00:01"
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    seek_pos,
                    "-i",
                    file_path,
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=320:-1",
                    "-q:v",
                    "4",
                    candidate_thumb,
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if os.path.exists(candidate_thumb) and os.path.getsize(candidate_thumb) > 0:
                thumb_path = candidate_thumb
            else:
                cleanup_file(candidate_thumb)
        except Exception:
            cleanup_file(candidate_thumb)

    if not thumb_path and os.path.exists(DEFAULT_COVER_FILE):
        thumb_path = DEFAULT_COVER_FILE

    return duration_sec, thumb_path


def transcode_video_to_height(input_path, output_path, target_height):
    """يحوّل الفيديو إلى الارتفاع المحدد (468, 360, 288, 240, 144) مع أبعاد زوجية."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("أداة ffmpeg غير مثبتة على السيرفر لإجراء التحويل.")

    h = int(target_height)
    # نضمن عرضاً زوجياً متناسباً مع الارتفاع المطلوب دون تكبير إذا كان المصدر أصغر، أو تحجيم مباشر
    vf_filter = f"scale=-2:{h}"

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vf",
        vf_filter,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "25",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        output_path,
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        cleanup_file(output_path)
        err_tail = (proc.stderr or "")[-250:]
        raise RuntimeError(f"فشل تحويل الفيديو إلى {h}p: {err_tail}")

    if os.path.getsize(output_path) > MAX_SIZE_BYTES:
        cleanup_file(output_path)
        raise FileTooLargeError(f"حجم الملف بعد التحويل إلى {h}p يتجاوز 2GB.")

    return output_path


def download_via_http_stream(url, out_prefix, content_type, total_size, on_progress):
    ext = guess_extension(url, content_type)
    out_path = f"{out_prefix}{ext}"

    if total_size and total_size > MAX_SIZE_BYTES:
        raise FileTooLargeError("حجم الملف يتجاوز 2GB.")

    headers = {**BROWSER_HEADERS, "Referer": url}
    downloaded = 0
    start_ts = time.time()
    last_ui_ts = 0.0
    last_bytes = 0
    last_speed_ts = start_ts
    current_speed = 0.0

    with requests.get(url, headers=headers, stream=True, timeout=30) as r:
        r.raise_for_status()
        resp_len_raw = r.headers.get("Content-Length")
        if resp_len_raw and resp_len_raw.isdigit():
            total_size = int(resp_len_raw)
            if total_size > MAX_SIZE_BYTES:
                raise FileTooLargeError("حجم الملف يتجاوز 2GB.")

        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > MAX_SIZE_BYTES:
                    f.close()
                    cleanup_file(out_path)
                    raise FileTooLargeError("حجم الملف يتجاوز 2GB.")

                f.write(chunk)
                now = time.time()
                dt_speed = now - last_speed_ts
                if dt_speed >= 0.8:
                    current_speed = (downloaded - last_bytes) / dt_speed
                    last_bytes = downloaded
                    last_speed_ts = now

                if now - last_ui_ts >= 2.5:
                    last_ui_ts = now
                    pct = (downloaded / total_size * 100.0) if total_size > 0 else 0.0
                    on_progress(pct, format_speed(current_speed))

    on_progress(100.0, "اكتمل")

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        cleanup_file(out_path)
        raise RuntimeError("الملف المحمّل فارغ أو غير موجود.")

    if os.path.getsize(out_path) > MAX_SIZE_BYTES:
        cleanup_file(out_path)
        raise FileTooLargeError("حجم الملف النهائي يتجاوز 2GB.")

    return out_path, 0


def download_via_ytdlp(url, out_prefix, on_progress):
    last_ui_ts = [0.0]

    def progress_hook(d):
        status = d.get("status")
        if status == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if downloaded > MAX_SIZE_BYTES or total > MAX_SIZE_BYTES:
                raise FileTooLargeError("حجم الملف يتجاوز 2GB.")

            now = time.time()
            if now - last_ui_ts[0] >= 2.5:
                last_ui_ts[0] = now
                pct = (downloaded / total * 100.0) if total > 0 else 0.0
                on_progress(pct, format_speed(d.get("speed")))
        elif status == "finished":
            on_progress(100.0, "اكتمل")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "format": f"best[filesize<={MAX_SIZE_BYTES}]/best",
        "merge_output_format": "mp4",
        "outtmpl": f"{out_prefix}.%(ext)s",
        "http_headers": {**BROWSER_HEADERS, "Referer": url},
        "progress_hooks": [progress_hook],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        duration_sec = 0
        if isinstance(info, dict):
            est = info.get("filesize") or info.get("filesize_approx") or 0
            if est and est > MAX_SIZE_BYTES:
                raise FileTooLargeError("حجم الملف يتجاوز 2GB.")
            if info.get("duration"):
                duration_sec = int(float(info["duration"]))

        ydl.extract_info(url, download=True)

        candidates = []
        if isinstance(info, dict):
            try:
                candidates.append(ydl.prepare_filename(info))
            except Exception:
                pass
        for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".ts"):
            candidates.append(out_prefix + ext)

        prefix_base = os.path.basename(out_prefix)
        for name in os.listdir(DOWNLOAD_DIR):
            if name.startswith(prefix_base):
                candidates.append(os.path.join(DOWNLOAD_DIR, name))

        for path in candidates:
            if path and os.path.exists(path) and os.path.getsize(path) > 0:
                if os.path.getsize(path) > MAX_SIZE_BYTES:
                    cleanup_file(path)
                    raise FileTooLargeError("حجم الملف النهائي يتجاوز 2GB.")
                return path, duration_sec

    raise RuntimeError("تعذر العثور على الملف بعد التحميل.")


def fetch_source_file(task_info, on_progress):
    mode = task_info.get("mode")
    prefix = os.path.join(DOWNLOAD_DIR, f"src_{uuid.uuid4().hex[:8]}")

    if mode == "url":
        url = task_info["url"]
        resolved_url, content_type, total_size = expand_url(url)
        if is_hls_or_stream_url(resolved_url, content_type):
            return download_via_ytdlp(resolved_url, prefix, on_progress)
        try:
            return download_via_http_stream(
                resolved_url, prefix, content_type, total_size, on_progress
            )
        except Exception:
            return download_via_ytdlp(resolved_url, prefix, on_progress)

    if mode == "telegram_video":
        tg_video = task_info["tg_video"]
        if tg_video.file_size and tg_video.file_size > MAX_SIZE_BYTES:
            raise FileTooLargeError("حجم الفيديو يتجاوز 2GB.")
        on_progress(25.0, "جلب من تلغرام...")
        file_info = bot.get_file(tg_video.file_id)
        downloaded_bytes = bot.download_file(file_info.file_path)
        if len(downloaded_bytes) > MAX_SIZE_BYTES:
            raise FileTooLargeError("حجم الفيديو يتجاوز 2GB.")
        raw_path = f"{prefix}.mp4"
        with open(raw_path, "wb") as f:
            f.write(downloaded_bytes)
        on_progress(100.0, "اكتمل")
        return raw_path, int(tg_video.duration or 0)

    raise RuntimeError("مصدر غير معروف.")


def run_convert_and_upload(chat_id, status_msg_id, task_info, target_height):
    raw_file = None
    converted_file = None
    thumb_path = None

    quality_label = f"{int(target_height)}p"

    with state_lock:
        ep_num = state["next_ep"]
        series_name = state["series"]

    ep_str = format_ep(ep_num)

    def update_dl(pct, speed_str):
        safe_edit_message(
            chat_id,
            status_msg_id,
            f"📥 تحميل المصدر (حتى 2GB)\n"
            f"🎬 الحلقة: ({ep_str})\n"
            f"🎯 الدقة المطلوبة: ({quality_label})\n"
            f"📊 التقدم: {build_progress_bar(pct)}\n"
            f"⚡ السرعة: {speed_str}",
        )

    update_dl(0.0, "بدء...")

    try:
        raw_file, meta_duration = fetch_source_file(task_info, update_dl)

        safe_edit_message(
            chat_id,
            status_msg_id,
            f"⚙️ جاري تحويل الفيديو إلى دقة ({quality_label})...\n"
            f"🎬 الحلقة: ({ep_str})",
        )

        converted_path = os.path.join(
            DOWNLOAD_DIR, f"out_{uuid.uuid4().hex[:8]}_{target_height}p.mp4"
        )
        converted_file = transcode_video_to_height(
            raw_file, converted_path, target_height
        )

        probed_duration, thumb_path = probe_media_info(converted_file)
        duration_sec = probed_duration or meta_duration or 0
        caption = build_caption(series_name, ep_num, quality_label, duration_sec)

        safe_edit_message(
            chat_id,
            status_msg_id,
            f"⬆️ جاري الرفع إلى تلغرام\n"
            f"🎬 الحلقة: ({ep_str}) — الجودة: ({quality_label})",
        )

        thumb_file = None
        try:
            if thumb_path and os.path.exists(thumb_path):
                thumb_file = open(thumb_path, "rb")

            with open(converted_file, "rb") as vid_file:
                try:
                    bot.send_video(
                        chat_id=chat_id,
                        video=vid_file,
                        caption=caption,
                        duration=duration_sec if duration_sec > 0 else None,
                        thumb=thumb_file,
                        supports_streaming=True,
                        timeout=1200,
                    )
                except Exception:
                    vid_file.seek(0)
                    if thumb_file:
                        thumb_file.seek(0)
                    bot.send_document(
                        chat_id=chat_id,
                        document=vid_file,
                        thumb=thumb_file,
                        caption=caption,
                        timeout=1200,
                    )
        finally:
            if thumb_file:
                try:
                    thumb_file.close()
                except Exception:
                    pass

        with state_lock:
            state["next_ep"] = ep_num + 1
            state["quality"] = quality_label
            save_state(state)
            next_ep_after = format_ep(state["next_ep"])

        safe_edit_message(
            chat_id,
            status_msg_id,
            f"✅ تم التحويل والرفع بنجاح إلى ({quality_label})!\n"
            f"🔢 الحلقة القادمة ستكون: ({next_ep_after})",
        )

    except FileTooLargeError:
        safe_edit_message(
            chat_id,
            status_msg_id,
            f"⚠️ تم تخطي الحلقة ({ep_str}) لأن حجمها يتجاوز 2GB.",
        )
    except Exception as e:
        safe_edit_message(
            chat_id,
            status_msg_id,
            f"❌ فشل التحويل أو الرفع ({ep_str}):\n{str(e)[:300]}",
        )
    finally:
        cleanup_file(raw_file)
        cleanup_file(converted_file)
        if thumb_path and thumb_path != DEFAULT_COVER_FILE:
            cleanup_file(thumb_path)


def register_and_prompt_qualities(chat_id, reply_to_msg_id, task_payload):
    task_id = uuid.uuid4().hex[:10]
    with state_lock:
        pending_tasks[task_id] = task_payload

    safe_send_message(
        chat_id,
        "🎞 اختر الدقة المطلوبة للتحويل (468 / 360 / 288 / 240 / 144):",
        reply_to_message_id=reply_to_msg_id,
        reply_markup=build_target_qualities_keyboard(task_id),
    )


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("cvt|"))
def handle_convert_callback(call):
    if not call.from_user or not is_authorized_user(call.from_user.id):
        bot.answer_callback_query(call.id, "هذا البوت خاص بالمالك فقط.")
        return

    parts = call.data.split("|")
    if len(parts) != 3 or not parts[2].isdigit():
        bot.answer_callback_query(call.id, "اختيار غير صالح.")
        return

    _, task_id, height_str = parts
    target_height = int(height_str)
    if target_height not in TARGET_HEIGHTS:
        bot.answer_callback_query(call.id, "دقة غير مدعومة.")
        return

    with state_lock:
        task_info = pending_tasks.pop(task_id, None)

    if not task_info:
        bot.answer_callback_query(call.id, "انتهت صلاحية الاختيار، أرسل الفيديو أو الرابط مجدداً.")
        return

    bot.answer_callback_query(call.id, f"جاري التحويل إلى {target_height}p...")

    threading.Thread(
        target=run_convert_and_upload,
        args=(
            call.message.chat.id,
            call.message.message_id,
            task_info,
            target_height,
        ),
        daemon=True,
    ).start()


@bot.message_handler(commands=["start", "help", "status"])
def send_welcome(message):
    if not is_authorized_message(message):
        return

    with state_lock:
        series_val = state["series"]
        next_ep_val = format_ep(state["next_ep"])
        quality_val = state["quality"]
        current_cap = build_caption(series_val, state["next_ep"], quality_val)

    lock_status = "مفعل 🔒" if ADMIN_ID is not None else "غير مضبوط (أضف ADMIN_ID في Render)"

    text = (
        "🎞 بوت تحويل الفيديو إلى دقات (468 / 360 / 288 / 240 / 144)\n\n"
        f"🔐 حماية المالك: {lock_status}\n"
        f"📌 المسلسل الحالي: {series_val}\n"
        f"🔢 الحلقة القادمة: ({next_ep_val})\n"
        f"🎞 آخر جودة: ({quality_val})\n"
        f"📝 الكابشن القادم:\n{current_cap}\n\n"
        "الأوامر المتاحة:\n"
        "• /setseries اسم المسلسل\n"
        "• /setep 1\n"
        "• /status\n\n"
        "📥 أرسل رابط فيديو مباشر (أو أرسل فيديو مباشرة) لتظهر أزرار الدقات!"
    )
    safe_send_message(message.chat.id, text, reply_to_message_id=message.message_id)


@bot.message_handler(commands=["setseries"])
def set_series(message):
    if not is_authorized_message(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        safe_send_message(
            message.chat.id,
            "⚠️ مثال:\n/setseries أمنية حب",
            reply_to_message_id=message.message_id,
        )
        return
    with state_lock:
        state["series"] = parts[1].strip()
        save_state(state)
        val = state["series"]
    safe_send_message(
        message.chat.id,
        f"✅ تم تحديث اسم المسلسل إلى: {val}",
        reply_to_message_id=message.message_id,
    )


@bot.message_handler(commands=["setep"])
def set_episode(message):
    if not is_authorized_message(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        safe_send_message(
            message.chat.id,
            "⚠️ مثال:\n/setep 1",
            reply_to_message_id=message.message_id,
        )
        return
    ep_val = int(parts[1].strip())
    if ep_val < 1:
        safe_send_message(
            message.chat.id,
            "⚠️ رقم الحلقة يجب أن يكون 1 أو أكثر.",
            reply_to_message_id=message.message_id,
        )
        return
    with state_lock:
        state["next_ep"] = ep_val
        save_state(state)
    safe_send_message(
        message.chat.id,
        f"✅ تم ضبط الحلقة القادمة لتكون: ({format_ep(ep_val)})",
        reply_to_message_id=message.message_id,
    )


@bot.message_handler(content_types=["video"])
def handle_video_message(message):
    if not is_authorized_message(message):
        return
    if not message.video:
        return
    register_and_prompt_qualities(
        message.chat.id,
        message.message_id,
        {"mode": "telegram_video", "tg_video": message.video},
    )


@bot.message_handler(func=lambda m: isinstance(m.text, str) and "http" in m.text.lower())
def handle_url_message(message):
    if not is_authorized_message(message):
        return

    urls = extract_urls_from_text(message.text)
    if not urls:
        safe_send_message(
            message.chat.id,
            "⚠️ لم يتم العثور على رابط صالح.",
            reply_to_message_id=message.message_id,
        )
        return

    register_and_prompt_qualities(
        message.chat.id,
        message.message_id,
        {"mode": "url", "url": urls[0]},
    )


def run_bot():
    try:
        bot.remove_webhook()
    except Exception:
        pass

    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=25)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
