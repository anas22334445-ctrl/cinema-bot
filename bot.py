import json
import os
import re
import threading
import time
import uuid
from urllib.parse import urljoin
from flask import Flask
import requests
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
pending_urls = {}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}


@app.route("/")
def home():
    return "Bot is alive!", 200


def load_state():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "series": "أمنية حب",
        "next_ep": 8,
        "quality": "720p",
    }


def save_state(state_data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state_data, f, ensure_ascii=False, indent=2)


state = load_state()


def is_admin_id(user_id):
    return True


def is_admin(message):
    return True


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


class FileTooLargeError(Exception):
    pass


def build_quality_keyboard(task_id):
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = [
        types.InlineKeyboardButton(text=label, callback_data=f"q|{task_id}|{code}")
        for code, label, _ in QUALITIES
    ]
    markup.add(*buttons)
    markup.add(
        types.InlineKeyboardButton(
            text="⚡ أفضل جودة متاحة (حتى 2GB)",
            callback_data=f"q|{task_id}|best",
        )
    )
    return markup


def build_format_selector(quality_code):
    if quality_code == "best":
        return (
            f"best[ext=mp4][filesize<={MAX_SIZE_BYTES}]/"
            f"best[filesize<={MAX_SIZE_BYTES}]/"
            f"bestvideo[filesize<={MAX_SIZE_BYTES}]+bestaudio/best"
        )

    _, target_h = QUALITY_MAP[quality_code]
    return (
        f"best[ext=mp4][height<={target_h}][filesize<={MAX_SIZE_BYTES}]/"
        f"best[height<={target_h}][filesize<={MAX_SIZE_BYTES}]/"
        f"bestvideo[height<={target_h}][filesize<={MAX_SIZE_BYTES}]+bestaudio/"
        f"best[filesize<={MAX_SIZE_BYTES}]/bestvideo[filesize<={MAX_SIZE_BYTES}]+bestaudio/best"
    )


def find_downloaded_file(out_prefix, info=None, ydl=None):
    candidates = []
    if info and ydl:
        try:
            candidates.append(ydl.prepare_filename(info))
        except Exception:
            pass

    for ext in (".mp4", ".mkv", ".webm", ".mov", ".ts", ".m4v"):
        candidates.append(out_prefix + ext)

    for name in os.listdir(DOWNLOAD_DIR):
        prefix_base = os.path.basename(out_prefix)
        if name.startswith(prefix_base):
            candidates.append(os.path.join(DOWNLOAD_DIR, name))

    for path in candidates:
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            return path
    return None


def scrape_media_urls_from_page(page_url):
    found = []
    try:
        resp = requests.get(page_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        html = resp.text

        raw_matches = re.findall(
            r"""https?://[^\s"'<>\\)]+\.(?:m3u8|mp4)(?:\?[^\s"'<>\\)]*)?""",
            html,
            flags=re.IGNORECASE,
        )
        for m in raw_matches:
            clean = m.replace("\\/", "/")
            if clean not in found:
                found.append(clean)

        attr_matches = re.findall(
            r"""(?:src|data-src)\s*=\s*["']([^"']+)["']""",
            html,
            flags=re.IGNORECASE,
        )
        for src in attr_matches:
            full = urljoin(page_url, src)
            if full not in found:
                found.append(full)
    except Exception:
        pass
    return found


def download_direct_file_up_to_2gb(url, out_prefix, headers=None):
    req_headers = dict(HEADERS)
    if headers:
        req_headers.update(headers)

    out_path = f"{out_prefix}.mp4"
    downloaded = 0

    with requests.get(url, headers=req_headers, stream=True, timeout=30) as r:
        r.raise_for_status()
        content_len = r.headers.get("Content-Length")
        if content_len and int(content_len) > MAX_SIZE_BYTES:
            raise FileTooLargeError("حجم الملف يتجاوز 2GB.")

        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 512):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > MAX_SIZE_BYTES:
                    f.close()
                    cleanup_file(out_path)
                    raise FileTooLargeError("حجم الملف يتجاوز 2GB.")
                f.write(chunk)

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        cleanup_file(out_path)
        raise FileNotFoundError("تعذر حفظ ملف الفيديو المباشر.")

    return out_path


def download_from_any_url(url, out_prefix, quality_code):
    last_yt_error = None

    def progress_hook(d):
        if d.get("status") == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if downloaded > MAX_SIZE_BYTES or total > MAX_SIZE_BYTES:
                raise FileTooLargeError("حجم الفيديو يتجاوز 2GB.")

    ydl_opts = {
        "format": build_format_selector(quality_code),
        "merge_output_format": "mp4",
        "outtmpl": f"{out_prefix}.%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
        "http_headers": HEADERS,
        "socket_timeout": 30,
        "retries": 3,
    }

    if os.path.exists(COOKIES_FILE):
        ydl_opts["cookiefile"] = COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            est_size = info.get("filesize") or info.get("filesize_approx") or 0
            if est_size and est_size > MAX_SIZE_BYTES:
                raise FileTooLargeError("حجم الفيديو يتجاوز 2GB.")

            info = ydl.extract_info(url, download=True)
            file_path = find_downloaded_file(out_prefix, info=info, ydl=ydl)
            if file_path:
                if os.path.getsize(file_path) > MAX_SIZE_BYTES:
                    cleanup_file(file_path)
                    raise FileTooLargeError("حجم الملف النهائي يتجاوز 2GB.")
                return file_path
    except FileTooLargeError:
        raise
    except Exception as e:
        last_yt_error = str(e)

    if re.search(r"\.(mp4|mkv|webm|mov|m4v)(\?|$)", url, flags=re.IGNORECASE):
        try:
            return download_direct_file_up_to_2gb(url, out_prefix, headers={"Referer": url})
        except FileTooLargeError:
            raise
        except Exception:
            pass

    candidate_urls = scrape_media_urls_from_page(url)
    for cand in candidate_urls:
        try:
            with yt_dlp.YoutubeDL({**ydl_opts, "http_headers": {**HEADERS, "Referer": url}}) as ydl:
                info = ydl.extract_info(cand, download=True)
                file_path = find_downloaded_file(out_prefix, info=info, ydl=ydl)
                if file_path:
                    if os.path.getsize(file_path) > MAX_SIZE_BYTES:
                        cleanup_file(file_path)
                        raise FileTooLargeError("حجم الملف النهائي يتجاوز 2GB.")
                    return file_path
        except FileTooLargeError:
            raise
        except Exception:
            if cand.lower().split("?")[0].endswith(".mp4"):
                try:
                    return download_direct_file_up_to_2gb(cand, out_prefix, headers={"Referer": url})
                except FileTooLargeError:
                    raise
                except Exception:
                    continue

    if last_yt_error:
        raise RuntimeError(f"سبب فشل التحميل:\n{last_yt_error[:400]}")

    raise RuntimeError(
        "تعذر استخراج أو تنزيل الفيديو من هذا الرابط (قد يكون محمياً بتشفير DRM أو يتطلب تسجيل دخول/كوكيز)."
    )


def run_download_and_send(chat_id, status_msg_id, url, quality_code):
    file_path = None
    if quality_code == "best":
        quality_label = "HD"
    else:
        quality_label = QUALITY_MAP[quality_code][0].split()[0]

    try:
        bot.edit_message_text(
            f"⏳ جاري تنزيل الفيديو بدقة ({quality_label}) — الحد الأقصى 2GB...",
            chat_id=chat_id,
            message_id=status_msg_id,
        )

        out_prefix = os.path.join(DOWNLOAD_DIR, f"vid_{chat_id}_{uuid.uuid4().hex[:8]}")
        file_path = download_from_any_url(url, out_prefix, quality_code)

        bot.edit_message_text(
            "⬆️ تم التنزيل، جاري رفع الفيديو إلى تلغرام...",
            chat_id=chat_id,
            message_id=status_msg_id,
        )

        with state_lock:
            ep_num = state["next_ep"]
            series_name = state["series"]
            state["quality"] = quality_label
            save_state(state)
            caption = build_caption(series_name, ep_num, quality_label)

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
            next_ep_str = format_ep(state["next_ep"])

        bot.edit_message_text(
            f"✔️ تم تجهيز الحلقة ({format_ep(ep_num)}) بدقة ({quality_label}) — التالية ستكون ({next_ep_str})",
            chat_id=chat_id,
            message_id=status_msg_id,
        )

    except FileTooLargeError:
        bot.edit_message_text(
            "⚠️ حجم الفيديو أكبر من 2GB المسموح به.",
            chat_id=chat_id,
            message_id=status_msg_id,
        )
    except Exception as e:
        bot.edit_message_text(
            f"❌ فشل التنزيل أو الرفع:\n`{e}`",
            chat_id=chat_id,
            message_id=status_msg_id,
            parse_mode="Markdown",
        )
    finally:
        cleanup_file(file_path)


@bot.message_handler(commands=["start", "help", "status"])
def send_welcome(message):
    if not is_admin(message):
        return

    with state_lock:
        current_cap = build_caption(state["series"], state["next_ep"], state["quality"])
        series_val = state["series"]
        next_ep_val = format_ep(state["next_ep"])
        quality_val = state["quality"]

    text = (
        "🎬 **بوت تحميل وترقيم حلقات المسلسلات**\n\n"
        f"📌 **المسلسل الحالي:** {series_val}\n"
        f"🔢 **الحلقة القادمة:** ({next_ep_val})\n"
        f"🎞 **الجودة الحالية:** ({quality_val})\n"
        f"📝 **الكابشن القادم:**\n`{current_cap}`\n\n"
        "**الأوامر المتاحة:**\n"
        "• `/setseries اسم المسلسل`\n"
        "• `/setep 9`\n"
        "• `/setquality 720p`\n"
        "• `/status`\n\n"
        "🌐 أرسل **أي رابط** وستظهر لك أزرار الدقات (4K حتى 144p)!"
    )
    bot.reply_to(message, text, parse_mode="Markdown")


@bot.message_handler(commands=["setseries"])
def set_series(message):
    if not is_admin(message):
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
    if not is_admin(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        bot.reply_to(message, "⚠️ مثال:\n`/setep 8`", parse_mode="Markdown")
        return
    ep_val = int(parts[1].strip())
    if ep_val < 1:
        bot.reply_to(message, "⚠️ رقم الحلقة يجب أن يكون 1 أو أكثر.")
        return
    with state_lock:
        state["next_ep"] = ep_val
        save_state(state)
    bot.reply_to(message, f"✅ تم ضبط الحلقة القادمة لتكون: **({format_ep(ep_val)})**", parse_mode="Markdown")


@bot.message_handler(commands=["setquality"])
def set_quality(message):
    if not is_admin(message):
        return
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
def handle_url_message(message):
    if not is_admin(message):
        return

    url = message.text.strip().split()[0]
    task_id = uuid.uuid4().hex[:10]

    with state_lock:
        pending_urls[task_id] = url

    bot.reply_to(
        message,
        "🎞 **اختر دقة التحميل المطلوبة (الحد الأقصى 2GB):**",
        reply_markup=build_quality_keyboard(task_id),
        parse_mode="Markdown",
    )


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("q|"))
def handle_quality_callback(call):
    if not call.from_user or not is_admin_id(call.from_user.id):
        bot.answer_callback_query(call.id, "غير مصرح لك.")
        return

    parts = call.data.split("|")
    if len(parts) != 3:
        bot.answer_callback_query(call.id, "طلب غير صالح.")
        return

    _, task_id, quality_code = parts
    with state_lock:
        url = pending_urls.pop(task_id, None)

    if not url:
        bot.answer_callback_query(call.id, "انتهت صلاحية هذا الطلب، أرسل الرابط مجدداً.")
        return

    bot.answer
