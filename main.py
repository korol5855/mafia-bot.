import os
import logging
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import speech_recognition as sr
from pydub import AudioSegment

# Перевірка наявності токена
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не знайдено в середовищі!")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Ініціалізація розпізнавача мови
r = sr.Recognizer()

async def process_voice_file(file_path: str) -> str:
    """Конвертує аудіо у формат PCM WAV та розшифровує через Google Speech API"""
    wav_path = file_path + ".wav"
    try:
        # Конвертація через pydub (використовує вбудований ffmpeg)
        audio = AudioSegment.from_file(file_path)
        audio = audio.set_channels(1).set_frame_rate(16000)
        audio.export(wav_path, format="wav")

        # Розпізнавання мови
        with sr.AudioFile(wav_path) as source:
            audio_data = r.record(source)
            text = r.recognize_google(audio_data, language="uk-UA")
            return text
    finally:
        # Прибираємо тимчасові файли після обробки
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(wav_path):
            os.remove(wav_path)

@dp.message(F.voice)
async def handle_voice(message: Message):
    # Сповіщаємо користувача, що бот почав обробку
    processing_msg = await message.answer("⏳ Оброблюю голосове повідомлення...")

    try:
        # Отримуємо файл з Telegram
        file = await bot.get_file(message.voice.file_id)
        file_path = f"voice_{message.voice.file_id}.ogg"
        await bot.download(file, destination=file_path)

        # Транскрибуємо
        text = await process_voice_file(file_path)

        # Видаляємо статусне повідомлення та надсилаємо результат чистим текстом
        await processing_msg.delete()
        await message.reply(f"🗣 Розшифровка:\n\n{text}")

    except Exception as e:
        logging.error(f"Помилка розшифровки: {e}")
        
        # Створюємо інлайн-кнопку «Повторити» з file_id у callback_data
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Повторити спробу", callback_data=f"retry_{message.voice.file_id}")]
        ])
        
        await processing_msg.edit_text(
            "⚠️ Не вдалося розшифрувати голосове повідомлення (можливо, тимчасова проблема з API або занадто коротке аудіо).",
            reply_markup=keyboard
        )

@dp.callback_query(F.data.startswith("retry_"))
async def retry_transcription(callback: CallbackQuery):
    file_id = callback.data.split("_")[1]
    
    await callback.message.edit_text("⏳ Пробую розшифрувати повторно...")
    
    try:
        file = await bot.get_file(file_id)
        file_path = f"voice_{file_id}.ogg"
        await bot.download(file, destination=file_path)

        text = await process_voice_file(file_path)
        
        await callback.message.edit_text(f"🗣 Розшифровка (повторна спроба):\n\n{text}")
    except Exception as e:
        logging.error(f"Помилка при повторній спробі: {e}")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Спробувати ще", callback_data=f"retry_{file_id}")]
        ])
        await callback.message.edit_text("❌ Знову не вийшло. Спробуй пізніше або надішли інше повідомлення.", reply_markup=keyboard)
        
    await callback.answer()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
