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

# Переходимо на HTML за замовчуванням задля безпеки імен з спецсимволами (_ *, [], тощо)
bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

game = {
    "status": "waiting",   # Можливі стани: "waiting", "night", "discussion", "voting", "finished", "stopped"
    "players": {},         # {user_id: {"name": str, "role": str, "alive": bool, "number": int, "lucky_used": bool, "self_heals_used": int}}
    "chat_id": None,
    "mafia_votes": {},     # {mafia_user_id: target_user_id}
    "doctor_target": None,
    "sheriff_target": None,
    "sheriff_shot": None,
    "sheriff_action_done": False,
    "votes": {},           # {voter_id: target_id}
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
    buttons.append([InlineKeyboardButton(text="💤 Нікого не вбивати (пропуск)", callback_data="mkel_skip")])
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
        if p["alive"] and uid != user_id:  # Шериф не може вистрілити в себе
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
    text = f"📋 <b>Живі гравці у місті ({len(alive_players)}):</b>\n"
    for p in sorted(alive_players, key=lambda x: x["number"]):
        safe_name = escape(p['name'])
        text += f"• {p['number']}. {safe_name}\n"
    return text

def format_all_roles_summary():
    text = "📜 <b>Склад завершеної гри (хто ким був):</b>\n\n"
    for p in sorted(game["players"].values(), key=lambda x: x["number"]):
        status = "💀 мертвий" if not p["alive"] else "🟢 вижив"
        safe_name = escape(p['name'])
        text += f"• {p['number']}. {safe_name} — {ROLE_ICONS.get(p['role'], p['role'])} ({status})\n"
    return text

def get_mafia_team_str():
    mafia_members = [escape(p['name']) for p in game["players"].values() if p["role"] == "mafia"]
    return "\n".join([f"• {name}" for name in mafia_members])

# --- КОМАНДИ ---
@dp.message(F.text == "/start", F.chat.type == "private")
async def private_start(message: Message):
    user_name = escape(message.from_user.first_name)
    await message.answer(
        f"👋 Привіт, <b>{user_name}</b>! Вітаю тебе в боті для гри в <b>Мафію</b>.\n\n"
        "📜 <b>Правила гри та ролі:</b>\n"
        "🔪 <b>Мафія</b> — спільно обирає жертву для вбивства.\n"
        "🩺 <b>Доктор</b> — може врятувати від кулі мафії себе (не більше 1 разу за гру) чи іншого гравця.\n"
        "🕵️ <b>Шериф</b> — перевіряє підозрілих або може сам відкрити вогонь (але не в себе).\n"
        "🍀 <b>Щасливчик</b> — мирний житель, який має шанс уникнути смерті від кулі мафії.\n"
        "😇 <b>Мирний житель</b> — бере участь у денних обговореннях і голосуваннях."
    )

@dp.message(F.text.startswith("/"))
async def cmd_commands(message: Message):
    text = message.text.lower().split('@')[0]
    
    if text in ["/mafia", "/start"] and message.chat.type != "private":
        if game["status"] not in ["waiting", "finished", "stopped"] and bool(game["players"]):
            return await message.answer("⚠️ Неможливо почати нову гру: попередня партія ще триває!")
            
        game["status"] = "waiting"
        game["players"].clear()
        game["chat_id"] = message.chat.id
        await message.answer(
            "🎴 Увага! Оголошено збір на нову гру в Мафію!\n\n"
            "Тисніть кнопку нижче для участі:",
            reply_markup=get_join_keyboard()
        )
        
    elif text == "/cancel":
        game["status"] = "stopped"
        game["players"].clear()
        if game["timer_task"]:
            game["timer_task"].cancel()
        await mute_chat(message.chat.id, False)
        await message.answer("❌ ГРУ СКАСОВАНО! Чат розблоковано. Можна розпочати нову за командою /mafia")

# --- ВХІД ТА СТАРТ ГРИ ---
@dp.callback_query(F.data == "join_game")
async def cb_join(callback: CallbackQuery):
    if game["status"] != "waiting":
        return await callback.answer("Зараз немає активного набору в гру!", show_alert=True)
        
    user = callback.from_user
    if user.id in game["players"]:
        return await callback.answer("Ти вже в грі!", show_alert=True)
        
    game["players"][user.id] = {
        "name": user.first_name, 
        "role": "civilian", 
        "alive": True, 
        "number": 0, 
        "lucky_used": False,
        "self_heals_used": 0
    }
    names = [escape(p["name"]) for p in game["players"].values()]
    
    await callback.answer("Ти увійшов у гру!")
    try:
        await callback.message.edit_text(
            f"🕶 Збір гравців!\n\nУчасники ({len(names)}):\n" + "\n".join([f"• {n}" for n in names]), 
            reply_markup=get_join_keyboard()
        )
    except Exception:
        pass

@dp.callback_query(F.data == "start_game")
async def cb_start(callback: CallbackQuery):
    total_players = len(game["players"])
    if total_players < 5:
        return await callback.answer("Потрібно мінімум 5 гравців для повноцінної гри!", show_alert=True)
    
    game["status"] = "night"
    game["mafia_votes"].clear()
    game["doctor_target"] = None
    game["sheriff_target"] = None
    game["sheriff_shot"] = None
    game["sheriff_action_done"] = False
    game["votes"].clear()
    game["runoff_candidates"].clear()
    
    user_ids = list(game["players"].keys())
    random.shuffle(user_ids)
    
    for i, uid in enumerate(user_ids, start=1):
        game["players"][uid]["number"] = i
        game["players"][uid]["lucky_used"] = False
        game["players"][uid]["self_heals_used"] = 0
    
    if total_players <= 5:
        mafia_count = 1
        has_lucky = True
    elif total_players <= 8:
        mafia_count = 2
        has_lucky = True
    elif total_players <= 11:
        mafia_count = 3
        has_lucky = True
    else:
        mafia_count = 3
        has_lucky = False

    idx = 0
    for i in range(mafia_count):
        game["players"][user_ids[idx]]["role"] = "mafia"
        idx += 1
    
    game["players"][user_ids[idx]]["role"] = "doctor"
    idx += 1
    
    game["players"][user_ids[idx]]["role"] = "sheriff"
    idx += 1
    
    if has_lucky:
        game["players"][user_ids[idx]]["role"] = "lucky"
        idx += 1
        
    for i in range(idx, total_players):
        game["players"][user_ids[i]]["role"] = "civilian"
        
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    await mute_chat(game["chat_id"], True)
    await send_phase_photo(game["chat_id"], "night", "🌙 Місто засинає. Чат заблоковано. Ролі роздано в ЛС!")
    
    mafia_team_text = get_mafia_team_str()

    for uid, p in game["players"].items():
        try:
            if p["role"] == "mafia":
                await bot.send_message(
                    uid, 
                    f"🔪 <b>Ти МАФІЯ.</b>\n\nВаша команда:\n{mafia_team_text}\n\nКого вбиваємо?\n\n{get_alive_list_text()}", 
                    reply_markup=get_mafia_keyboard(game["players"])
                )
            elif p["role"] == "doctor":
                await bot.send_message(uid, f"🩺 Ти ДОКТОР. Кого будеш лікувати (себе можна лише 1 раз за гру):\n\n{get_alive_list_text()}", reply_markup=get_doctor_keyboard(game["players"]))
            elif p["role"] == "sheriff":
                await bot.send_message(uid, f"🕵️ Ти ШЕРИФ. Обирай перевірку чи постріл:\n\n{get_alive_list_text()}", reply_markup=get_sheriff_keyboard(game["players"], uid))
            elif p["role"] == "lucky":
                await bot.send_message(uid, f"🍀 Ти ЩАСЛИВЧИК. Якщо мафія обере тебе своєю жертвою, ти маєш шанс дивом вижити у першу ніч замаху!\n\n{get_alive_list_text()}")
            else:
                await bot.send_message(uid, f"💤 Ти мирний житель. Спи спокійно.\n\n{get_alive_list_text()}")
        except Exception:
            pass

    if game["timer_task"]:
        game["timer_task"].cancel()
    game["timer_task"] = asyncio.create_task(night_timer())

async def night_timer():
    await asyncio.sleep(40)
    if game["status"] == "night":
        await resolve_night()

# --- ОБРОБКА НІЧНИХ ДІЙ ---
@dp.callback_query(F.data.startswith(("mkel_", "heal_", "check_", "shot_", "heal_skip")))
async def cb_night_actions(callback: CallbackQuery):
    if game["status"] != "night":
        return await callback.answer("❌ Ця ніч вже закінчилася або ще не почалася!", show_alert=True)
        
    user_id = callback.from_user.id
    player = game["players"].get(user_id)
    
    if not player or not player["alive"]:
        return await callback.answer("❌ Ви мертві і не можете здійснювати дії!", show_alert=True)
        
    data = callback.data.split("_")
    action = data[0]
    
    chat_messages = {
        "mkel": "🌃 Ніч стає темнішою... Мафія обговорює плани та обирає ціль.",
        "heal": "🛡 Лікар на посту. Він робить все, щоб врятувати чиєсь життя.",
        "heal_skip": "🥃 Лікар вирішив відсидітися вдома. Сьогодні кожен сам за себе.",
        "check": "🔎 Шериф крадькома перевіряє підозрілих осіб.",
        "shot": "💥 Шериф зняв запобіжник. Повітря в місті стало напруженим..."
    }

    choice_text = "✅ Вибір збережено."

    if action == "mkel":
        if player["role"] != "mafia":
            return await callback.answer("❌ Ця дія доступна лише мафії!", show_alert=True)
            
        target_val = "skip" if data[1] == "skip" else int(data[1])
        if target_val != "skip":
            target_player = game["players"].get(target_val)
            if not target_player or not target_player["alive"]:
                return await callback.answer("❌ Цей гравець уже мертвий!", show_alert=True)
                
            if target_player["role"] == "mafia":
                return await callback.answer("❌ Мафія не може вбити мафію!", show_alert=True)
                
        game["mafia_votes"][user_id] = target_val
        
        if target_val == "skip":
            choice_text = "💤 Ви проголосували за те, щоб нікого не вбивати."
        else:
            target_name = escape(game["players"].get(target_val, {}).get("name", ""))
            choice_text = f"✅ Ваш голос за ціль: <b>{target_name}</b>"
        
        await bot.send_message(game["chat_id"], chat_messages["mkel"])
        
    elif action in ["heal", "heal_skip"]:
        if player["role"] != "doctor":
            return await callback.answer("❌ Ця дія доступна лише доктору!", show_alert=True)
            
        if action == "heal_skip":
            game["doctor_target"] = "skip"
            choice_text = "💤 Ви нікого не лікували цієї ночі."
            await bot.send_message(game["chat_id"], chat_messages["heal_skip"])
        else:
            target_id = int(data[1])
            target_player = game["players"].get(target_id)
            if not target_player or not target_player["alive"]:
                return await callback.answer("❌ Цей гравець уже мертвий!", show_alert=True)
                
            if target_id == user_id:
                if player["self_heals_used"] >= 1:
                    return await callback.answer("❌ Ви вже використали своє єдине самолікування за цю гру!", show_alert=True)
                player["self_heals_used"] += 1
                
            game["doctor_target"] = target_id
            target_name = escape(target_player["name"])
            choice_text = f"✅ Ви обрали кого лікувати: <b>{target_name}</b>"
            await bot.send_message(game["chat_id"], chat_messages["heal"])
            
    elif action in ["check", "shot"]:
        if player["role"] != "sheriff":
            return await callback.answer("❌ Ця дія доступна лише шерифу!", show_alert=True)
            
        if game["sheriff_action_done"]:
            return await callback.answer("Ти вже зробив дію цієї ночі!", show_alert=True)
            
        target_id = int(data[1])
        
        if action == "shot" and target_id == user_id:
            return await callback.answer("❌ Ви не можете вистрілити в самого себе!", show_alert=True)
            
        target_player = game["players"].get(target_id)
        if not target_player or not target_player["alive"]:
            return await callback.answer("❌ Цей гравець уже мертвий!", show_alert=True)
            
        if action == "check":
            target_role = target_player["role"]
            res = "мафія 🔪" if target_role == "mafia" else "мирний житель 😇"
            target_name = escape(target_player["name"])
            choice_text = f"✅ Перевірено <b>{target_name}</b> — виявився(-лась) як <b>{res}</b>"
            
            await callback.message.answer(f"🔍 Перевірка завершена: {target_name} виявився(-лась) — {res}")
            game["sheriff_target"] = target_id
            game["sheriff_action_done"] = True
            await bot.send_message(game["chat_id"], chat_messages["check"])
            
        elif action == "shot":
            target_name = escape(target_player["name"])
            choice_text = f"🎯 Ви зробили постріл у гравця: <b>{target_name}</b>"
            
            game["sheriff_shot"] = target_id
            game["sheriff_action_done"] = True
            await callback.message.answer(f"🔫 Ти вистрілив у гравця: {target_name}")
            await bot.send_message(game["chat_id"], chat_messages["shot"])

    try:
        await callback.message.edit_text(choice_text, reply_markup=None)
    except Exception:
        pass

    await callback.answer("Збережено!")
    await check_night_actions()

async def check_night_actions():
    alive_mafias = [uid for uid, p in game["players"].items() if p["alive"] and p["role"] == "mafia"]
    mafia_done = all(m_id in game["mafia_votes"] for m_id in alive_mafias) if alive_mafias else True
    
    doctor_done = game["doctor_target"] is not None
    
    sheriff_alive = any(p["alive"] for uid, p in game["players"].items() if p["role"] == "sheriff")
    sheriff_done = not sheriff_alive or game["sheriff_action_done"]
    
    if mafia_done and doctor_done and sheriff_done:
        if game["timer_task"]:
            game["timer_task"].cancel()
        await resolve_night()

async def resolve_night():
    if game["status"] != "night": return
    
    victim = None
    if game["mafia_votes"]:
        t_counts = {}
        skip_count = 0
        alive_mafias_count = sum(1 for p in game["players"].values() if p["alive"] and p["role"] == "mafia")
        
        for m_id, t_id in game["mafia_votes"].items():
            if game["players"].get(m_id, {}).get("alive", False):
                if t_id == "skip":
                    skip_count += 1
                else:
                    t_counts[t_id] = t_counts.get(t_id, 0) + 1
                    
        if skip_count == alive_mafias_count:
            victim = "skip"
        elif t_counts:
            max_v = max(t_counts.values())
            candidates = [t for t, cnt in t_counts.items() if cnt == max_v]
            victim = random.choice(candidates)

    doctor = game["doctor_target"]
    sheriff_shot = game["sheriff_shot"]
    
    text = "🌅 <b>Ранок у місті.</b>\n\n"
    
    if victim and victim != "skip":
        victim_player = game["players"].get(victim)
        if victim_player and victim_player["alive"]:
            victim_name = escape(victim_player['name'])
            if victim == doctor:
                text += f"🩺 Доктор врятував <b>{victim_name}</b> від кулі мафії!\n"
            elif victim_player["role"] == "lucky" and not victim_player["lucky_used"]:
                victim_player["lucky_used"] = True
                text += f"🍀 Куля мафії летіла в <b>{victim_name}</b>, але завдяки неймовірному везінню він(вона) дивом уникнув(-ла) смерті!\n"
            else:
                victim_player["alive"] = False
                role_key = victim_player["role"]
                role_name = ROLE_ICONS.get(role_key, role_key)
                text += f"💀 Вбито мафією: <b>{victim_name}</b>! Роль була: <b>{role_name}</b> 🪦\n"
    else:
        text += "Ніч від мафії пройшла спокійно (нікого не вбили).\n"
        
    if sheriff_shot:
        shot_player = game["players"].get(sheriff_shot)
        if shot_player and shot_player["alive"]:
            shot_name = escape(shot_player['name'])
            if sheriff_shot == doctor and sheriff_shot != victim:
                text += f"🛡 Доктор також залікував рану від пострілу шерифа по <b>{shot_name}</b>!\n"
            else:
                shot_player["alive"] = False
                shot_role_key = shot_player["role"]
                shot_role = ROLE_ICONS.get(shot_role_key, shot_role_key)
                text += f"🎯 Шериф здійснив постріл і вбив <b>{shot_name}</b>! Роль була: <b>{shot_role}</b> 🪦\n"

    text += "\n" + get_alive_list_text()

    await mute_chat(game["chat_id"], False)
    await send_phase_photo(game["chat_id"], "day", text)
    
    if await check_win_condition():
        return

    game["status"] = "discussion"
    await bot.send_message(game["chat_id"], "🗣 Чат відкрито! Обговорення рівно <b>1 хвилину</b> ⏳")
    
    if game["timer_task"]:
        game["timer_task"].cancel()
    game["timer_task"] = asyncio.create_task(discussion_timer())

async def discussion_timer():
    await asyncio.sleep(60)
    if game["status"] == "discussion":
        await bot.send_message(game["chat_id"], "⏳ Час обговорення вийшов! Переходимо до голосування ⚖️")
        await start_voting()

# --- ГОЛОСУВАННЯ ---
async def start_voting(candidate_ids=None):
    game["status"] = "voting"
    game["votes"].clear()
    
    await mute_chat(game["chat_id"], True)
    
    if candidate_ids:
        names = ", ".join([f"{game['players'][uid]['number']}. {escape(game['players'][uid]['name'])}" for uid in candidate_ids])
        msg_text = f"⚖️ <b>ПЕРЕСТРІЛКА!</b> Голоси розділилися рівно між: <b>{names}</b>.\nУ вас є 30 секунд на вирішальне голосування!"
    else:
        msg_text = "⚖️ <b>Час голосування!</b> Обирайте підозрюваного:\n\n" + get_alive_list_text()

    await bot.send_message(
        game["chat_id"], 
        msg_text, 
        reply_markup=get_vote_keyboard(game["players"], candidate_ids)
    )
    
    if game["timer_task"]:
        game["timer_task"].cancel()
    game["timer_task"] = asyncio.create_task(voting_timer())

async def voting_timer():
    await asyncio.sleep(30)
    if game["status"] == "voting":
        await resolve_voting()

@dp.callback_query(F.data.startswith("vote_"))
async def cb_vote(callback: CallbackQuery):
    if game["status"] != "voting":
        return await callback.answer("❌ Голосування зараз не триває!", show_alert=True)
        
    voter_id = callback.from_user.id
    player = game["players"].get(voter_id)
    
    if not player or not player["alive"]:
        return await callback.answer("❌ Мертві не голосують або ви вже не у грі!", show_alert=True)
        
    target_id = int(callback.data.split("_")[1])
    target_player = game["players"].get(target_id)
    if not target_player or not target_player["alive"]:
        return await callback.answer("❌ Не можна голосувати за мертвого гравця!", show_alert=True)
        
    game["votes"][voter_id] = target_id
    target_name = escape(target_player["name"])
    
    try:
        await callback.message.edit_text(
            f"🗳 Ваш голос за гравця <b>{target_name}</b> успішно прийнято. Очікуємо інших...", 
            reply_markup=None
        )
    except Exception:
        pass

    await callback.answer("Голос прийнято!")
    
    alive_players_ids = {uid for uid, p in game["players"].items() if p["alive"]}
    voted_alive_count = sum(1 for v_id in game["votes"] if v_id in alive_players_ids)
    
    if voted_alive_count >= len(alive_players_ids):
        if game["timer_task"]:
            game["timer_task"].cancel()
        await resolve_voting()

async def resolve_voting():
    if game["status"] != "voting": return
    
    alive_players_ids = {uid for uid, p in game["players"].items() if p["alive"]}
    
    vote_counts = {}
    for voter, target in game["votes"].items():
        if voter in alive_players_ids and target in alive_players_ids:
            vote_counts[target] = vote_counts.get(target, 0) + 1
        
    text = "📊 <b>Результати голосування:</b>\n\n"
    if vote_counts:
        max_votes = max(vote_counts.values())
        candidates = [uid for uid, count in vote_counts.items() if count == max_votes]
        
        if game["runoff_candidates"] and len(candidates) > 1:
            text += "Нічия під час перестрілки! Місто вирішило нікого не виганяти цього разу."
            game["runoff_candidates"].clear()
            await finalize_voting_round(text)
            return

        if len(candidates) > 1:
            game["runoff_candidates"] = candidates
            names_str = ", ".join([f"{game['players'][c]['number']}. {escape(game['players'][c]['name'])}" for c in candidates])
            text += f"⚖️ <b>Нічия!</b> Кілька гравців набрали однакову кількість голосів: <b>{names_str}</b>.\nЗапускаємо додатковий раунд голосування між ними!"
            await bot.send_message(game["chat_id"], text)
            await start_voting(candidate_ids=candidates)
            return
        
        exiled = candidates[0]
        game["players"][exiled]["alive"] = False
        role_key = game["players"][exiled]["role"]
        role_name = ROLE_ICONS.get(role_key, role_key)
        exiled_name = escape(game['players'][exiled]['name'])
        text += f"⚖️ Місто вигнало гравця <b>{exiled_name}</b>.\nЙого роль була: <b>{role_name}</b> 🪦"
        game["runoff_candidates"].clear()
        await finalize_voting_round(text)
    else:
        text += "Ніхто не проголосував."
        game["runoff_candidates"].clear()
        await finalize_voting_round(text)

async def finalize_voting_round(text: str):
    text += "\n\n" + get_alive_list_text()
    await mute_chat(game["chat_id"], False)
    await bot.send_message(game["chat_id"], text)
    
    if await check_win_condition():
        return
        
    game["status"] = "night"
    game["mafia_votes"].clear()
    game["doctor_target"] = None
    game["sheriff_target"] = None
    game["sheriff_shot"] = None
    game["sheriff_action_done"] = False
    game["votes"].clear()
    
    await mute_chat(game["chat_id"], True)
    await send_phase_photo(game["chat_id"], "night", "🌙 Місто знову засинає...")
    
    mafia_team_text = get_mafia_team_str()

    for uid, p in game["players"].items():
        try:
            if p["alive"]:
                if p["role"] == "mafia":
                    await bot.send_message(
                        uid, 
                        f"🔪 Ваша команда:\n{mafia_team_text}\n\nКого вбиваємо?\n\n{get_alive_list_text()}", 
                        reply_markup=get_mafia_keyboard(game["players"])
                    )
                elif p["role"] == "doctor":
                    await bot.send_message(uid, f"🩺 Кого будеш лікувати (себе можна лише 1 раз за гру):\n\n{get_alive_list_text()}", reply_markup=get_doctor_keyboard(game["players"]))
                elif p["role"] == "sheriff":
                    await bot.send_message(uid, f"🕵️ Обирай перевірку чи постріл:\n\n{get_alive_list_text()}", reply_markup=get_sheriff_keyboard(game["players"], uid))
        except Exception:
            pass

    if game["timer_task"]:
        game["timer_task"].cancel()
    game["timer_task"] = asyncio.create_task(night_timer())

async def check_win_condition():
    alive_mafia = sum(1 for p in game["players"].values() if p["alive"] and p["role"] == "mafia")
    alive_non_mafia = sum(1 for p in game["players"].values() if p["alive"] and p["role"] != "mafia")
    
    if alive_mafia == 0:
        game["status"] = "finished"
        summary_text = "🎉 <b>ПЕРЕМОГА МИРНИХ!</b> Всю мафію знищено! 😇\n\n" + format_all_roles_summary()
        await bot.send_message(game["chat_id"], summary_text)
        return True
    elif alive_mafia >= alive_non_mafia:
        game["status"] = "finished"
        summary_text = "🔪 <b>ПЕРЕМОГА МАФІЇ!</b> Вони захопили місто! 😈\n\n" + format_all_roles_summary()
        await bot.send_message(game["chat_id"], summary_text)
        return True
    return False

async def main():
    print("Бот запущено в режимі HTML з повним екрануванням імен та захистом мафії...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
