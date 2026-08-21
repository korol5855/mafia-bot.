import asyncio
import logging
import os
import tempfile
import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import speech_recognition as sr
from pydub import AudioSegment
import imageio_ffmpeg

# Налаштування конвертера для роботи на Render
AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()

# Отримання токена з налаштувань Render
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не знайдено в налаштуваннях середовища!")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()

def transcribe_audio_file(ogg_path: str) -> str:
    wav_path = ogg_path + ".wav"
    try:
        # Конвертація в wav
        sound = AudioSegment.from_file(ogg_path, format="ogg")
        sound.export(wav_path, format="wav")

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            
            # Спробуємо розпізнати: пріоритет на ru-RU (краще підходить для суржику та коротких фраз)
            try:
                return recognizer.recognize_google(audio_data, language="ru-RU")
            except sr.UnknownValueError:
                # Якщо не вийшло, пробуємо uk-UA
                try:
                    return recognizer.recognize_google(audio_data, language="uk-UA")
                except:
                    return "⚠️ Не вдалося розпізнати мову."
            except Exception:
                return "⚠️ Помилка розпізнавання."

    except Exception as e:
        return f"⚠️ Помилка обробки: {e}"
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    ogg_path = None
    try:
        # Отримання файлу з Telegram
        file_id = message.voice.file_id
        file_info = await bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
            ogg_path = tmp_file.name

        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    with open(ogg_path, "wb") as f:
                        f.write(content)
                else:
                    return

        # Переклад (без статусних повідомлень)
        transcribed_text = await asyncio.to_thread(transcribe_audio_file, ogg_path)
        await message.reply(f"🗣 **Розшифровка:**\n\n{transcribed_text}")

    except Exception as e:
        print(f"Помилка в обробці: {e}")
    finally:
        if ogg_path and os.path.exists(ogg_path):
            os.remove(ogg_path)

async def main():
    logging.basicConfig(level=logging.INFO)
    print("Бот запущено!")
    # Скидаємо вебхуки, щоб уникнути конфліктів
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
