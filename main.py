import asyncio
import os
import speech_recognition as sr
from aiogram import Bot, Dispatcher, F, types

TOKEN = "8700228403:AAESsiqBgXkBZFbm6RhQbDuJpp7zF51hCmc"

bot = Bot(token=TOKEN)
dp = Dispatcher()
recognizer = sr.Recognizer()

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    processing_msg = await message.reply("⏳ Перетворюю голос у текст...")
    
    file = await bot.get_file(message.voice.file_id)
    ogg_path = f"voice_{message.voice.file_id}.ogg"
    wav_path = f"voice_{message.voice.file_id}.wav"
    
    # Скачуємо голосове повідомлення
    await bot.download(file, destination=ogg_path)
    
    # Конвертуємо у формат WAV
    os.system(f"ffmpeg -i {ogg_path} {wav_path}")
    
    try:
        # Розпізнаємо голос українською мовою безкоштовно через Google
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="uk-UA")
            
            user_name = message.from_user.first_name
            await message.reply(f"🎙 **{user_name}:**\n{text}")
            
    except Exception as e:
        await message.reply("❌ Не вдалося розпізнати голосове повідомлення.")
    
    finally:
        if os.path.exists(ogg_path): 
            os.remove(ogg_path)
        if os.path.exists(wav_path): 
            os.remove(wav_path)
        try:
            await processing_msg.delete()
        except:
            pass

async def main():
    print("Бот запущено!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())