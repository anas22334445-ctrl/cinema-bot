import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ضع هنا توكن البوت الخاص بك
TOKEN = os.getenv("9183863222:AAE4v7z2s4bNuhMVH1-o48vUqV89C2rDmWA")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# قاعدة بيانات مؤقتة لتخزين الغلاف وقائمة الحلقات
# (في حال رغبت بربط قاعدة بيانات حقيقية لاحقاً مثل SQLite أو MongoDB)
db = {
    "cover_file_id": None,
    "episodes": []  # قائمة تخزن بيانات الحلقات: [{"title": "الحلقة 1", "file_id": "..."}]
}

# حالات المحادثة (FSM)
class AdminStates(StatesGroup):
    waiting_for_cover = State()
    waiting_for_videos = State()

# أمر البدء /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = [
        [InlineKeyboardButton(text="🎬 استعراض الحلقات", callback_data="list_episodes")],
        [InlineKeyboardButton(text="⚙️ تعيين غلاف جديد (للمشرف)", callback_data="set_cover")],
        [InlineKeyboardButton(text="➕ إضافة حلقات جديدة (للمشرف)", callback_data="add_episodes")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=kb)
    
    welcome_text = (
        "أهلاً بك في بوت الأرشيف الخاص بقناتك! 🎥✨\n"
        "يمكن للمتابعين تصفح الحلقات ومشاهدتها بالأحجام الأصلية والغلاف الثابت."
    )
    
    if db["cover_file_id"]:
        await message.answer_photo(photo=db["cover_file_id"], caption=welcome_text, reply_markup=keyboard)
    else:
        await message.answer(welcome_text, reply_markup=keyboard)

# زر تعيين الغلاف
@dp.callback_query(F.data == "set_cover")
async def callback_set_cover(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📸 أرسل الآن صورة الغلاف (البوستر الثابت) ليتم اعتمادها لكل الحلقات:")
    await state.set_state(AdminStates.waiting_for_cover)
    await callback.answer()

# استقبال الغلاف من المشرف
@dp.message(AdminStates.waiting_for_cover, F.photo)
async def process_cover(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    db["cover_file_id"] = photo_id
    await state.clear()
    await message.answer("✅ تم حفظ الغلاف الثابت بنجاح! يمكنك الآن بدء إرسال الحلقات عبر خيار (إضافة حلقات جديدة).")

# زر إضافة حلقات
@dp.callback_query(F.data == "add_episodes")
async def callback_add_episodes(callback: types.CallbackQuery, state: FSMContext):
    if not db["cover_file_id"]:
        await callback.message.answer("⚠️ تنبيه: يجب تعيين الغلاف أولاً قبل إضافة الحلقات!")
        await callback.answer()
        return
    
    await callback.message.answer("📤 أرسل الفيديوهات الآن (يمكنك إرسالها واحداً تلو الآخر)، وعند الانتهاء اضغط على /done")
    await state.set_state(AdminStates.waiting_for_videos)
    await callback.answer()

# استقبال الفيديوهات وترقيمها تلقائياً
@dp.message(AdminStates.waiting_for_videos, F.video)
async def process_video(message: types.Message):
    video_id = message.video.file_id
    ep_number = len(db["episodes"]) + 1
    ep_title = f"الحلقة {ep_number}"
    
    db["episodes"].append({
        "title": ep_title,
        "file_id": video_id
    })
    
    await message.reply(f"✅ تمت إضافة ({ep_title}) بنجاح برقمها التلقائي!")

# إنهاء عملية إرسال الفيديوهات
@dp.message(AdminStates.waiting_for_videos, Command("done"))
async def finish_videos(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(f"🎉 تم الانتهاء من حفظ الحلقات! الإجمالي الحالي: {len(db['episodes'])} حلقة مرقمة ومربوطة بالغلاف.")

# عرض قائمة الحلقات للمتابعين
@dp.callback_query(F.data == "list_episodes")
async def callback_list_episodes(callback: types.CallbackQuery):
    if not db["episodes"]:
        await callback.message.answer("📭 عذراً، لا توجد حلقات مضافة حتى الآن في الأرشيف.")
        await callback.answer()
        return

    # إنشاء أزرار لكل حلقة
    buttons = []
    for index, ep in enumerate(db["episodes"]):
        buttons.append([InlineKeyboardButton(text=ep["title"], callback_data=f"watch_{index}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="main_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await callback.message.answer("🎬 اختر الحلقة التي تريد مشاهدتها من الأرشيف:", reply_markup=keyboard)
    await callback.answer()

# تشغيل حلقة معينة عند ضغط زرها
@dp.callback_query(F.data.startswith("watch_"))
async def watch_episode(callback: types.CallbackQuery):
    ep_index = int(callback.data.split("_")[1])
    ep = db["episodes"][ep_index]
    
    # إرسال الفيديو مع الغلاف الثابت الذي حفظناه مسبقاً وبدون أي ضغط
    if db["cover_file_id"]:
        await callback.message.answer_photo(
            photo=db["cover_file_id"],
            caption=f"📺 {ep['title']}\n✨ تم الرفع بالحجم الأصلي عبر أرشيف Cinema Drama plus"
        )
    
    await callback.message.answer_video(
        video=ep["file_id"],
        caption=f"▶️ {ep['title']}"
    )
    await callback.answer()

# العودة للقائمة الرئيسية
@dp.callback_query(F.data == "main_menu")
async def back_to_main(callback: types.CallbackQuery):
    await cmd_start(callback.message)
    await callback.answer()

if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))
