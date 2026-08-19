import asyncio
import os
import random
import threading
from html import escape
from http.server import HTTPServer, BaseHTTPRequestHandler
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, 
    FSInputFile, ChatPermissions
)

# --- 1. МІКРО-СЕРВЕР ДЛЯ ПОРТІВ RENDER ---
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

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

game = {
    "status": "waiting",
    "players": {},
    "chat_id": None,
    "mafia_votes": {},
    "doctor_target": None,
    "sheriff_target": None,
    "sheriff_shot": None,
    "sheriff_action_done": False,
    "votes": {},
    "runoff_candidates": [], 
    "timer_task": None
}

ROLE_ICONS = {
    "mafia": "Мафія 🔪",
    "doctor": "Доктор 🩺",
    "sheriff": "Шериф 🕵️",
    "lucky": "Щасливчик 🍀",
    "civilian": "Мирний житель 😇"
}

# --- КЛАВІАТУРИ ---
def get_join_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Увійти в гру", callback_data="join_game")],
        [InlineKeyboardButton(text="🚀 Почати гру", callback_data="start_game")]
    ])

def get_mafia_keyboard(players):
    buttons = [
        [InlineKeyboardButton(text=f"{p['number']}. {p['name']}", callback_data=f"mkel_{uid}")]
        for uid, p in players.items() if p["alive"] and p["role"] != "mafia"
    ]
    buttons.append([InlineKeyboardButton(text="💤 Нікого не вбивати", callback_data="mkel_skip")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_doctor_keyboard(players):
    buttons = [
        [InlineKeyboardButton(text=f"{p['number']}. {p['name']}", callback_data=f"heal_{uid}")]
        for uid, p in players.items() if p["alive"]
    ]
    buttons.append([InlineKeyboardButton(text="💤 Нікого не лікувати", callback_data="heal_skip")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_sheriff_keyboard(players, user_id):
    buttons = [
        [InlineKeyboardButton(text=f"🔍 Перевірити: {p['number']}. {p['name']}", callback_data=f"check_{uid}")]
        for uid, p in players.items() if p["alive"]
    ]
    buttons.append([InlineKeyboardButton(text="--- АБО ВИСТРІЛ ---", callback_data="ignore")])
    for uid, p in players.items():
        if p["alive"] and uid != user_id:  
            buttons.append([InlineKeyboardButton(text=f"🔫 Вистрілити: {p['number']}. {p['name']}", callback_data=f"shot_{uid}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_vote_keyboard(players, candidate_ids=None):
    target_players = players if not candidate_ids else {uid: p for uid, p in players.items() if uid in candidate_ids}
    buttons = [
        [InlineKeyboardButton(text=f"👉 {p['number']}. {p['name']}", callback_data=f"vote_{uid}")]
        for uid, p in target_players.items() if p["alive"]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def mute_chat(chat_id: int, mute: bool = True):
    try:
        await bot.set_chat_permissions(chat_id, ChatPermissions(can_send_messages=not mute))
    except Exception as e:
        print(f"Помилка зміни прав чату: {e}")

async def send_phase_photo(chat_id: int, phase: str, caption: str):
    filename = f"{phase}.jpg"
    if os.path.exists(filename):
        photo = FSInputFile(filename)
        await bot.send_photo(chat_id, photo=photo, caption=caption)
    else:
        await bot.send_message(chat_id, caption)

def get_alive_list_text():
    alive_players = [p for p in game["players"].values() if p["alive"]]
    text = f"📋 <b>Живі гравці ({len(alive_players)}):</b>\n"
    for p in sorted(alive_players, key=lambda x: x["number"]):
        text += f"• {p['number']}. {escape(p['name'])}\n"
    return text

def format_all_roles_summary():
    text = "📜 <b>Підсумки гри:</b>\n\n"
    for p in sorted(game["players"].values(), key=lambda x: x["number"]):
        status = "💀" if not p["alive"] else "🟢"
        text += f"• {p['number']}. {escape(p['name'])} — {ROLE_ICONS.get(p['role'], p['role'])} {status}\n"
    return text

def get_mafia_team_str():
    mafia_members = [escape(p['name']) for p in game["players"].values() if p["role"] == "mafia"]
    return "\n".join([f"• {name}" for name in mafia_members])

# --- ОСНОВНІ КОМАНДИ ---
@dp.message(F.text.startswith("/"))
async def cmd_commands(message: Message):
    text = message.text.lower().split('@')[0]
    if text == "/mafia":
        game["status"] = "waiting"
        game["players"].clear()
        game["chat_id"] = message.chat.id
        await message.answer("🎴 Нова гра! Долучайтеся:", reply_markup=get_join_keyboard())
    elif text == "/cancel":
        game["status"] = "stopped"
        await mute_chat(message.chat.id, False)
        await message.answer("❌ Гру зупинено.")

# --- ОБРОБКА НІЧНИХ ДІЙ (Логіка 50/50 для Щасливчика) ---
async def resolve_night():
    if game["status"] != "night": return
    
    victim = None
    if game["mafia_votes"]:
        t_counts = {}
        for m_id, t_id in game["mafia_votes"].items():
            if t_id != "skip": t_counts[t_id] = t_counts.get(t_id, 0) + 1
        if t_counts:
            max_v = max(t_counts.values())
            candidates = [t for t, cnt in t_counts.items() if cnt == max_v]
            victim = random.choice(candidates)

    doctor = game["doctor_target"]
    sheriff_shot = game["sheriff_shot"]
    
    text = "🌅 <b>Ранок.</b>\n\n"
    
    # 1. Дія мафії
    if victim:
        victim_player = game["players"].get(victim)
        if victim_player and victim_player["alive"]:
            victim_name = escape(victim_player['name'])
            if victim == doctor:
                text += f"🩺 Доктор врятував <b>{victim_name}</b>!\n"
            elif victim_player["role"] == "lucky" and not victim_player["lucky_used"]:
                victim_player["lucky_used"] = True
                if random.random() < 0.5:
                    text += f"🍀 Куля мафії летіла в <b>{victim_name}</b>, але він(вона) дивом вижив(ла)!\n"
                else:
                    victim_player["alive"] = False
                    text += f"💀 Вбито мафією: <b>{victim_name}</b>! (Щасливчику не пощастило).\n"
            else:
                victim_player["alive"] = False
                text += f"💀 Вбито мафією: <b>{victim_name}</b>!\n"

    # 2. Дія шерифа (Доктор тепер захищає від усього)
    if sheriff_shot:
        shot_player = game["players"].get(sheriff_shot)
        if shot_player and shot_player["alive"]:
            shot_name = escape(shot_player['name'])
            if sheriff_shot == doctor:
                text += f"🛡 Доктор залікував рану від шерифа у <b>{shot_name}</b>!\n"
            else:
                shot_player["alive"] = False
                text += f"🎯 Шериф вбив <b>{shot_name}</b>!\n"

    await mute_chat(game["chat_id"], False)
    await bot.send_message(game["chat_id"], text + "\n" + get_alive_list_text())
    
    if not await check_win_condition():
        game["status"] = "discussion"
        await bot.send_message(game["chat_id"], "🗣 Обговорення (1 хв)!")
        await asyncio.sleep(60)
        await start_voting()

# --- ЗАПУСК ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
