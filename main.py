import os
import logging
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from groq import Groq

# Перевірка наявності токенів
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не знайдено в середовищі!")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY не знайдено в середовищі!")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Ініціалізація клієнта Groq
groq_client = Groq(api_key=GROQ_API_KEY)

async def process_voice_file(file_path: str) -> str:
    """Відправляє оригінальний аудіофайл напряму в Groq Whisper без конвертації"""
    try:
        with open(file_path, "rb") as file_to_transcribe:
            transcription = groq_client.audio.transcriptions.create(
                file=(file_path, file_to_transcribe.read()),
                model="whisper-large-v3",
                language="uk",
                temperature=0.0
            )
            return transcription.text
    finally:
        # Прибираємо тимчасовий файл
        if os.path.exists(file_path):
            os.remove(file_path)

@dp.message(F.voice)
async def handle_voice(message: Message):
    processing_msg = await message.answer("⏳ Оброблюю...")

    try:
        file = await bot.get_file(message.voice.file_id)
        file_path = f"voice_{message.voice.file_id}.ogg"
        await bot.download(file, destination=file_path)

        text = await process_voice_file(file_path)

        await processing_msg.delete()
        await message.reply(f"🗣 Розшифровка:\n\n{text}")

    except Exception as e:
        logging.error(f"Помилка розшифровки через Groq: {e}")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Повторити спробу", callback_data=f"retry_{message.voice.file_id}")]
        ])
        await processing_msg.edit_text("⚠️ Не вдалося розшифрувати через нейромережу.", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("retry_"))
async def retry_transcription(callback: CallbackQuery):
    file_id = callback.data.split("_")[1]
    await callback.message.edit_text("⏳ Пробую ще раз...")
    
    try:
        file = await bot.get_file(file_id)
        file_path = f"voice_{file_id}.ogg"
        await bot.download(file, destination=file_path)

        text = await process_voice_file(file_path)
        await callback.message.edit_text(f"🗣 Розшифровка:\n\n{text}")
    except Exception as e:
        logging.error(f"Помилка повтору: {e}")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Спробувати ще", callback_data=f"retry_{file_id}")]
        ])
        await callback.message.edit_text("❌ Знову помилка.", reply_markup=keyboard)
    await callback.answer()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
