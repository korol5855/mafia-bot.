import os
import telebot
import speech_recognition as sr

TOKEN = "8700228403:AAESsiqBgXKZFBm6RhQbDuJpp7zF51hCmc"
bot = telebot.TeleBot(TOKEN)
recognizer = sr.Recognizer()

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    processing_msg = bot.reply_to(message, "⏳ Перетворюю голос у текст...")
    
    try:
        # Отримуємо файл з Telegram
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        ogg_path = f"voice_{message.voice.file_id}.ogg"
        wav_path = f"voice_{message.voice.file_id}.wav"
        
        with open(ogg_path, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        # Конвертуємо через ffmpeg
        os.system(f"ffmpeg -i {ogg_path} {wav_path}")
        
        # Розпізнаємо через Google
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="uk-UA")
            
            user_name = message.from_user.first_name
            bot.reply_to(message, f"🎙 **{user_name}:**\n{text}")
            
    except Exception as e:
        bot.reply_to(message, "❌ Не вдалося розпізнати голосове повідомлення.")
        print(f"Error: {e}")
        
    finally:
        # Прибираємо сміття
        if os.path.exists(ogg_path): 
            os.remove(ogg_path)
        if os.path.exists(wav_path): 
            os.remove(wav_path)
        try:
            bot.delete_message(message.chat.id, processing_msg.message_id)
        except:
            pass

print("Бот запущено!")
bot.infinity_polling()
