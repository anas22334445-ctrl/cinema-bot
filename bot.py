import json
import os
import re
import threading
import time
import uuid
from flask import Flask
import requests
import telebot
import yt_dlp

BOT_TOKEN = os.getenv("BOT_TOKEN", "8956783825:AAHS01rPwSdVztYF-k1jfdk9zGIBnfTqcgw")
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else None

DATA_FILE = "series_state.json"
DOWNLOAD_DIR = "downloads"

MAX_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
CHUNK_SIZE = 1024 * 512  # 512 KB

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
state_lock = threading.Lock()

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
    return "Direct Video Downloader Bot is alive!", 200


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


def build_caption(series_name, ep_num, quality):
    return f"{series_name} — الحلقة ({format_ep(ep_num)}) — الجودة ({quality})"


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

    return out_path


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
        est = (
            (info.get("filesize") or info.get("filesize_approx") or 0)
            if isinstance(info, dict)
            else 0
        )
        if est and est > MAX_SIZE_BYTES:
            raise FileTooLargeError("حجم الملف يتجاوز 2GB.")

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
                return path

    raise RuntimeError("تعذر العثور على الملف بعد التحميل.")


def download_direct_video(url, out_prefix, on_progress):
    resolved_url, content_type, total_size = expand_url(url)

    if is_hls_or_stream_url(resolved_url, content_type):
        return download_via_ytdlp(resolved_url, out_prefix, on_progress)

    try:
        return download_via_http_stream(
            resolved_url, out_prefix, content_type, total_size, on_progress
        )
    except Exception:
        return download_via_ytdlp(resolved_url, out_prefix, on_progress)


def process_direct_urls(chat_id, status_msg_id, urls):
    total_count = len(urls)
    success_count = 0

    for idx, raw_url in enumerate(urls, start=1):
        file_path = None
        with state_lock:
            ep_num = state["next_ep"]
            series_name = state["series"]
            quality_label = state["quality"]

        ep_str = format_ep(ep_num)
        caption = build_caption(series_name, ep_num, quality_label)

        def update_msg(pct, speed_str):
            try:
                bot.edit_message_text(
                    f"📥 **تحميل مباشر ({idx}/{total_count})**\n"
                    f"🎬 الحلقة: **({ep_str})**\n"
                    f"🎞 الجودة: **({quality_label})**\n"
                    f"📊 التقدم: `{build_progress_bar(pct)}`\n"
                    f"⚡ السرعة: `{speed_str}`",
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        update_msg(0.0, "بدء...")

        try:
            out_prefix = os.path.join(
                DOWNLOAD_DIR, f"dir_{chat_id}_{idx}_{uuid.uuid4().hex[:6]}"
            )
            file_path = download_direct_video(raw_url, out_prefix, update_msg)

            bot.edit_message_text(
                f"⬆️ **جاري الرفع إلى تلغرام ({idx}/{total_count})**\n"
                f"🎬 الحلقة: **({ep_str})**",
                chat_id=chat_id,
                message_id=status_msg_id,
                parse_mode="Markdown",
            )

            with open(file_path, "rb") as vid_file:
                try:
                    bot.send_video(
                        chat_id=chat_id,
                        video=vid_file,
                        caption=caption,
                        supports_streaming=True,
                        timeout=600,
                    )
                except Exception:
                    vid_file.seek(0)
                    bot.send_document(
                        chat_id=chat_id,
                        document=vid_file,
                        caption=caption,
                        timeout=600,
                    )

            with state_lock:
                state["next_ep"] = ep_num + 1
                save_state(state)

            success_count += 1

        except FileTooLargeError:
            bot.send_message(
                chat_id,
                f"⚠️ تم تخطي الحلقة ({ep_str}) لأن حجمها يتجاوز 2GB.",
            )
        except Exception as e:
            bot.send_message(
                chat_id,
                f"❌ فشل تحميل/رفع الحلقة ({ep_str}):\n`{str(e)[:300]}`",
                parse_mode="Markdown",
            )
        finally:
            cleanup_file(file_path)

    with state_lock:
        next_ep_after = format_ep(state["next_ep"])

    bot.edit_message_text(
        f"✅ **اكتملت العملية!**\n"
        f"✔️ تم رفع **{success_count}** من أصل **{total_count}** فيديو.\n"
        f"🔢 الحلقة القادمة ستكون: **({next_ep_after})**",
        chat_id=chat_id,
        message_id=status_msg_id,
        parse_mode="Markdown",
    )


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
        "📥 **بوت تحميل الفيديوهات من روابط مباشرة**\n\n"
        f"🔐 **حماية المالك:** {lock_status}\n"
        f"📌 **المسلسل الحالي:** {series_val}\n"
        f"🔢 **الحلقة القادمة:** ({next_ep_val})\n"
        f"🎞 **الجودة بالكابشن:** ({quality_val})\n"
        f"📝 **الكابشن القادم:**\n`{current_cap}`\n\n"
        "**الأوامر المتاحة:**\n"
        "• `/setseries اسم المسلسل`\n"
        "• `/setep 1`\n"
        "• `/setquality 720p`\n"
        "• `/status`\n\n"
        "🔗 أرسل **رابط تنزيل مباشر للفيديو** (أو عدة روابط) ليبدأ التحميل والرفع فوراً!"
    )
    bot.reply_to(message, text, parse_mode="Markdown")


@bot.message_handler(commands=["setseries"])
def set_series(message):
    if not is_authorized_message(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message, "⚠️ مثال:\n`/setseries أمنية حب`", parse_mode="Markdown")
        return
    with state_lock:
        state["series"] = parts[1].strip()
        save_state(state)
        val = state["series"]
    bot.reply_to(message, f"✅ تم تحديث اسم المسلسل إلى: **{val}**", parse_mode="Markdown")


@bot.message_handler(commands=["setep"])
def set_episode(message):
    if not is_authorized_message(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        bot.reply_to(message, "⚠️ مثال:\n`/setep 1`", parse_mode="Markdown")
        return
    ep_val = int(parts[1].strip())
    if ep_val < 1:
        bot.reply_to(message, "⚠️ رقم الحلقة يجب أن يكون 1 أو أكثر.")
        return
    with state_lock:
        state["next_ep"] = ep_val
        save_state(state)
    bot.reply_to(
        message,
        f"✅ تم ضبط الحلقة القادمة لتكون: **({format_ep(ep_val)})**",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["setquality"])
def set_quality(message):
    if not is_authorized_message(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message, "⚠️ مثال:\n`/setquality 720p`", parse_mode="Markdown")
        return
    with state_lock:
        state["quality"] = parts[1].strip()
        save_state(state)
        val = state["quality"]
    bot.reply_to(message, f"✅ تم تحديث الجودة بالكابشن إلى: **({val})**", parse_mode="Markdown")


@bot.message_handler(func=lambda m: isinstance(m.text, str) and "http" in m.text.lower())
def handle_direct_links(message):
    if not is_authorized_message(message):
        return

    urls = extract_urls_from_text(message.text)
    if not urls:
        bot.reply_to(message, "⚠️ لم يتم العثور على رابط صالح.")
        return

    status_msg = bot.reply_to(
        message,
        f"⏳ **تم استلام ({len(urls)}) رابط مباشر، جاري التحميل...**",
        parse_mode="Markdown",
    )

    threading.Thread(
        target=process_direct_urls,
        args=(message.chat.id, status_msg.message_id, urls),
        daemon=True,
    ).start()


def run_bot():
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
