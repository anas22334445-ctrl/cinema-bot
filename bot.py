import os
import os
import threading
from flask import Flask
import telebot

# 👇 ضع توكن البوت هنا أو في متغير BOT_TOKEN داخل Render
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "8956783825:AAHS01rPwSdVztYF-k1jfdk9zGIBnfTqcgw"
bot = telebot.TeleBot(BOT_TOKEN)

# خادم ويب خفيف لخدمة Web Service في Render
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running", 200


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# لكل مستخدم نحتفظ بإعدادات التسمية الحالية
user_sessions = {}


def format_ep(num: int) -> str:
    return f"{num:02d}"


def build_caption(session: dict, num: int) -> str:
    ep_str = format_ep(num)
    return f"مسلسل {session['series']} | الحلقة ({ep_str}) | {session['quality']}"


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    text = (
        "أهلاً بك 👋\n\n"
        "طريقة العمل:\n"
        "1️⃣ أرسل أمر التسمية بهذا الشكل:\n"
        "`/set قلبي يحترق لأجله | 10 | 720p - HD`\n\n"
        "2️⃣ بعدها أرسل الفيديوهات (حتى دفعة كاملة)، وسأعيد إرسالها لك مرتبة ومسمّاة تلقائياً.\n\n"
        "لإيقاف الوضع الحالي: `/stop`"
    )
    bot.reply_to(message, text, parse_mode="Markdown")


@bot.message_handler(commands=["set"])
def set_naming(message):
    raw = message.text.replace("/set", "", 1).strip()
    parts = [p.strip() for p in raw.split("|")]

    if len(parts) < 2 or not parts[0]:
        bot.reply_to(
            message,
            "اكتب الأمر هكذا:\n`/set قلبي يحترق لأجله | 10 | 720p - HD`",
            parse_mode="Markdown",
        )
        return

    series_name = parts[0]
    try:
        start_num = int(parts[1])
    except ValueError:
        bot.reply_to(
            message,
            "الرقم بعد `|` يجب أن يكون عدداً صحيحاً، مثل `1` أو `10`.",
            parse_mode="Markdown",
        )
        return

    quality = parts[2] if len(parts) >= 3 and parts[2] else "720p - HD"

    user_sessions[message.chat.id] = {
        "series": series_name,
        "next_num": start_num,
        "quality": quality,
    }

    sample = build_caption(user_sessions[message.chat.id], start_num)
    bot.reply_to(
        message,
        f"✅ تم الضبط:\n"
        f"• المسلسل: *{series_name}*\n"
        f"• يبدأ من الحلقة: *{start_num:02d}*\n"
        f"• الدقة: *{quality}*\n\n"
        f"المعاينة:\n`{sample}`\n\n"
        f"الآن أرسل الفيديوهات.",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["stop"])
def stop_naming(message):
    user_sessions.pop(message.chat.id, None)
    bot.reply_to(message, "تم إيقاف وضع التسمية التلقائية.")


@bot.message_handler(content_types=["video"])
def handle_video(message):
    chat_id = message.chat.id
    if chat_id not in user_sessions:
        bot.reply_to(
            message,
            "حدد أولاً اسم العمل ورقم البداية والدقة عبر:\n`/set قلبي يحترق لأجله | 10 | 720p - HD`",
            parse_mode="Markdown",
        )
        return

    session = user_sessions[chat_id]
    num = session["next_num"]
    caption = build_caption(session, num)
    session["next_num"] += 1

    file_id = message.video.file_id
    bot.send_video(
        chat_id=chat_id,
        video=file_id,
        caption=caption,
        supports_streaming=True,
    )


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot.infinity_polling(skip_pending=True)
