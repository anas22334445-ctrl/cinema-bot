import os
import telebot
from telebot import types
import yt_dlp

TOKEN = "9183863222:AAE4v7z2s4bNuhMVH1-o48VUqV89C2rDmWA"
bot = telebot.TeleBot(TOKEN)

DOWNLOAD_DIR = "downloads"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

user_links = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    welcome_text = (
        "أهلاً بك يا بطل في بوت قناتك الخارق! 🔥🎬\n\n"
        "• أرسل لي رابط الفيديو وسأقوم بتحميله بدقة ممتازة تناسب القناة وبدون أخطاء حجم الملف."
    )
    bot.reply_to(message, welcome_text)

@bot.message_handler(func=lambda message: message.text and message.text.startswith("http"))
def handle_url(message):
    url = message.text.strip()
    user_id = message.from_user.id
    user_links[user_id] = url

    markup = types.InlineKeyboardMarkup(row_width=2)
    resolutions = ["720p", "480p", "360p", "best"]
    
    buttons = [types.InlineKeyboardButton(res, callback_data=f"dl_{res}") for res in resolutions]
    markup.add(*buttons)

    bot.reply_to(message, "🎬 <b>اختر دقة التحميل المناسبة:</b>", parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("dl_"))
def download_video(call):
    user_id = call.from_user.id
    if user_id not in user_links:
        bot.answer_callback_query(call.id, "انتهت صلاحية الرابط، أرسل الرابط من جديد.")
        return

    url = user_links[user_id]
    resolution = call.data.split("_")[1]

    bot.answer_callback_query(call.id, f"جاري بدء التحميل...")
    msg = bot.send_message(call.message.chat.id, f"⏳ <b>جاري سحب ومعالجة الفيديو... يرجى الانتظار</b>", parse_mode="HTML")

    if resolution == "720p":
        format_selector = 'best[height<=720]/best'
    elif resolution == "480p":
        format_selector = 'best[height<=480]/best'
    elif resolution == "360p":
        format_selector = 'best[height<=360]/best'
    else:
        format_selector = 'best'

    ydl_opts = {
        'format': format_selector,
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
        'geo_bypass': True,
        'nocheckcertificate': True,
        'noplaylist': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

        file_size = os.path.getsize(filename) / (1024 * 1024)
        if file_size > 49:
            bot.edit_message_text(f"⚠️ <b>عذراً، حجم الفيديو ({file_size:.1f}MB) أكبر من الحد المسموح للرفع المباشر (50MB).</b>", call.message.chat.id, msg.message_id, parse_mode="HTML")
            if os.path.exists(filename):
                os.remove(filename)
            return

        bot.edit_message_text("📤 <b>جاري رفع الفيديو إلى تيليغرام...</b>", call.message.chat.id, msg.message_id, parse_mode="HTML")
        
        with open(filename, 'rb') as video_file:
            bot.send_video(call.message.chat.id, video_file, caption="✅ تم التحميل بنجاح بواسطة بوت قناتك!")

        bot.delete_message(call.message.chat.id, msg.message_id)
        
        if os.path.exists(filename):
            os.remove(filename)

    except Exception as e:
        bot.edit_message_text(f"⚠️ <b>حدث خطأ أثناء التحميل:</b>\n\n<code>{str(e)}</code>", call.message.chat.id, msg.message_id, parse_mode="HTML")

if __name__ == "__main__":
    print("Bot is running...")
    bot.infinity_polling()
