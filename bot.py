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

bot = Bot(token=TOKEN)
dp = Dispatcher()

game = {
    "status": "waiting",
    "players": {},       # {user_id: {"name": str, "role": str, "alive": bool, "number": int}}
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

def get_sheriff_keyboard(players):
    buttons = [
        [InlineKeyboardButton(text=f"🔍 Перевірити: {p['number']}. {p['name']}", callback_data=f"check_{uid}")]
        for uid, p in players.items() if p["alive"]
    ]
    buttons.append([InlineKeyboardButton(text="--- АБО ВИСТРІЛ ---", callback_data="ignore")])
    for uid, p in players.items():
        if p["alive"]:
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
    text = f"📋 **Живі гравці у місті ({len(alive_players)}):**\n"
    for p in sorted(alive_players, key=lambda x: x["number"]):
        text += f"• {p['number']}. {p['name']}\n"
    return text

def format_all_roles_summary():
    text = "📜 **Склад завершеної гри (хто ким був):**\n\n"
    for p in sorted(game["players"].values(), key=lambda x: x["number"]):
        status = "💀 мертвий" if not p["alive"] else "🟢 вижив"
        text += f"• {p['number']}. {p['name']} — {ROLE_ICONS.get(p['role'], p['role'])} ({status})\n"
    return text

def get_mafia_team_str():
    mafia_members = [p['name'] for p in game["players"].values() if p["role"] == "mafia"]
    return "\n".join([f"• {name}" for name in mafia_members])

# --- КОМАНДИ ---
@dp.message(F.text == "/start", F.chat.type == "private")
async def private_start(message: Message):
    user_name = message.from_user.first_name
    await message.answer(
        f"👋 Привіт, **{user_name}**! Вітаю тебе в боті для гри в **Мафію**.\n\n"
        "📜 **Правила гри та ролі:**\n"
        "🔪 **Мафія** — спільно обирає жертву для вбивства.\n"
        "🩺 **Доктор** — може врятувати від кулі мафії себе чи іншого гравця.\n"
        "🕵️ **Шериф** — перевіряє підозрілих або може сам відкрити вогонь.\n"
        "😇 **Мирний житель** — бере участь у денних обговореннях і голосуваннях."
    )

@dp.message(F.text.startswith("/"))
async def cmd_commands(message: Message):
    text = message.text.lower().split('@')[0]
    
    if text in ["/mafia", "/start"] and message.chat.type != "private":
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
        
    game["players"][user.id] = {"name": user.first_name, "role": "civilian", "alive": True, "number": 0}
    names = [p["name"] for p in game["players"].values()]
    
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
    if total_players < 3:
        return await callback.answer("Потрібно мінімум 3 гравці для повноцінної гри!", show_alert=True)
    
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
    
    # Динамічний баланс мафії
    if total_players <= 5:
        mafia_count = 1
    elif total_players <= 9:
        mafia_count = 2
    elif total_players <= 13:
        mafia_count = 3
    elif total_players <= 17:
        mafia_count = 4
    else:
        mafia_count = 5

    for i in range(mafia_count):
        game["players"][user_ids[i]]["role"] = "mafia"
    
    game["players"][user_ids[mafia_count]]["role"] = "doctor"
    game["players"][user_ids[mafia_count + 1]]["role"] = "sheriff"
    
    for uid in user_ids[mafia_count + 2:]:
        game["players"][uid]["role"] = "civilian"
        
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
                    f"🔪 **Ти МАФІЯ.**\n\nВаша команда:\n{mafia_team_text}\n\nКого вбиваємо?\n\n{get_alive_list_text()}", 
                    reply_markup=get_mafia_keyboard(game["players"])
                )
            elif p["role"] == "doctor":
                await bot.send_message(uid, f"🩺 Ти ДОКТОР. Кого будеш лікувати:\n\n{get_alive_list_text()}", reply_markup=get_doctor_keyboard(game["players"]))
            elif p["role"] == "sheriff":
                await bot.send_message(uid, f"🕵️ Ти ШЕРИФ. Обирай перевірку чи постріл:\n\n{get_alive_list_text()}", reply_markup=get_sheriff_keyboard(game["players"]))
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

# --- ОБРОБКА НІЧНИХ ДІЙ (З ПЕРЕВІРКОЮ ЖИТТЯ ГРАВЦЯ) ---
@dp.callback_query(F.data.startswith(("mkel_", "heal_", "check_", "shot_", "heal_skip")))
async def cb_night_actions(callback: CallbackQuery):
    if game["status"] != "night":
        return await callback.answer("❌ Ця ніч вже закінчилася або ще не почалася!", show_alert=True)
        
    user_id = callback.from_user.id
    player = game["players"].get(user_id)
    
    # Перевірка, чи гравець існує і чи він ЖИВИЙ
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
                
        # Додатково переконуємося в реальному часі, що мафія досі жива
        if not player["alive"]:
            return await callback.answer("❌ Ви загинули і більше не голосуєте!", show_alert=True)
            
        game["mafia_votes"][user_id] = target_val
        
        if target_val == "skip":
            choice_text = "💤 Ви проголосували за те, щоб нікого не вбивати."
        else:
            target_name = game["players"].get(target_val, {}).get("name", "")
            choice_text = f"✅ Ваш голос за ціль: **{target_name}**"
        
        await bot.send_message(game["chat_id"], chat_messages["mkel"])
        
    elif action in ["heal", "heal_skip"]:
        if player["role"] != "doctor":
            return await callback.answer("❌ Ця дія доступна лише доктору!", show_alert=True)
            
        if not player["alive"]:
            return await callback.answer("❌ Ви загинули!", show_alert=True)
            
        if action == "heal_skip":
            game["doctor_target"] = "skip"
            choice_text = "💤 Ви нікого не лікували цієї ночі."
            await bot.send_message(game["chat_id"], chat_messages["heal_skip"])
        else:
            target_id = int(data[1])
            target_player = game["players"].get(target_id)
            if not target_player or not target_player["alive"]:
                return await callback.answer("❌ Цей гравець уже мертвий!", show_alert=True)
                
            game["doctor_target"] = target_id
            target_name = target_player["name"]
            choice_text = f"✅ Ви обрали кого лікувати: **{target_name}**"
            await bot.send_message(game["chat_id"], chat_messages["heal"])
            
    elif action in ["check", "shot"]:
        if player["role"] != "sheriff":
            return await callback.answer("❌ Ця дія доступна лише шерифу!", show_alert=True)
            
        if not player["alive"]:
            return await callback.answer("❌ Ви загинули!", show_alert=True)
            
        if game["sheriff_action_done"]:
            return await callback.answer("Ти вже зробив дію цієї ночі!", show_alert=True)
            
        target_id = int(data[1])
        target_player = game["players"].get(target_id)
        if not target_player or not target_player["alive"]:
            return await callback.answer("❌ Цей гравець уже мертвий!", show_alert=True)
            
        if action == "check":
            target_role = target_player["role"]
            res = "мафія 🔪" if target_role == "mafia" else "мирний житель 😇"
            target_name = target_player["name"]
            choice_text = f"✅ Перевірено **{target_name}** — виявився(-лась) як **{res}**"
            
            await callback.message.answer(f"🔍 Перевірка завершена: {target_name} виявився(-лась) — {res}")
            game["sheriff_target"] = target_id
            game["sheriff_action_done"] = True
            await bot.send_message(game["chat_id"], chat_messages["check"])
            
        elif action == "shot":
            target_name = target_player["name"]
            choice_text = f"🎯 Ви зробили постріл у гравця: **{target_name}**"
            
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
        for m_id, t_id in game["mafia_votes"].items():
            if game["players"].get(m_id, {}).get("alive", False):
                t_counts[t_id] = t_counts.get(t_id, 0) + 1
        if t_counts:
            max_v = max(t_counts.values())
            candidates = [t for t, cnt in t_counts.items() if cnt == max_v]
            victim = random.choice(candidates)

    doctor = game["doctor_target"]
    sheriff_shot = game["sheriff_shot"]
    
    text = "🌅 Ранок у місті.\n\n"
    
    if victim and victim != "skip":
        if victim == doctor:
            text += f"🩺 Доктор врятував {game['players'][victim]['name']} від кулі мафії!\n"
        else:
            if game["players"].get(victim, {}).get("alive", False):
                game["players"][victim]["alive"] = False
                role_key = game["players"][victim]["role"]
                role_name = ROLE_ICONS.get(role_key, role_key)
                text += f"💀 Вбито мафією: **{game['players'][victim]['name']}**! Роль була: **{role_name}** 🪦\n"
    else:
        text += "Ніч від мафії пройшла спокійно (нікого не вбили).\n"
        
    if sheriff_shot:
        if sheriff_shot == doctor and sheriff_shot != victim:
            text += f"🛡 Доктор також залікував рану від пострілу шерифа по {game['players'][sheriff_shot]['name']}!\n"
        else:
            if game["players"].get(sheriff_shot, {}).get("alive", False):
                game["players"][sheriff_shot]["alive"] = False
                shot_role_key = game["players"][sheriff_shot]["role"]
                shot_role = ROLE_ICONS.get(shot_role_key, shot_role_key)
                text += f"🎯 Шериф здійснив постріл і вбив **{game['players'][sheriff_shot]['name']}**! Роль була: **{shot_role}** 🪦\n"

    text += "\n" + get_alive_list_text()

    await mute_chat(game["chat_id"], False)
    await send_phase_photo(game["chat_id"], "day", text)
    
    if await check_win_condition():
        return

    game["status"] = "discussion"
    await bot.send_message(game["chat_id"], "🗣 Чат відкрито! Обговорення рівно **1 хвилину** ⏳")
    
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
        names = ", ".join([f"{game['players'][uid]['number']}. {game['players'][uid]['name']}" for uid in candidate_ids])
        msg_text = f"⚖️ ПЕРЕСТРІЛКА! Голоси розділилися рівно між: **{names}**.\nУ вас є 30 секунд на вирішальне голосування!"
    else:
        msg_text = "⚖️ Час голосування! Обирайте підозрюваного:\n\n" + get_alive_list_text()

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
    if not game["players"].get(voter_id, {}).get("alive", False):
        return await callback.answer("Мертві не голосують!", show_alert=True)
        
    target_id = int(callback.data.split("_")[1])
    target_player = game["players"].get(target_id)
    if not target_player or not target_player["alive"]:
        return await callback.answer("❌ Не можна голосувати за мертвого гравця!", show_alert=True)
        
    game["votes"][voter_id] = target_id
    target_name = target_player["name"]
    
    try:
        await callback.message.edit_text(
            f"🗳 Ваш голос за гравця **{target_name}** успішно прийнято. Очікуємо інших...", 
            reply_markup=None
        )
    except Exception:
        pass

    await callback.answer("Голос прийнято!")
    
    alive_players = [uid for uid, p in game["players"].items() if p["alive"]]
    if len(game["votes"]) >= len(alive_players):
        if game["timer_task"]:
            game["timer_task"].cancel()
        await resolve_voting()

async def resolve_voting():
    if game["status"] != "voting": return
    
    vote_counts = {}
    for voter, target in game["votes"].items():
        vote_counts[target] = vote_counts.get(target, 0) + 1
        
    text = "📊 Результати голосування:\n\n"
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
            names_str = ", ".join([f"{game['players'][c]['number']}. {game['players'][c]['name']}" for c in candidates])
            text += f"⚖️ Нічия! Кілька гравців набрали однакову кількість голосів: **{names_str}**.\nЗапускаємо додатковий раунд голосування між ними!"
            await bot.send_message(game["chat_id"], text)
            await start_voting(candidate_ids=candidates)
            return
        
        exiled = candidates[0]
        game["players"][exiled]["alive"] = False
        role_key = game["players"][exiled]["role"]
        role_name = ROLE_ICONS.get(role_key, role_key)
        text += f"⚖️ Місто вигнало гравця **{game['players'][exiled]['name']}**.\nЙого роль була: **{role_name}** 🪦"
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
                    await bot.send_message(uid, f"🩺 Кого будеш лікувати:\n\n{get_alive_list_text()}", reply_markup=get_doctor_keyboard(game["players"]))
                elif p["role"] == "sheriff":
                    await bot.send_message(uid, f"🕵️ Обирай перевірку чи постріл:\n\n{get_alive_list_text()}", reply_markup=get_sheriff_keyboard(game["players"]))
        except Exception:
            pass

    if game["timer_task"]:
        game["timer_task"].cancel()
    game["timer_task"] = asyncio.create_task(night_timer())

async def check_win_condition():
    alive_mafia = sum(1 for p in game["players"].values() if p["alive"] and p["role"] == "mafia")
    alive_non_mafia = sum(1 for p in game["players"].values() if p["alive"] and p["role"] != "mafia")
    
    if alive_mafia == 0:
        summary_text = "🎉 ПЕРЕМОГА МИРНИХ! Всю мафію знищено! 😇\n\n" + format_all_roles_summary()
        await bot.send_message(game["chat_id"], summary_text)
        game["status"] = "waiting"
        return True
    elif alive_mafia >= alive_non_mafia:
        summary_text = "🔪 ПЕРЕМОГА МАФІЇ! Вони захопили місто! 😈\n\n" + format_all_roles_summary()
        await bot.send_message(game["chat_id"], summary_text)
        game["status"] = "waiting"
        return True
    return False

async def main():
    print("Бот 'Мафія' оновлено: додано захист та перевірки ролей на нічних кнопках...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
