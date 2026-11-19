import json
import json
import os
import threading
import time
from collections import defaultdict
from flask import Flask
import telebot

BOT_TOKEN = os.getenv("BOT_TOKEN", "8956783825:AAHS01rPwSdVztYF-k1jfdk9zGIBnfTqcgw")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATA_FILE = "series_state.json"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# تجميع رسائل المجموعات والدفعات السريعة
media_groups = defaultdict(list)
media_group_timers = {}
state_lock = threading.Lock()


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
        "quality": "HD",
    }


def save_state(state_data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state_data, f, ensure_ascii=False, indent=2)


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


def extract_media_info(message):
    if message.content_type == "video":
        return message.video.file_id, True
    if message.content_type == "document":
        mime = message.document.mime_type or ""
        if mime.startswith("video/"):
            return message.document.file_id, False
    return None, None


def process_batch(batch_key, chat_id):
    # انتظار قصير لضمان وصول بقية عناصر الدفعة/المجموعة
    time.sleep(1.5)

    with state_lock:
        items = media_groups.pop(batch_key, [])
        media_group_timers.pop(batch_key, None)

    if not items:
        return

    # ترتيب الرسائل حسب رقم الرسالة لضمان التسلسل الصحيح
    items.sort(key=lambda m: m.message_id)

    sent_count = 0
    first_ep = None
    last_ep = None
    failed_count = 0

    for msg in items:
        file_id, is_video = extract_media_info(msg)
        if not file_id:
            continue

        with state_lock:
            ep_num = state["next_ep"]
            caption = build_caption(state["series"], ep_num, state["quality"])

        try:
            if is_video:
                bot.send_video(
                    chat_id=chat_id,
                    video=file_id,
                    caption=caption,
                    supports_streaming=True,
                )
            else:
                bot.send_document(
                    chat_id=chat_id,
                    document=file_id,
                    caption=caption,
                )

            with state_lock:
                if first_ep is None:
                    first_ep = ep_num
                last_ep = ep_num
                state["next_ep"] = ep_num + 1
                save_state(state)

            sent_count += 1
            # فاصل خفيف جداً لتجنب قيود سرعة تلغرام عند إرسال عدد كبير
            time.sleep(0.35)

        except Exception as e:
            failed_count += 1
            bot.send_message(
                chat_id=chat_id,
                text=f"❌ تعذر إرسال الحلقة ({format_ep(ep_num)}):\n`{e}`",
                parse_mode="Markdown",
            )

    if sent_count > 0:
        if sent_count == 1:
            summary = (
                f"✔️ تم تجهيز الحلقة ({format_ep(first_ep)}) — "
                f"التالية ستكون ({format_ep(state['next_ep'])})"
            )
        else:
            summary = (
                f"✔️ تم تجهيز **{sent_count}** حلقات من **({format_ep(first_ep)})** "
                f"إلى **({format_ep(last_ep)})**\n"
                f"🔢 الحلقة التالية ستكون: **({format_ep(state['next_ep'])})**"
            )
        if failed_count > 0:
            summary += f"\n⚠️ فشل إرسال {failed_count} ملف."

        bot.send_message(chat_id=chat_id, text=summary, parse_mode="Markdown")


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
        "🎬 **بوت ترقيم وحلقات المسلسلات**\n\n"
        f"📌 **المسلسل الحالي:** {series_val}\n"
        f"🔢 **الحلقة القادمة:** ({next_ep_val})\n"
        f"🎞 **الجودة:** ({quality_val})\n"
        f"📝 **الكابشن القادم:**\n`{current_cap}`\n\n"
        "**الأوامر المتاحة:**\n"
        "• `/setseries اسم المسلسل` — لتغيير اسم المسلسل\n"
        "• `/setep 9` — لتحديد رقم الحلقة القادمة\n"
        "• `/setquality 720p` — لتغيير الجودة\n"
        "• `/status` — لعرض الإعدادات الحالية\n\n"
        "📥 أرسل فيديو واحداً أو **عدة فيديوهات دفعة واحدة** وسأرتبها وأرقمها تلقائياً!"
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

    with state_lock:
        state["series"] = parts[1].strip()
        save_state(state)
        series_val = state["series"]

    bot.reply_to(message, f"✅ تم تحديث اسم المسلسل إلى: **{series_val}**", parse_mode="Markdown")


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
        bot.reply_to(message, "⚠️ اكتب الجودة بعد الأمر، مثال:\n`/setquality HD` أو `/setquality 720p`", parse_mode="Markdown")
        return

    with state_lock:
        state["quality"] = parts[1].strip()
        save_state(state)
        quality_val = state["quality"]

    bot.reply_to(message, f"✅ تم تحديث الجودة إلى: **({quality_val})**", parse_mode="Markdown")


@bot.message_handler(content_types=["video", "document"])
def handle_media(message):
    if not is_admin(message):
        return

    file_id, _ = extract_media_info(message)
    if not file_id:
        bot.reply_to(message, "⚠️ يرجى إرسال ملف فيديو فقط.")
        return

    chat_id = message.chat.id
    # إذا كانت الرسالة ضمن ألبوم نجمعها حسب media_group_id، وإلا نجمع الدفعات السريعة لنفس المستخدم
    batch_key = f"{chat_id}:{message.media_group_id}" if message.media_group_id else f"{chat_id}:single"

    with state_lock:
        media_groups[batch_key].append(message)
        if batch_key not in media_group_timers:
            t = threading.Thread(target=process_batch, args=(batch_key, chat_id), daemon=True)
            media_group_timers[batch_key] = t
            t.start()


def run_bot():
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
