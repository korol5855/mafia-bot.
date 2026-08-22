import os
import asyncio
import logging

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
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

# Максимум 3 розшифровки одночасно
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
# РОЗБИВАННЯ ДОВГОГО ТЕКСТУ
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

    # Telegram Voice
    if message.voice:

        return {
            "file_id": message.voice.file_id,
            "file_size": message.voice.file_size,
            "file_name": "voice.ogg",
        }

    # Звичайний аудіофайл
    if message.audio:

        return {
            "file_id": message.audio.file_id,
            "file_size": message.audio.file_size,
            "file_name": message.audio.file_name or "audio.mp3",
        }

    return None


# ============================================================
# РОЗШИФРОВКА
# ============================================================

async def process_and_reply(
    target_message,
    audio_info,
    context: ContextTypes.DEFAULT_TYPE,
):

    file_size = audio_info["file_size"]

    # --------------------------------------------------------
    # Перевірка розміру
    # --------------------------------------------------------

    if file_size and file_size > MAX_FILE_SIZE:

        await target_message.reply_text(
            "❌ Файл занадто великий.\n\n"
            "Максимальний розмір — 20 МБ."
        )

        return


    max_retries = 3
    text = None


    # --------------------------------------------------------
    # Автоматичні повторні спроби
    # --------------------------------------------------------

    for attempt in range(max_retries):

        try:

            async with transcription_semaphore:

                # Отримуємо файл Telegram
                telegram_file = await context.bot.get_file(
                    audio_info["file_id"]
                )

                # Завантажуємо файл
                file_bytes = await telegram_file.download_as_bytearray()


                logger.info(
                    "Файл завантажено: %d байт",
                    len(file_bytes),
                )


                # ------------------------------------------------
                # GROQ WHISPER
                # ------------------------------------------------

                transcription = await client.audio.transcriptions.create(

                    file=(
                        audio_info["file_name"],
                        bytes(file_bytes),
                    ),

                    model="whisper-large-v3",

                    response_format="text",

                    # Більш стабільний результат
                    temperature=0.0,
                )


            text = str(transcription).strip()


            if text:
                break


        except Exception as e:

            logger.warning(
                "Спроба %d/%d не вдалася: %s",
                attempt + 1,
                max_retries,
                e,
            )


            # Якщо це була остання спроба
            if attempt == max_retries - 1:
                raise


            # Чекаємо перед повтором
            await asyncio.sleep(1.5)


    # --------------------------------------------------------
    # Якщо текст порожній
    # --------------------------------------------------------

    if not text:

        await target_message.reply_text(
            "Не вдалося розпізнати голосове повідомлення."
        )

        return


    logger.info(
        "Розшифровка готова: %d символів",
        len(text),
    )


    # --------------------------------------------------------
    # ВІДПРАВЛЯЄМО ТІЛЬКИ ТЕКСТ
    # --------------------------------------------------------

    parts = split_text(text)


    for part in parts:

        await target_message.reply_text(
            part
        )


# ============================================================
# ОБРОБКА ГОЛОСОВИХ
# ============================================================

async def handle_voice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    message = update.message

    if not message:
        return


    audio_info = get_audio_info(message)

    if not audio_info:
        return


    # --------------------------------------------------------
    # Логування користувача
    # --------------------------------------------------------

    user = message.from_user

    if user:

        username = (
            f"@{user.username}"
            if user.username
            else str(user.id)
        )

    else:

        username = "unknown"


    logger.info(
        "Нове голосове від %s",
        username,
    )


    # --------------------------------------------------------
    # Обробляємо
    # --------------------------------------------------------

    try:

        await process_and_reply(
            message,
            audio_info,
            context,
        )

    except Exception:

        logger.exception(
            "Помилка під час обробки голосового"
        )

        await message.reply_text(
            "❌ Не вдалося розшифрувати голосове повідомлення."
        )


# ============================================================
# ГЛОБАЛЬНИЙ ОБРОБНИК ПОМИЛОК
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.exception(
        "Необроблена помилка:",
        exc_info=context.error,
    )


# ============================================================
# ЗАПУСК
# ============================================================

def main():

    logger.info(
        "Запуск бота..."
    )


    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )


    # Telegram Voice
    app.add_handler(
        MessageHandler(
            filters.VOICE,
            handle_voice,
        )
    )


    # Звичайні аудіофайли
    app.add_handler(
        MessageHandler(
            filters.AUDIO,
            handle_voice,
        )
    )


    # Глобальні помилки
    app.add_error_handler(
        error_handler
    )


    logger.info(
        "Бот запущено 🚀"
    )


    app.run_polling()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
