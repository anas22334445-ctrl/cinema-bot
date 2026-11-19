import os
import os
import threading
from flask import Flask
import telebot

# ضع توكن البوت هنا داخل علامتي التنصيص إذا لم تضعه في Environment بـ Render
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "8956783825:AAHS01rPwSdVztYF-k1jfdk9zGIBnfTqcgw"

bot = telebot.TeleBot(BOT_TOKEN)

# سيرفر ويب خفيف لإبقاء Web Service متوافقاً مع Render
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running", 200


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# تخزين إعدادات كل محادثة
user_sessions = {}

STYLES = {
    1: "مسلسل {series} | الحلقة ({ep}) | {quality}",
    2: "مسلسل ({series}) | الحلقة ({ep}) | [ {quality} ]",
    3: "{series} - الحلقة {ep} ({quality})",
    4: "مسلسل {series} • الحلقة ({ep}) • {quality}",
}


def format_ep(session: dict, num: int) -> str:
    pad = session.get("pad", 2)
    return f"{num:0{pad}d}"


def build_caption(session: dict, num: int) -> str:
    ep_str = format_ep(session, num)
    style_id = session.get("style", 1)
    template = STYLES.get(style_id, STYLES[1])
    return template.format(
        series=session["series"],
        ep=ep_str,
        quality=session["quality"],
    )


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    text = (
        "أهلاً بك 👋\n\n"
        "طريقة الضبط:\n"
        "`/set اسم_المسلسل | رقم_البداية | الدقة | رقم_الشكل`\n\n"
        "مثال:\n"
        "`/set قلبي يحترق لأجله | 10 | 720p - HD | 1`\n\n"
        "الأشكال المتاحة:\n"
        "• *1:* `مسلسل قلبي يحترق لأجله | الحلقة (10) | 720p - HD`\n"
        "• *2:* `مسلسل (قلبي يحترق لأجله) | الحلقة (10) | [ 720p - HD ]`\n"
        "• *3:* `قلبي يحترق لأجله - الحلقة 10 (720p - HD)`\n"
        "• *4:* `مسلسل قلبي يحترق لأجله • الحلقة (10) • 720p - HD`\n\n"
        "أوامر إضافية:\n"
        "• `/style 2` — تغيير الشكل\n"
        "• `/quality 720p - HD` — تغيير الدقة\n"
        "• `/stop` — إيقاف الوضع الحالي\n\n"
        "بعد الضبط، أرسل الفيديوهات ليتم إرسالها مسمّاة بالترتيب."
    )
    bot.reply_to(message, text, parse_mode="Markdown")


@bot.message_handler(commands=["set"])
def set_naming(message):
    raw = message.text.replace("/set", "", 1).strip()
    parts = [p.strip() for p in raw.split("|")]

    if len(parts) < 2 or not parts[0]:
        bot.reply_to(
            message,
            "اكتب الأمر هكذا:\n`/set قلبي يحترق لأجله | 10 | 720p - HD | 1`",
            parse_mode="Markdown",
        )
        return

    series_name = parts[0]
    try:
        start_num = int(parts[1])
    except ValueError:
        bot.reply_to(
            message,
            "رقم البداية يجب أن يكون عدداً صحيحاً مثل `1` أو `10`.",
            parse_mode="Markdown",
        )
        return

    old_session = user_sessions.get(message.chat.id, {})
    quality = parts[2] if len(parts) >= 3 and parts[2] else old_session.get("quality", "720p - HD")

    style_id = old_session.get("style", 1)
    if len(parts) >= 4 and parts[3]:
        try:
            chosen = int(parts[3])
            if chosen in STYLES:
                style_id = chosen
            else:
                bot.reply_to(message, "رقم الشكل من 1 إلى 4 فقط. تم اختيار الشكل 1.")
                style_id = 1
        except ValueError:
            bot.reply_to(message, "رقم الشكل من 1 إلى 4 فقط. تم اختيار الشكل 1.")
            style_id = 1

    user_sessions[message.chat.id] = {
        "series": series_name,
        "next_num": start_num,
        "quality": quality,
        "style": style_id,
        "pad": 2,
    }

    preview = build_caption(user_sessions[message.chat.id], start_num)
    bot.reply_to(
        message,
        f"✅ تم حفظ الإعدادات:\n"
        f"• المسلسل: *{series_name}*\n"
        f"• يبدأ من: *{start_num:02d}*\n"
        f"• الدقة: *{quality}*\n"
        f"• الشكل: *{style_id}*\n\n"
        f"المعاينة:\n`{preview}`",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["style"])
def change_style(message):
    chat_id = message.chat.id
    if chat_id not in user_sessions:
        bot.reply_to(message, "اضبط المسلسل أولاً عبر `/set`.", parse_mode="Markdown")
        return

    arg = message.text.replace("/style", "", 1).strip()
    try:
        style_id = int(arg)
        if style_id not in STYLES:
            raise ValueError
    except ValueError:
        bot.reply_to(message, "اكتب رقماً من 1 إلى 4، مثال:\n`/style 1`", parse_mode="Markdown")
        return

    user_sessions[chat_id]["style"] = style_id
    preview = build_caption(user_sessions[chat_id], user_sessions[chat_id]["next_num"])
    bot.reply_to(message, f"✅ تم تغيير الشكل إلى *{style_id}*:\n`{preview}`", parse_mode="Markdown")


@bot.message_handler(commands=["quality"])
def change_quality(message):
    chat_id = message.chat.id
    if chat_id not in user_sessions:
        bot.reply_to(message, "اضبط المسلسل أولاً عبر `/set`.", parse_mode="Markdown")
        return

    new_quality = message.text.replace("/quality", "", 1).strip()
    if not new_quality:
        bot.reply_to(message, "اكتب الدقة بعد الأمر، مثال:\n`/quality 720p - HD`", parse_mode="Markdown")
        return

    user_sessions[chat_id]["quality"] = new_quality
    preview = build_caption(user_sessions[chat_id], user_sessions[chat_id]["next_num"])
    bot.reply_to(message, f"✅ تم تحديث الدقة إلى *{new_quality}*:\n`{preview}`", parse_mode="Markdown")


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
            "حدد أولاً المسلسل ورقم البداية عبر:\n`/set قلبي يحترق لأجله | 10 | 720p - HD | 1`",
            parse_mode="Markdown",
        )
        return

    session = user_sessions[chat_id]
    num = session["next_num"]
    caption = build_caption(session, num)
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
