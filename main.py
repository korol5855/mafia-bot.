import asyncio
import logging
import os
import tempfile
import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не знайдено в середовищі!")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()

async def transcribe_audio_file(ogg_path: str) -> str:
    try:
        # Надсилаємо оудіо напряму до Google Speech API без зайвих бібліотек
        url = "https://www.google.com/speech-api/v2/recognize?client=chromium&lang=uk-UA&output=json"
        
        with open(ogg_path, "rb") as f:
            audio_data = f.read()

        headers = {"Content-Type": "audio/ogg; codecs=opus"}
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=audio_data, headers=headers) as resp:
                if resp.status == 200:
                    response_text = await resp.text()
                    import json
                    for line in response_text.split("\n"):
                        if "result" in line:
                            data = json.loads(line)
                            if data.get("result"):
                                transcript = data["result"][0]["alternative"][0]["transcript"]
                                if transcript:
                                    return transcript
                                    
        return "⚠️ Не вдалося розпізнати мовлення."
    except Exception as e:
        return f"⚠️ Помилка обробки: {e}"

@dp.message(F.voice)
async def handle_voice(message: types.Message):
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
                    return

        transcribed_text = await transcribe_audio_file(ogg_path)
        await message.reply(f"🗣 **Розшифровка:**\n\n{transcribed_text}")

    except Exception as e:
        await message.reply(f"❌ Помилка: {e}")
    finally:
        if ogg_path and os.path.exists(ogg_path):
            os.remove(ogg_path)

async def main():
    logging.basicConfig(level=logging.INFO)
    print("Бот запущено!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
