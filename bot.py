import json
import json
import os
import telebot

# ضع توكن البوت هنا أو في متغيرات البيئة (Environment Variables)
BOT_TOKEN = os.getenv("BOT_TOKEN", "8956783825:AAHS01rPwSdVztYF-k1jfdk9zGIBnfTqcgw")

# ضع آيدي حسابك (أدمن البوت) كرقم صحيح، مثل: 123456789
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DATA_FILE = "series_state.json"

bot = telebot.TeleBot(BOT_TOKEN)


def load_state():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # يبدأ من الحلقة 8 لأنك حددتها في مثالك
    return {
        "series": "أمنية حب",
        "next_ep": 8,
        "quality": "HD",
    }


def save_state(state):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


state = load_state()


def is_admin(message):
    if ADMIN_ID == 0:
        return True
    return message.from_user and message.from_user.id == ADMIN_ID


def format_ep(num):
    num_int = int(num)
    return f"{num_int:02d}" if num_int < 100 else str(num_int)


def build_caption(series_name, ep_num, quality):
    return f"{series_name} — الحلقة ({format_ep(ep_num)}) — الجودة ({quality})"


@bot.message_handler(commands=["start", "help", "status"])
def send_welcome(message):
    if not is_admin(message):
        return

    current_cap = build_caption(state["series"], state["next_ep"], state["quality"])
    text = (
        "🎬 **بوت ترقيم وحلقات المسلسلات**\n\n"
        f"📌 **المسلسل الحالي:** {state['series']}\n"
        f"🔢 **الحلقة القادمة:** ({format_ep(state['next_ep'])})\n"
        f"🎞 **الجودة:** ({state['quality']})\n"
        f"📝 **الكابشن القادم:**\n`{current_cap}`\n\n"
        "**الأوامر المتاحة:**\n"
        "• `/setseries اسم المسلسل` — لتغيير اسم المسلسل\n"
        "• `/setep 9` — لتحديد رقم الحلقة القادمة\n"
        "• `/setquality 720p` — لتغيير الجودة (مثلاً HD أو 720p)\n"
        "• `/status` — لعرض الإعدادات الحالية\n\n"
        "📥 فقط أرسل **الفيديو** الآن وسأعيد إرساله بالكابشن الجاهز وأزيد الرقم تلقائياً!"
    )
    bot.reply_to(message, text, parse_mode="Markdown")


@bot.message_handler(commands=["setseries"])
def set_series(message):
    if not is_admin(message):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message, "⚠️ اكتب الاسم بعد الأمر، مثال:\n`/setseries أمنية حب`", parse_mode="Markdown")
        return

    state["series"] = parts[1].strip()
    save_state(state)
    bot.reply_to(message, f"✅ تم تحديث اسم المسلسل إلى: **{state['series']}**", parse_mode="Markdown")


@bot.message_handler(commands=["setep"])
def set_episode(message):
    if not is_admin(message):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        bot.reply_to(message, "⚠️ اكتب رقم الحلقة بشكل صحيح، مثال:\n`/setep 8`", parse_mode="Markdown")
        return

    ep_val = int(parts[1].strip())
    if ep_val < 1:
        bot.reply_to(message, "⚠️ رقم الحلقة يجب أن يكون 1 أو أكثر.")
        return

    state["next_ep"] = ep_val
    save_state(state)
    bot.reply_to(message, f"✅ تم ضبط الحلقة القادمة لتكون: **({format_ep(ep_val)})**", parse_mode="Markdown")


@bot.message_handler(commands=["setquality"])
def set_quality(message):
    if not is_admin(message):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message, "⚠️ اكتب الجودة بعد الأمر، مثال:\n`/setquality HD` أو `/setquality 720p`", parse_mode="Markdown")
        return

    state["quality"] = parts[1].strip()
    save_state(state)
    bot.reply_to(message, f"✅ تم تحديث الجودة إلى: **({state['quality']})**", parse_mode="Markdown")


@bot.message_handler(content_types=["video", "document"])
def handle_media(message):
    if not is_admin(message):
        return

    file_id = None
    is_video = False

    if message.content_type == "video":
        file_id = message.video.file_id
        is_video = True
    elif message.content_type == "document":
        mime = message.document.mime_type or ""
        if mime.startswith("video/"):
            file_id = message.document.file_id
            is_video = False
        else:
            bot.reply_to(message, "⚠️ يرجى إرسال ملف فيديو فقط.")
            return

    ep_num = state["next_ep"]
    caption = build_caption(state["series"], ep_num, state["quality"])

    try:
        if is_video:
            bot.send_video(
                chat_id=message.chat.id,
                video=file_id,
                caption=caption,
                supports_streaming=True,
            )
        else:
            bot.send_document(
                chat_id=message.chat.id,
                document=file_id,
                caption=caption,
            )

        # زيادة رقم الحلقة تلقائياً بعد نجاح الإرسال
        state["next_ep"] = ep_num + 1
        save_state(state)

        bot.send_message(
            chat_id=message.chat.id,
            text=f"✔️ تم تجهيز الحلقة ({format_ep(ep_num)}) — التالية ستكون ({format_ep(state['next_ep'])})",
        )

    except Exception as e:
        bot.reply_to(message, f"❌ حدث خطأ أثناء الإرسال:\n`{e}`", parse_mode="Markdown")


print("Bot is running...")
bot.infinity_polling(skip_pending=True)
