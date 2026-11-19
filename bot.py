import telebot

BOT_TOKEN = "ضع_توكن_البوت_هنا"
bot = telebot.TeleBot(BOT_TOKEN)

# لكل مستخدم نحتفظ بإعدادات التسمية الحالية
# مثال: {"prefix": "مسلسل لعنة حب الحلقة", "next_num": 1, "pad": 1}
user_sessions = {}

@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    text = (
        "أهلاً بك 👋\n\n"
        "طريقة العمل:\n"
        "1️⃣ أرسل أمر التسمية بهذا الشكل:\n"
        "`/set مسلسل لعنة حب الحلقة | 1`\n\n"
        "2️⃣ بعدها أرسل الفيديوهات (حتى دفعة كاملة)، وسأعيد إرسالها لك مرتبة ومسمّاة تلقائياً.\n\n"
        "لإيقاف الوضع الحالي: `/stop`"
    )
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=["set"])
def set_naming(message):
    raw = message.text.replace("/set", "", 1).strip()
    if "|" not in raw:
        bot.reply_to(
            message,
            "اكتب الأمر هكذا:\n`/set مسلسل لعنة حب الحلقة | 1`",
            parse_mode="Markdown"
        )
        return

    parts = raw.split("|", 1)
    prefix = parts[0].strip()
    try:
        start_num = int(parts[1].strip())
    except ValueError:
        bot.reply_to(message, "الرقم بعد `|` يجب أن يكون عدداً صحيحاً، مثل `1`.", parse_mode="Markdown")
        return

    user_sessions[message.chat.id] = {
        "prefix": prefix,
        "next_num": start_num
    }
    bot.reply_to(
        message,
        f"✅ تم الضبط:\nالقاعدة: *{prefix}*\nسيبدأ الترقيم من: *{start_num}*\n\nالآن أرسل الفيديوهات.",
        parse_mode="Markdown"
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
            "حدد أولاً اسم العمل ورقم البداية عبر:\n`/set مسلسل لعنة حب الحلقة | 1`",
            parse_mode="Markdown"
        )
        return

    session = user_sessions[chat_id]
    num = session["next_num"]
    caption = f"{session['prefix']} {num}"
    session["next_num"] += 1

    file_id = message.video.file_id
    bot.send_video(
        chat_id=chat_id,
        video=file_id,
        caption=caption,
        supports_streaming=True
    )

bot.infinity_polling(skip_pending=True)
