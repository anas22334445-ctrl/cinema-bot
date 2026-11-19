import os
import os
import threading
from flask import Flask
import telebot

BOT_TOKEN = os.environ.get("BOT_TOKEN") or "8956783825:AAHS01rPwSdVztYF-k1jfdk9zGIBnfTqcgw"
bot = telebot.TeleBot(BOT_TOKEN)

# خادم ويب خفيف ليبقى متوافقاً مع Web Service في Render
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running", 200


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


user_sessions = {}


def format_caption(series_name: str, ep_num: int, quality: str) -> str:
    ep_str = f"{ep_num:02d}"
    return f"مسلسل ({series_name})  |  الحلقة ({ep_str})  |  [ {quality} ]"


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    text = (
        "أهلاً بك 👋\n\n"
        "لتحديد المسلسل ورقم البداية والدقة، أرسل:\n"
        "`/set #عـمـيـلـة_سـريـة | 1 | 720p HD`\n\n"
        "• يمكنك تغيير الدقة في أي وقت مثل `720p` أو `720p HD` أو `1080p`.\n"
        "• لإيقاف التسمية الحالية أرسل `/stop`.\n"
        "• أرسل الفيديوهات بعدها ليتم تسميتها وإرسالها بالترتيب."
    )
    bot.reply_to(message, text)


@bot.message_handler(commands=["set"])
def set_series(message):
    raw = message.text.replace("/set", "", 1).strip()
    parts = [p.strip() for p in raw.split("|")]

    if len(parts) < 3 or not parts[0] or not parts[2]:
        bot.reply_to(
            message,
            "اكتب الأمر بالصيغة التالية:\n"
            "`/set اسم_المسلسل | رقم_البداية | الدقة`\n\n"
            "مثال:\n"
            "`/set #عـمـيـلـة_سـريـة | 1 | 720p HD`",
        )
        return

    series_name = parts[0]
    try:
        start_num = int(parts[1])
    except ValueError:
        bot.reply_to(message, "رقم البداية يجب أن يكون رقماً صحيحاً مثل 1.")
        return

    quality = parts[2]

    user_sessions[message.chat.id] = {
        "series": series_name,
        "next_num": start_num,
        "quality": quality,
    }

    preview = format_caption(series_name, start_num, quality)
    bot.reply_to(
        message,
        f"✅ تم حفظ الإعدادات:\n"
        f"المعاينة للحلقة القادمة:\n`{preview}`",
    )


@bot.message_handler(commands=["stop"])
def stop_series(message):
    user_sessions.pop(message.chat.id, None)
    bot.reply_to(message, "تم إيقاف وضع التسمية الحالية.")


@bot.message_handler(content_types=["video"])
def handle_video(message):
    chat_id = message.chat.id
    if chat_id not in user_sessions:
        bot.reply_to(
            message,
            "حدد أولاً البيانات عبر الأمر:\n"
            "`/set #عـمـيـلـة_سـريـة | 1 | 720p HD`",
        )
        return

    session = user_sessions[chat_id]
    caption = format_caption(
        session["series"],
        session["next_num"],
        session["quality"],
    )
    session["next_num"] += 1

    bot.send_video(
        chat_id=chat_id,
        video=message.video.file_id,
        caption=caption,
        supports_streaming=True,
    )


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot.infinity_polling(skip_pending=True)
