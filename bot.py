import json
import os
import queue
import threading
import time
from flask import Flask
import telebot

BOT_TOKEN = os.getenv("BOT_TOKEN", "8956783825:AAHS01rPwSdVztYF-k1jfdk9zGIBnfTqcgw")
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else None

DATA_FILE = "series_state.json"
MAX_BATCH_VIDEOS = 100

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

state_lock = threading.Lock()
job_queue = queue.Queue()


@app.route("/")
def home():
    return "Batch Video Numbering Bot (up to 100 videos) is alive!", 200


def safe_send_message(chat_id, text, reply_to_message_id=None):
    try:
        return bot.send_message(
            chat_id,
            text,
            reply_to_message_id=reply_to_message_id,
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
                    "quality": data.get("quality", "HD"),
                }
        except Exception:
            pass
    return {
        "series": "أمنية حب",
        "next_ep": 1,
        "quality": "HD",
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


def format_ep(num):
    num_int = int(num)
    return f"{num_int:02d}" if num_int < 100 else str(num_int)


def build_caption(series_name, ep_num, quality):
    return f"{series_name} — الحلقة ({format_ep(ep_num)}) — الجودة ({quality})"


def relay_single_video(chat_id, msg_id, video_obj):
    with state_lock:
        ep_num = state["next_ep"]
        series_name = state["series"]
        quality_label = state["quality"]

    ep_str = format_ep(ep_num)
    next_ep_str = format_ep(ep_num + 1)
    caption = build_caption(series_name, ep_num, quality_label)

    try:
        bot.send_video(
            chat_id=chat_id,
            video=video_obj.file_id,
            caption=caption,
            duration=int(video_obj.duration) if getattr(video_obj, "duration", None) else None,
            width=int(video_obj.width) if getattr(video_obj, "width", None) else None,
            height=int(video_obj.height) if getattr(video_obj, "height", None) else None,
            supports_streaming=True,
            timeout=120,
        )

        with state_lock:
            state["next_ep"] = ep_num + 1
            save_state(state)

        safe_send_message(
            chat_id,
            f"تم تجهيز الحلقة ({ep_str}) — التالية ستكون ({next_ep_str})",
            reply_to_message_id=msg_id,
        )
    except Exception as e:
        safe_send_message(
            chat_id,
            f"⚠️ تعذر ترقيم الحلقة ({ep_str}): {str(e)[:200]}",
            reply_to_message_id=msg_id,
        )


def worker_loop():
    while True:
        item = job_queue.get()
        try:
            chat_id, msg_id, video_obj = item
            relay_single_video(chat_id, msg_id, video_obj)
            time.sleep(0.35)
        except Exception as e:
            print(f"Queue worker error: {e}")
            time.sleep(1)
        finally:
            job_queue.task_done()


@bot.message_handler(commands=["start", "help", "status"])
def send_welcome(message):
    if not is_authorized_message(message):
        return

    with state_lock:
        series_val = state["series"]
        next_ep_val = format_ep(state["next_ep"])
        quality_val = state["quality"]
        current_cap = build_caption(series_val, state["next_ep"], quality_label=quality_val)

    text = (
        "🎬 بوت ترقيم الفيديوهات دفعة واحدة (حتى 100 فيديو)\n\n"
        f"📌 المسلسل الحالي: {series_val}\n"
        f"🔢 الحلقة القادمة: ({next_ep_val})\n"
        f"🎞 الجودة: ({quality_val})\n"
        f"📝 الكابشن القادم:\n{current_cap}\n\n"
        "الأوامر المتاحة:\n"
        "• /setseries اسم المسلسل\n"
        "• /setep 1\n"
        "• /setquality HD\n"
        "• /status\n\n"
        "📥 أرسل حتى 100 فيديو دفعة واحدة وسيتم ترقيمها وإرسالها بالترتيب!"
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


@bot.message_handler(commands=["setquality"])
def set_quality(message):
    if not is_authorized_message(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        safe_send_message(
            message.chat.id,
            "⚠️ مثال:\n/setquality HD",
            reply_to_message_id=message.message_id,
        )
        return
    with state_lock:
        state["quality"] = parts[1].strip()
        save_state(state)
        val = state["quality"]
    safe_send_message(
        message.chat.id,
        f"✅ تم تحديث الجودة إلى: ({val})",
        reply_to_message_id=message.message_id,
    )


@bot.message_handler(content_types=["video"])
def handle_video(message):
    if not is_authorized_message(message):
        return
    if not message.video:
        return

    if job_queue.qsize() >= MAX_BATCH_VIDEOS:
        safe_send_message(
            message.chat.id,
            f"⚠️ الطابور ممتلئ حالياً (الحد الأقصى {MAX_BATCH_VIDEOS} فيديو دفعة واحدة). انتظر قليلاً ثم أرسل الباقي.",
            reply_to_message_id=message.message_id,
        )
        return

    job_queue.put((message.chat.id, message.message_id, message.video))


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
    threading.Thread(target=worker_loop, daemon=True).start()
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
