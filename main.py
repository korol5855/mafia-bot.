import os
import logging
import asyncio

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


# Groq async client
client = AsyncGroq(api_key=GROQ_API_KEY)


# Максимальний розмір файлу.
# Telegram Bot API має обмеження на завантаження файлів.
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


# Скільки голосових можемо обробляти одночасно.
# 3 — хороший баланс для невеликого бота.
transcription_semaphore = asyncio.Semaphore(3)


# ============================================================
# ЛОГУВАННЯ
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("voice_transcriber")


# ============================================================
# ДОПОМІЖНІ ФУНКЦІЇ
# ============================================================

def split_text(text: str, max_length: int = 4000) -> list[str]:
    """
    Telegram має обмеження на довжину повідомлення.
    Ділимо довгий текст приблизно по 4000 символів.
    """

    if len(text) <= max_length:
        return [text]

    parts = []

    while len(text) > max_length:
        # Намагатимемося розділити по переносу рядка
        split_at = text.rfind("\n", 0, max_length)

        # Якщо переносу немає — шукаємо пробіл
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_length)

        # Якщо й пробілу немає — ріжемо жорстко
        if split_at == -1:
            split_at = max_length

        parts.append(text[:split_at].strip())
        text = text[split_at:].strip()

    if text:
        parts.append(text)

    return parts


def get_audio_info(message):
    """
    Визначає, що саме прийшло:
    Telegram voice або audio.
    """

    if message.voice:
        return {
            "file_id": message.voice.file_id,
            "file_size": message.voice.file_size,
            "file_name": "voice.ogg",
            "type": "voice",
        }

    if message.audio:
        filename = message.audio.file_name or "audio.mp3"

        return {
            "file_id": message.audio.file_id,
            "file_size": message.audio.file_size,
            "file_name": filename,
            "type": "audio",
        }

    return None


# ============================================================
# ОСНОВНА ОБРОБКА
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
    # Перевірка розміру
    # --------------------------------------------------------

    file_size = audio_info["file_size"]

    if file_size and file_size > MAX_FILE_SIZE:
        await message.reply_text(
            "❌ Файл занадто великий.\n\n"
            "Максимальний розмір для обробки — 20 МБ."
        )
        return


    user = message.from_user

    username = (
        f"@{user.username}"
        if user and user.username
        else str(user.id) if user else "unknown"
    )

    logger.info(
        "Нове аудіо від %s | type=%s | size=%s",
        username,
        audio_info["type"],
        file_size,
    )


    # --------------------------------------------------------
    # Повідомлення про обробку
    # --------------------------------------------------------

    processing_message = await message.reply_text(
        "🎧 Обробляю голосове повідомлення...\n"
        "⏳ Зачекай кілька секунд."
    )


    try:

        # ----------------------------------------------------
        # Обмежуємо кількість одночасних запитів до Groq
        # ----------------------------------------------------

        async with transcription_semaphore:

            # Отримуємо файл Telegram
            telegram_file = await context.bot.get_file(
                audio_info["file_id"]
            )

            # Завантажуємо байти
            file_bytes = await telegram_file.download_as_bytearray()

            logger.info(
                "Файл завантажено | %s | %d bytes",
                username,
                len(file_bytes),
            )


            # ------------------------------------------------
            # Розшифровка
            # ------------------------------------------------

            transcription = await client.audio.transcriptions.create(
                file=(
                    audio_info["file_name"],
                    bytes(file_bytes),
                ),
                model="whisper-large-v3",
                response_format="text",
            )


        # Перетворюємо результат у звичайний текст
        text = str(transcription).strip()


        if not text:
            await processing_message.edit_text(
                "🤔 Не вдалося розібрати голосове повідомлення.\n\n"
                "Спробуй записати його ще раз, бажано трохи чіткіше."
            )
            return


        logger.info(
            "Розшифровка готова | %s | %d символів",
            username,
            len(text),
        )


        # ----------------------------------------------------
        # Прибираємо повідомлення "обробляю"
        # ----------------------------------------------------

        await processing_message.delete()


        # ----------------------------------------------------
        # Виводимо результат
        # ----------------------------------------------------

        parts = split_text(text)


        # Якщо текст короткий — одне красиве повідомлення
        if len(parts) == 1:

            await message.reply_text(
                "📝 Розшифровка:\n\n"
                f"{parts[0]}"
            )

        else:

            await message.reply_text(
                "📝 Розшифровка:\n\n"
                f"{parts[0]}"
            )

            for index, part in enumerate(parts[1:], start=2):

                await message.reply_text(
                    f"📝 Продовження ({index}/{len(parts)}):\n\n"
                    f"{part}"
                )


    except Exception as e:

        logger.exception(
            "Помилка при обробці голосового від %s",
            username,
        )

        try:
            await processing_message.edit_text(
                "❌ Не вдалося розшифрувати голосове.\n\n"
                "Спробуй ще раз."
            )

        except Exception:
            # Якщо повідомлення вже було видалене/змінене
            await message.reply_text(
                "❌ Не вдалося розшифрувати голосове.\n\n"
                "Спробуй ще раз."
            )


# ============================================================
# ПОМИЛКИ TELEGRAM
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

    logger.info("Запускаємо Pigeon Transcriber...")


    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )


    # Голосові повідомлення
    app.add_handler(
        MessageHandler(
            filters.VOICE,
            handle_voice,
        )
    )


    # Аудіофайли
    app.add_handler(
        MessageHandler(
            filters.AUDIO,
            handle_voice,
        )
    )


    # Глобальний обробник помилок
    app.add_error_handler(error_handler)


    logger.info("Бот успішно запущений 🚀")


    app.run_polling()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
