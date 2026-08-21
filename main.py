import os
import asyncio
import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from groq import AsyncGroq


# ============================================================
# НАЛАШТУВАННЯ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("Не знайдено BOT_TOKEN")

if not GROQ_API_KEY:
    raise RuntimeError("Не знайдено GROQ_API_KEY")


client = AsyncGroq(api_key=GROQ_API_KEY)

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
transcription_semaphore = asyncio.Semaphore(3)


# ============================================================
# ЛОГУВАННЯ
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# РОЗБИВАЄМО ДОВГИЙ ТЕКСТ
# ============================================================

def split_text(text: str, max_length: int = 4000) -> list[str]:
    if len(text) <= max_length:
        return [text]

    parts = []
    while len(text) > max_length:
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length

        parts.append(text[:split_at].strip())
        text = text[split_at:].strip()

    if text:
        parts.append(text)

    return parts


# ============================================================
# ОТРИМАННЯ ІНФОРМАЦІЇ ПРО АУДІО
# ============================================================

def get_audio_info(message):
    if message.voice:
        return {
            "file_id": message.voice.file_id,
            "file_size": message.voice.file_size,
            "file_name": "voice.ogg",
        }
    if message.audio:
        return {
            "file_id": message.audio.file_id,
            "file_size": message.audio.file_size,
            "file_name": message.audio.file_name or "audio.mp3",
        }
    return None


# ============================================================
# ОСНОВНА ЛОГІКА РОЗШИФРОВКИ (З АВТОПОВТОРАМИ)
# ============================================================

async def process_and_reply(target_message, audio_info, context: ContextTypes.DEFAULT_TYPE):
    file_size = audio_info["file_size"]

    if file_size and file_size > MAX_FILE_SIZE:
        await target_message.reply_text(
            "❌ Файл занадто великий.\n\n"
            "Максимальний розмір — 20 МБ."
        )
        return

    max_retries = 3
    text = None

    for attempt in range(max_retries):
        try:
            async with transcription_semaphore:
                telegram_file = await context.bot.get_file(audio_info["file_id"])
                file_bytes = await telegram_file.download_as_bytearray()

                transcription = await client.audio.transcriptions.create(
                    file=(
                        audio_info["file_name"],
                        bytes(file_bytes),
                    ),
                    model="whisper-large-v3",
                    response_format="text",
                )

            text = str(transcription).strip()
            if text:
                break

        except Exception as e:
            logger.warning(f"Спроба {attempt + 1} з {max_retries} не вдалася: {e}")
            if attempt == max_retries - 1:
                raise e
            await asyncio.sleep(1.5)

    if not text:
        keyboard = [[InlineKeyboardButton("🔄 Спробувати ще раз", callback_data="retry_transcription")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await target_message.reply_text(
            "Не вдалося розпізнати голосове повідомлення.",
            reply_markup=reply_markup
        )
        return

    parts = split_text(text)
    for part in parts:
        await target_message.reply_text(part)


# ============================================================
# ОБРОБНИКИ ПОВІДОМЛЕНЬ ТА КНОПОК
# ============================================================

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    audio_info = get_audio_info(message)
    if not audio_info:
        return

    user = message.from_user
    username = f"@{user.username}" if user and user.username else (str(user.id) if user else "unknown")
    logger.info("Нове голосове від %s", username)

    await process_and_reply(message, audio_info, context)


async def retry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Повторюємо розшифровку...")

    bot_message = query.message
    original_message = bot_message.reply_to_message

    if not original_message:
        await bot_message.edit_text("❌ Не вдалося знайти оригінальне голосове повідомлення для повтору.")
        return

    audio_info = get_audio_info(original_message)
    if not audio_info:
        await bot_message.edit_text("❌ Оригінальний файл більше недоступний.")
        return

    await bot_message.edit_text("⏳ Пробуємо знову...")
    await process_and_reply(bot_message, audio_info, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Необроблена помилка:", exc_info=context.error)


# ============================================================
# ЗАПУСК БОТА
# ============================================================

def main():
    logger.info("Запуск бота...")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_voice))
    app.add_handler(CallbackQueryHandler(retry_callback, pattern="^retry_transcription$"))
    app.add_error_handler(error_handler)

    logger.info("Бот запущено 🚀")
    app.run_polling()


if __name__ == "__main__":
    main()
        
