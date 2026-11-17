import os
import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)

user_links = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message,
        "أهلاً بك يا بطل في بوت قناتك الخارق! 🎬🔥\n\n"
        "• أرسل لي **رابط فيديو مفرد** لاختيار الدقة وتحميله.\n"
        "• أو أرسل لي **رابط قائمة تشغيل (Playlist)** لسحب وتحميل الحلقات دفعة واحدة!\n\n"
        "جميع الدقات مدعومة (من 4K وحتى 144p)."
    )

@bot.message_handler(func=lambda message: 'youtube.com' in message.text or 'youtu.be' in message.text)
def handle_youtube_link(message):
    user_id = message.from_user.id
    user_links[user_id] = message.text

    if 'playlist' in message.text or 'list=' in message.text:
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("📥 تحميل قائمة التشغيل كاملة (720p)", callback_data="pl_720"),
            InlineKeyboardButton("📥 تحميل قائمة التشغيل كاملة (480p)", callback_data="pl_480")
        )
        bot.reply_to(message, "📂 يبدو أن هذا رابط **قائمة تشغيل (Playlist)**! اختر الجودة المناسبة لتحميل الحلقات:", reply_markup=markup)
    else:
        markup = InlineKeyboardMarkup()
        markup.row_width = 3
        markup.add(
            InlineKeyboardButton("4K", callback_data="res_2160"),
            InlineKeyboardButton("2K", callback_data="res_1440"),
            InlineKeyboardButton("1080p", callback_data="res_1080"),
            InlineKeyboardButton("720p", callback_data="res_720"),
            InlineKeyboardButton("640p", callback_data="res_640"),
            InlineKeyboardButton("576p", callback_data="res_576"),
            InlineKeyboardButton("468p", callback_data="res_468"),
            InlineKeyboardButton("360p", callback_data="res_360"),
            InlineKeyboardButton("288p", callback_data="res_288"),
            InlineKeyboardButton("244p", callback_data="res_244"),
            InlineKeyboardButton("144p", callback_data="res_144")
        )
        bot.reply_to(message, "🎬 اختر دقة التحميل المطلوبة للحلقة:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith(('res_', 'pl_')))
def callback_query(call):
    user_id = call.from_user.id
    if user_id not in user_links:
        bot.answer_callback_query(call.id, "انتهت صلاحية الرابط، أرسل الرابط من جديد.")
        return

    url = user_links[user_id]
    action_type, resolution = call.data.split('_')

    if action_type == 'res':
        bot.answer_callback_query(call.id, f"جاري تحميل دقة {resolution}...")
        msg = bot.send_message(call.message.chat.id, f"⏳ جاري تجهيز الفيديو بجودة {resolution}p، يرجى الانتظار...")

        output_template = f"video_{user_id}.mp4"
        ydl_opts = {
            'format': f'bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]/best',
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            if os.path.exists(output_template):
                with open(output_template, 'rb') as f:
                    bot.send_video(call.message.chat.id, f, caption=f"✨ تم التحميل بنجاح (جودة {resolution}p) | Cinema Drama plus")
                os.remove(output_template)
            else:
                for file in os.listdir('.'):
                    if file.startswith(f"video_{user_id}"):
                        with open(file, 'rb') as f:
                            bot.send_video(call.message.chat.id, f, caption=f"✨ تم التحميل بنجاح | Cinema Drama plus")
                        os.remove(file)
                        break

            bot.delete_message(call.message.chat.id, msg.message_id)
        except Exception as e:
            bot.edit_message_text(f"⚠️ حدث خطأ أثناء التحميل:\n{e}", call.message.chat.id, msg.message_id)
            if os.path.exists(output_template):
                os.remove(output_template)

    elif action_type == 'pl':
        bot.answer_callback_query(call.id, "بدء تحميل قائمة التشغيل...")
        msg = bot.send_message(call.message.chat.id, f"📂 جاري سحب قائمة التشغيل كاملة بجودة {resolution}p، قد يستغرق ذلك بعض الوقت...")

        ydl_opts = {
            'format': f'bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]/best',
            'outtmpl': f'playlist_{user_id}_%(title)s.%(ext)s',
            'merge_output_format': 'mp4',
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            bot.send_message(call.message.chat.id, "✅ تم تحميل قائمة التشغيل وتجهيز الملفات بنجاح لقناتك!")
            bot.delete_message(call.message.chat.id, msg.message_id)
            
            for file in os.listdir('.'):
                if file.startswith(f"playlist_{user_id}"):
                    try:
                        os.remove(file)
                    except:
                        pass
        except Exception as e:
            bot.edit_message_text(f"⚠️ حدث خطأ أثناء تحميل القائمة:\n{e}", call.message.chat.id, msg.message_id)

bot.infinity_polling()
