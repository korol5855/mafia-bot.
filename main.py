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

    # Робимо кілька спроб автоматично
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
                break # Успішно розшифрували — виходимо з циклу

        except Exception as e:
            logger.warning(f"Спроба {attempt + 1} з {max_retries} не вдалася: {e}")
            if attempt == max_retries - 1:
                # Якщо це була остання спроба — ловимо помилку нижче
                raise e
            await asyncio.sleep(1.5) # Коротка пауза перед наступною автоматичною спробою

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
        
