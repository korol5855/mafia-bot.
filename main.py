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
    """Конвертує аудіо, додає тишу в кінці та розшифровує через Google Speech API"""
    wav_path = file_path + ".wav"
    try:
        # Завантажуємо аудіо
        audio = AudioSegment.from_file(file_path)
        
        # Додаємо 500мс тиші в кінці, щоб Google не ковтав останні слова
        silence = AudioSegment.silent(duration=500)
        audio = audio + silence
        
        # Конвертуємо у формат PCM WAV
        audio = audio.set_channels(1).set_frame_rate(16000)
        audio.export(wav_path, format="wav")

        # Розпізнавання мови
        with sr.AudioFile(wav_path) as source:
            audio_data = r.record(source)
            text = r.recognize_google(audio_data, language="uk-UA")
            return text
    finally:
        # Прибираємо тимчасові файли
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(wav_path):
            os.remove(wav_path)

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
        logging.error(f"Помилка розшифровки: {e}")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Повторити спробу", callback_data=f"retry_{message.voice.file_id}")]
        ])
        await processing_msg.edit_text("⚠️ Не вдалося розшифрувати.", reply_markup=keyboard)

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
