import os
import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
from groq import Groq

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice = update.message.voice or update.message.audio
    if not voice:
        return

    file = await context.bot.get_file(voice.file_id)
    file_bytes = await file.download_as_bytearray()

    try:
        # Без параметра language модель сама автоматично визначає мову 
        # і пише її "як є", без примусового перекладу чи спотворень.
        transcription = client.audio.transcriptions.create(
            file=("voice.ogg", bytes(file_bytes)),
            model="whisper-large-v3",
            response_format="text"
        )
        
        await update.message.reply_text(f"🗣 Розшифровка:\n\n{transcription}")
        
    except Exception as e:
        print(f"Error: {e}")
        await update.message.reply_text("Помилка при обробці голосового повідомлення.")

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    print("Бот запущено...")
    app.run_polling()
