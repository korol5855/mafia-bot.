import asyncio
import logging
from aiogram import Bot, Dispatcher, F, types
import aiohttp

TOKEN = "8700228403:AAESsiqBgXkBZFbm6RhQbDuJpp7zF51hCmc"

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    # Сповіщаємо, що файл прийнято
    status_msg = await message.reply("⏳ Завантажую та обробляю голосове...")
    
    try:
        # Отримуємо файл від Telegram
        file_id = message.voice.file_id
        file = await bot.get_file(file_id)
        file_path = file.file_path
        
        # Скачуємо файл напряму через Telegram API без конвертації у важкі програми
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as resp:
                if resp.status == 200:
                    audio_bytes = await resp.read()
                    # Тут робимо базову обробку і надсилаємо попередження про успіх
                    await message.reply("✅ Голосове успішно завантажено на сервер! Зараз налаштуємо фінальний вивід тексту.")
                else:
                    await message.reply("❌ Не вдалося завантажити файл з серверов Telegram.")
    except Exception as e:
        await message.reply(f"❌ Сталася помилка: {str(e)}")
    finally:
        await status_msg.delete()

async def main():
    logging.basicConfig(level=logging.INFO)
    print("Бот запущено!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
