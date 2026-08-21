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

AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()

# Забираємо токен із змінних середовища Render
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не знайдено в середовищі!")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()

def transcribe_audio_file(ogg_path: str) -> str:
    wav_path = ogg_path + ".wav"
    try:
        sound = AudioSegment.from_file(ogg_path, format="ogg")
        sound.export(wav_path, format="wav")

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="uk-UA")
            return text
    except sr.UnknownValueError:
        return "⚠️ Не вдалося розпізнати мовлення."
    except sr.RequestError as e:
        return f"⚠️ Помилка сервісу розпізнавання: {e}"
    except Exception as e:
        return f"⚠️ Помилка обробки аудіо: {e}"
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    status_msg = await message.reply("⏳ Розпізнаю голосове повідомлення...")
    ogg_path = None
    try:
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
                    await status_msg.edit_text("❌ Помилка завантаження файлу.")
                    return

        transcribed_text = await asyncio.to_thread(transcribe_audio_file, ogg_path)
        await message.reply(f"🗣 **Розшифровка:**\n\n{transcribed_text}")

    except Exception as e:
        await message.reply(f"❌ Виникла помилка: {str(e)}")
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass
        if ogg_path and os.path.exists(ogg_path):
            os.remove(ogg_path)

async def main():
    logging.basicConfig(level=logging.INFO)
    print("Бот запускається...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
