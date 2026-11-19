import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from flask import Flask
import telebot
from telebot import types
import yt_dlp

BOT_TOKEN = os.getenv("BOT_TOKEN", "8956783825:AAHS01rPwSdVztYF-k1jfdk9zGIBnfTqcgw")
COOKIES_FILE = os.getenv("COOKIES_FILE", "cookies.txt")
DATA_FILE = "series_state.json"
DOWNLOAD_DIR = "downloads"

MAX_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

QUALITIES = [
    ("4k", "4K (2160p)", 2160),
    ("2k", "2K (1440p)", 1440),
    ("1080", "1080p", 1080),
    ("720", "720p", 720),
    ("640", "640p", 640),
    ("576", "576p", 576),
    ("468", "468p", 468),
    ("360", "360p", 360),
    ("288", "288p", 288),
    ("240", "240p", 240),
    ("144", "144p", 144),
]

QUALITY_MAP = {code: (label, height) for code, label, height in QUALITIES}

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
state_lock = threading.Lock()
pending_playlists = {}


class FileTooLargeError(Exception):
    pass


@app.route("/")
def home():
    return "YouTube Playlist Bot is alive!", 200


def load_state():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "series": "أمنية حب",
        "next_ep": 1,
        "quality": "720p",
    }


def save_state(state_data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state_data, f, ensure_ascii=False, indent=2)


state = load_state()


def is_youtube_url(url):
    return bool(
        re.match(
            r"^https?://(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/.+",
            url.strip(),
            flags=re.IGNORECASE,
        )
    )


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


def build_quality_keyboard(task_id):
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = [
        types.InlineKeyboardButton(text=label, callback_data=f"pq|{task_id}|{code}")
        for code, label, _ in QUALITIES
    ]
    markup.add(*buttons)
    markup.add(
        types.InlineKeyboardButton(
            text="⚡ أفضل جودة متاحة لكل حلقة (حتى 2GB)",
            callback_data=f"pq|{task_id}|best",
        )
    )
    return markup


def build_format_selector(quality_code):
    if quality_code == "best":
        return (
            f"best[ext=mp4][filesize<={MAX_SIZE_BYTES}]/"
            f"best[filesize<={MAX_SIZE_BYTES}]/"
            f"bestvideo[ext=mp4][filesize<={MAX_SIZE_BYTES}]+bestaudio[ext=m4a]/"
            f"bestvideo[filesize<={MAX_SIZE_BYTES}]+bestaudio/best"
        )

    _, target_h = QUALITY_MAP[quality_code]
    return (
        f"best[ext=mp4][height<={target_h}][filesize<={MAX_SIZE_BYTES}]/"
        f"best[height<={target_h}][filesize<={MAX_SIZE_BYTES}]/"
        f"bestvideo[ext=mp4][height<={target_h}][filesize<={MAX_SIZE_BYTES}]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={target_h}][filesize<={MAX_SIZE_BYTES}]+bestaudio/"
        f"best[filesize<={MAX_SIZE_BYTES}]/best"
    )


def get_base_ydl_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web", "tv"],
            }
        },
    }
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts


def extract_playlist_entries(playlist_url):
    opts = {
        **get_base_ydl_opts(),
        "extract_flat": "in_playlist",
        "noplaylist": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(playlist_url, download=False)

    entries = []
    if isinstance(info, dict) and info.get("entries"):
        for idx, item in enumerate(info["entries"], start=1):
            if not item:
                continue
            vid_id = item.get("id")
            vid_url = item.get("url") or (
                f"https://www.youtube.com/watch?v={vid_id}" if vid_id else None
            )
            if vid_url and not vid_url.startswith("http"):
                vid_url = f"https://www.youtube.com/watch?v={vid_url}"
            if vid_url:
                entries.append(
                    {
                        "index": idx,
                        "title": item.get("title") or f"فيديو {idx}",
                        "url": vid_url,
                    }
                )
    elif isinstance(info, dict) and info.get("webpage_url"):
        entries.append(
            {
                "index": 1,
                "title": info.get("title") or "فيديو 1",
                "url": info["webpage_url"],
            }
        )

    return entries


def find_downloaded_file(out_prefix, info=None, ydl=None):
    candidates = []
    if info and ydl:
        try:
            candidates.append(ydl.prepare_filename(info))
        except Exception:
            pass

    for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4v"):
        candidates.append(out_prefix + ext)

    prefix_base = os.path.basename(out_prefix)
    for name in os.listdir(DOWNLOAD_DIR):
        if name.startswith(prefix_base):
            candidates.append(os.path.join(DOWNLOAD_DIR, name))

    for path in candidates:
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            return path
    return None


def download_single_video_with_progress(
    video_url, out_prefix, quality_code, on_progress
):
    last_update_ts = [0.0]

    def progress_hook(d):
        status = d.get("status")
        if status == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if downloaded > MAX_SIZE_BYTES or total > MAX_SIZE_BYTES:
                raise FileTooLargeError("حجم الحلقة يتجاوز 2GB.")

            now = time.time()
            if now - last_update_ts[0] >= 2.5:
                last_update_ts[0] = now
                pct = (downloaded / total * 100.0) if total > 0 else 0.0
                speed_str = format_speed(d.get("speed"))
                on_progress(pct, speed_str)
        elif status == "finished":
            on_progress(100.0, "اكتمل")

    ydl_opts = {
        **get_base_ydl_opts(),
        "format": build_format_selector(quality_code),
        "merge_output_format": "mp4",
        "outtmpl": f"{out_prefix}.%(ext)s",
        "noplaylist": True,
        "progress_hooks": [progress_hook],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        est_size = info.get("filesize") or info.get("filesize_approx") or 0
        if est_size and est_size > MAX_SIZE_BYTES:
            raise FileTooLargeError("حجم الحلقة يتجاوز 2GB.")

        ydl.extract_info(video_url, download=True)
        file_path = find_downloaded_file(out_prefix, info=info, ydl=ydl)
        if not file_path:
            raise RuntimeError("تعذر العثور على ملف الفيديو بعد التحميل.")

        if os.path.getsize(file_path) > MAX_SIZE_BYTES:
            cleanup_file(file_path)
            raise FileTooLargeError("حجم الملف النهائي يتجاوز 2GB.")

        return file_path


def run_ordered_playlist_download(chat_id, status_msg_id, playlist_url, quality_code):
    quality_label = (
        "HD" if quality_code == "best" else QUALITY_MAP[quality_code][0].split()[0]
    )

    try:
        bot.edit_message_text(
            "🔎 جاري جلب حلقات قائمة التشغيل من يوتيوب بالترتيب...",
            chat_id=chat_id,
            message_id=status_msg_id,
        )

        entries = extract_playlist_entries(playlist_url)
        if not entries:
            bot.edit_message_text(
                "⚠️ لم يتم العثور على فيديوهات في هذه القائمة (تأكد أن الرابط قائمة تشغيل يوتيوب صحيحة وأن الكوكيز مرفوعة إذا كانت خاصة).",
                chat_id=chat_id,
                message_id=status_msg_id,
            )
            return

        total_count = len(entries)
        success_count = 0

        for pos, item in enumerate(entries, start=1):
            file_path = None
            vid_title = item["title"][:50]
            vid_url = item["url"]

            with state_lock:
                ep_num = state["next_ep"]
                series_name = state["series"]
                state["quality"] = quality_label
                save_state(state)

            ep_str = format_ep(ep_num)
            caption = build_caption(series_name, ep_num, quality_label)

            def update_dl_msg(pct, speed_str):
                try:
                    bot.edit_message_text(
                        f"📥 **تحميل بالترتيب ({pos}/{total_count})**\n"
                        f"🎬 الحلقة: **({ep_str})** — {vid_title}\n"
                        f"🎞 الجودة: **({quality_label})**\n"
                        f"📊 التقدم: `{build_progress_bar(pct)}`\n"
                        f"⚡ السرعة: `{speed_str}`",
                        chat_id=chat_id,
                        message_id=status_msg_id,
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

            update_dl_msg(0.0, "بدء...")

            try:
                out_prefix = os.path.join(
                    DOWNLOAD_DIR, f"pl_{chat_id}_{pos}_{uuid.uuid4().hex[:6]}"
                )
                file_path = download_single_video_with_progress(
                    vid_url, out_prefix, quality_code, update_dl_msg
                )

                bot.edit_message_text(
                    f"⬆️ **جاري الرفع إلى تلغرام ({pos}/{total_count})**\n"
                    f"🎬 الحلقة: **({ep_str})** — {vid_title}",
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
                    f"⚠️ تم تخطي الحلقة ({ep_str}) ({vid_title}) لأن حجمها يتجاوز 2GB.",
                )
            except Exception as e:
                bot.send_message(
                    chat_id,
                    f"❌ فشل تحميل/رفع الحلقة ({ep_str}) ({vid_title}):\n`{str(e)[:300]}`",
                    parse_mode="Markdown",
                )
            finally:
                cleanup_file(file_path)

        with state_lock:
            next_ep_after = format_ep(state["next_ep"])

        bot.edit_message_text(
            f"✅ **اكتملت قائمة التشغيل!**\n"
            f"✔️ تم رفع **{success_count}** من أصل **{total_count}** حلقة بالترتيب.\n"
            f"🔢 الحلقة القادمة ستكون: **({next_ep_after})**",
            chat_id=chat_id,
            message_id=status_msg_id,
            parse_mode="Markdown",
        )

    except Exception as e:
        bot.edit_message_text(
            f"❌ حدث خطأ أثناء معالجة قائمة التشغيل:\n`{str(e)[:400]}`",
            chat_id=chat_id,
            message_id=status_msg_id,
            parse_mode="Markdown",
        )


@bot.message_handler(commands=["start", "help", "status"])
def send_welcome(message):
    with state_lock:
        series_val = state["series"]
        next_ep_val = format_ep(state["next_ep"])
        quality_val = state["quality"]
        current_cap = build_caption(series_val, state["next_ep"], quality_val)

    cookies_status = "مفعل ✅" if os.path.exists(COOKIES_FILE) else "غير موجود (للروابط العامة فقط)"

    text = (
        "🎬 **بوت تحميل قوائم تشغيل يوتيوب بالترتيب**\n\n"
        f"📌 **المسلسل الحالي:** {series_val}\n"
        f"🔢 **الحلقة القادمة:** ({next_ep_val})\n"
        f"🎞 **الجودة الحالية:** ({quality_val})\n"
        f"🍪 **ملف الكوكيز (للقوائم الخاصة):** {cookies_status}\n"
        f"📝 **مثال الكابشن القادم:**\n`{current_cap}`\n\n"
        "**الأوامر المتاحة:**\n"
        "• `/setseries اسم المسلسل`\n"
        "• `/setep 1`\n"
        "• `/setquality 720p`\n"
        "• `/status`\n\n"
        "📺 أرسل **رابط قائمة تشغيل يوتيوب** لتبدأ باختيار الدقة والتحميل بالترتيب مع شريط التقدم!"
    )
    bot.reply_to(message, text, parse_mode="Markdown")


@bot.message_handler(commands=["setseries"])
def set_series(message):
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
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message, "⚠️ مثال:\n`/setquality 720p`", parse_mode="Markdown")
        return
    with state_lock:
        state["quality"] = parts[1].strip()
        save_state(state)
        val = state["quality"]
    bot.reply_to(message, f"✅ تم تحديث الجودة إلى: **({val})**", parse_mode="Markdown")


@bot.message_handler(func=lambda m: isinstance(m.text, str) and m.text.strip().startswith(("http://", "https://")))
def handle_url(message):
    url = message.text.strip().split()[0]

    if not is_youtube_url(url):
        bot.reply_to(
            message,
            "⚠️ هذا البوت يدعم **قوائم تشغيل يوتيوب حصراً** (`youtube.com` / `youtu.be`).",
            parse_mode="Markdown",
        )
        return

    task_id = uuid.uuid4().hex[:10]
    with state_lock:
        pending_playlists[task_id] = url

    bot.reply_to(
        message,
        "🎞 **تم استلام رابط يوتيوب، اختر الدقة المطلوبة للتحميل بالترتيب (حتى 2GB):**",
        reply_markup=build_quality_keyboard(task_id),
        parse_mode="Markdown",
    )


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("pq|"))
def handle_playlist_quality_callback(call):
    parts = call.data.split("|")
    if len(parts) != 3:
        bot.answer_callback_query(call.id, "طلب غير صالح.")
        return

    _, task_id, quality_code = parts
    with state_lock:
        playlist_url = pending_playlists.pop(task_id, None)

    if not playlist_url:
        bot.answer_callback_query(call.id, "انتهت صلاحية الطلب، أرسل الرابط مجدداً.")
        return

    bot.answer_callback_query(call.id, "جاري بدء التحميل بالترتيب...")
    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    threading.Thread(
        target=run_ordered_playlist_download,
        args=(chat_id, msg_id, playlist_url, quality_code),
        daemon=True,
    ).start()


def run_bot():
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "--pre", "yt-dlp"],
            check=False,
            timeout=120,
        )
    except Exception as e:
        print(f"yt-dlp auto-update note: {e}")

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
