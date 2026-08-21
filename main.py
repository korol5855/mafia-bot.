import asyncio
import logging
from aiogram import Bot, Dispatcher, F, types

TOKEN = "8700228403:AAESsiqBgXKZFBm6RhQbDuJpp7zF51hCmc"

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    # Поки що перевіряємо, чи бачить бот голосові з хмари
    await message.reply("🎙 Голосове повідомлення отримано на сервері! Працюємо над перекладом...")

async def main():
    logging.basicConfig(level=logging.INFO)
    print("Бот запущено в хмарі!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
