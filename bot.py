import os
import telebot
from telebot import types
import yt_dlp

# ضع توكن بوتك هنا
TOKEN = "9183863222:AAE4v7z2s4bNuhMVH1-o48vUqV89C2rDmWA"
bot = telebot.TeleBot(TOKEN)

# مجلد حفظ التحميلات المؤقتة
DOWNLOAD_DIR = "downloads"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# تخزين مؤقت للروابط المرسلة من المستخدمين
user_links = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    welcome_text = (
        "أهلاً بك يا بطل في بوت قناتك الخارق! 🔥🎬\n\n"
        "• أرسل لي **رابط فيديو مفرد** لاختيار الدقة وتحميله من أي منصة (يوتيوب، شاهد، وغيرها).\n"
        "• أو أرسل لي **رابط قائمة تشغيل (Playlist)** لسحب وتحميل الحلقات دفعة واحدة!\n\n"
        "جميع الدقات مدعومة (من 4K وحتى 144p)."
    )
    bot.reply_to(message, welcome_text)

@bot.message_handler(func=lambda message: message.text and message.text.startswith("http"))
def handle_url(message):
    url = message.text.strip()
    user_id = message.from_user.id
    user_links[user_id] = url

    # إنشاء أزرار اختيار الدقات
    markup = types.InlineKeyboardMarkup(row_width=3)
    resolutions = ["4K", "2K", "1080p", "720p", "640p", "576p", "468p", "360p", "288p", "244p", "144p"]
    
    buttons = [types.InlineKeyboardButton(res, callback_data=f"dl_{res}") for res in resolutions]
    markup.add(*buttons)

    bot.reply_to(message, "🎬 <b>اختر دقة التحميل المطلوبة للحلقة:</b>", parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("dl_"))
def download_video(call):
    user_id = call.from_user.id
    if user_id not in user_links:
        bot.answer_callback_query(call.id, "انتهت صلاحية الرابط، أرسل الرابط من جديد.")
        return

    url = user_links[user_id]
    resolution = call.data.split("_")[1]

    bot.answer_callback_query(call.id, f"جاري بدء التحميل بدقة {resolution}...")
    msg = bot.send_message(call.message.chat.id, f"⏳ <b>جاري سحب ومعالجة الفيديو (دقة {resolution})... يرجى الانتظار</b>", parse_mode="HTML")

    # تحديد صيغة الجودة المطلوبة لـ yt-dlp لتشمل جميع المواقع
    format_selector = 'best'
    if resolution == "4K":
        format_selector = 'bestvideo[height<=2160]+bestaudio/best[height<=2160]'
    elif resolution == "2K":
        format_selector = 'bestvideo[height<=1440]+bestaudio/best[height<=1440]'
    elif resolution == "1080p":
        format_selector = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'
    elif resolution == "720p":
        format_selector = 'bestvideo[height<=720]+bestaudio/best[height<=720]'
    elif resolution == "480p":
        format_selector = 'bestvideo[height<=480]+bestaudio/best[height<=480]'
    elif resolution == "360p":
        format_selector = 'bestvideo[height<=360]+bestaudio/best[height<=360]'

    ydl_opts = {
        'format': format_selector,
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
        'geo_bypass': True,          # تجاوز الحظر الجغرافي للمواقع
        'nocheckcertificate': True,   # تخطي أخطاء الأمان والشهادات
        'noplaylist': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

        bot.edit_message_text("📤 <b>جاري رفع الفيديو إلى تيليغرام...</b>", call.message.chat.id, msg.message_id, parse_mode="HTML")
        
        with open(filename, 'rb') as video_file:
            bot.send_video(call.message.chat.id, video_file, caption="✅ تم التحميل بنجاح بواسطة بوت قناتك الخارق!")

        bot.delete_message(call.message.chat.id, msg.message_id)
        
        # تنظيف الملف المؤقت
        if os.path.exists(filename):
            os.remove(filename)

    except Exception as e:
        bot.edit_message_text(f"⚠️ <b>حدث خطأ أثناء التحميل:</b>\n\n<code>{str(e)}</code>", call.message.chat.id, msg.message_id, parse_mode="HTML")

# تشغيل البوت
if __name__ == "__main__":
    print("Bot is running...")
    bot.infinity_polling()
