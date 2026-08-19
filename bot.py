import asyncio
import os
import random
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, 
    FSInputFile, ChatPermissions
)

# --- 1. ВЕБ-СЕРВЕР ДЛЯ RENDER (запускаємо найпершим) ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# --- 2. ОСНОВНИЙ КОД БОТА ---
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

game = {
    "status": "waiting",
    "players": {},
    "chat_id": None,
    "mafia_target": None,
    "doctor_target": None,
    "commissioner_target": None,
    "commissioner_shot": None,
    "sheriff_action_done": False,
    "votes": {},
    "runoff_candidates": [],
    "timer_task": None
}

# --- КЛАВІАТУРИ ---
def get_join_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Увійти в гру", callback_data="join_game")],
        [InlineKeyboardButton(text="🚀 Почати гру", callback_data="start_game")]
    ])

def get_mafia_keyboard(players):
    buttons = [[InlineKeyboardButton(text=f"{p['number']}. {p['name']}", callback_data=f"kill_{uid}")]
               for uid, p in players.items() if p["alive"] and p["role"] != "mafia"]
    buttons.append([InlineKeyboardButton(text="💤 Нікого не вбивати", callback_data="kill_skip")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_doctor_keyboard(players):
    buttons = [[InlineKeyboardButton(text=f"{p['number']}. {p['name']}", callback_data=f"heal_{uid}")]
               for uid, p in players.items() if p["alive"]]
    buttons.append([InlineKeyboardButton(text="💤 Нікого не лікувати", callback_data="heal_skip")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_comm_keyboard(players):
    buttons = [[InlineKeyboardButton(text=f"🔍 Перевірити: {p['number']}. {p['name']}", callback_data=f"check_{uid}")]
               for uid, p in players.items() if p["alive"]]
    buttons.append([InlineKeyboardButton(text="--- АБО ВИСТРІЛ ---", callback_data="ignore")])
    for uid, p in players.items():
        if p["alive"]: buttons.append([InlineKeyboardButton(text=f"🔫 Вистрілити: {p['number']}. {p['name']}", callback_data=f"shot_{uid}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_vote_keyboard(players, candidate_ids=None):
    target_players = players if not candidate_ids else {uid: p for uid, p in players.items() if uid in candidate_ids}
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👉 {p['number']}. {p['name']}", callback_data=f"vote_{uid}")]
        for uid, p in target_players.items() if p["alive"]
    ])

# --- ДОПОМІЖНІ ТА ІГРОВІ ФУНКЦІЇ ---
async def mute_chat(chat_id: int, mute: bool = True):
    try: await bot.set_chat_permissions(chat_id, ChatPermissions(can_send_messages=not mute))
    except Exception as e: print(f"Помилка зміни прав: {e}")

async def send_phase_photo(chat_id: int, phase: str, caption: str):
    filename = f"{phase}.jpg"
    if os.path.exists(filename): await bot.send_photo(chat_id, photo=FSInputFile(filename), caption=caption)
    else: await bot.send_message(chat_id, caption)

def get_alive_list_text():
    alive = [p for p in game["players"].values() if p["alive"]]
    text = f"📋 **Живі гравці ({len(alive)}):**\n"
    return text + "\n".join([f"• {p['number']}. {p['name']}" for p in sorted(alive, key=lambda x: x['number'])])

def format_all_roles_summary():
    text = "📜 **Склад гри:**\n\n"
    icons = {"mafia": "Мафія 🔪", "doctor": "Доктор 🩺", "commissioner": "Шериф 🕵️", "civilian": "Мирний 😇"}
    for p in sorted(game["players"].values(), key=lambda x: x["number"]):
        status = "💀 мертвий" if not p["alive"] else "🟢 вижив"
        text += f"• {p['number']}. {p['name']} — {icons.get(p['role'], p['role'])} ({status})\n"
    return text

# --- ХЕНДЛЕРИ ТА ЛОГІКА ---
@dp.message(F.text.startswith("/"))
async def cmd_commands(message: Message):
    text = message.text.lower().split('@')[0]
    if text in ["/mafia", "/start"]:
        game.update({"status": "waiting", "players": {}, "chat_id": message.chat.id})
        await message.answer("🕶 Місто засинає... Збір на Мафію!", reply_markup=get_join_keyboard())
    elif text == "/cancel":
        game["status"] = "waiting"
        await mute_chat(message.chat.id, False)
        await message.answer("❌ ГРУ СКАСОВАНО!")

@dp.callback_query(F.data == "join_game")
async def cb_join(callback: CallbackQuery):
    if game["status"] != "waiting": return await callback.answer("Гра вже почалася!", show_alert=True)
    user = callback.from_user
    if user.id not in game["players"]:
        game["players"][user.id] = {"name": user.first_name, "role": "civilian", "alive": True, "number": 0}
        await callback.answer("Ти увійшов!")
        await callback.message.edit_text(f"Учасники ({len(game['players'])}):\n" + "\n".join([p['name'] for p in game['players'].values()]), reply_markup=get_join_keyboard())

@dp.callback_query(F.data == "start_game")
async def cb_start(callback: CallbackQuery):
    if len(game["players"]) < 3: return await callback.answer("Потрібно від 3 гравців!", show_alert=True)
    game.update({"status": "night", "mafia_target": None, "doctor_target": None, "commissioner_shot": None, "sheriff_action_done": False})
    
    uids = list(game["players"].keys())
    random.shuffle(uids)
    for i, uid in enumerate(uids, 1): game["players"][uid]["number"] = i
    
    game["players"][uids[0]]["role"] = "mafia"
    game["players"][uids[1]]["role"] = "doctor"
    game["players"][uids[2]]["role"] = "commissioner"
    
    await mute_chat(game["chat_id"], True)
    await send_phase_photo(game["chat_id"], "night", "🌙 Місто засинає. Ролі роздано в ЛС!")
    for uid, p in game["players"].items():
        try:
            if p["role"] == "mafia": await bot.send_message(uid, "🔪 Ти МАФІЯ. Обери жертву.", reply_markup=get_mafia_keyboard(game["players"]))
            elif p["role"] == "doctor": await bot.send_message(uid, "🩺 Ти ДОКТОР. Кого лікувати?", reply_markup=get_doctor_keyboard(game["players"]))
            elif p["role"] == "commissioner": await bot.send_message(uid, "🕵️ Ти КОМІСАР. Перевірка чи постріл?", reply_markup=get_comm_keyboard(game["players"]))
            else: await bot.send_message(uid, "💤 Ти мирний. Спи.")
        except: pass
    game["timer_task"] = asyncio.create_task(night_timer())

async def night_timer():
    await asyncio.sleep(40)
    if game["status"] == "night": await resolve_night()

# [Тут має бути вся інша логіка обробки (resolve_night, voting, тощо) - вона в тебе вже правильна]
# ... залишаємо решту твого коду без змін ...

async def main():
    print("Бот 'Мафія' оновлено і повністю готовий до роботи...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
