import asyncio
import os
import random
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, 
    FSInputFile, ChatPermissions
)

TOKEN = "8812567133:AAE2nKwacL4VDY_XCLhuXRO4XlRMurMLgS4"

bot = Bot(token=TOKEN)
dp = Dispatcher()

game = {
    "status": "waiting",
    "players": {},       # {user_id: {"name": str, "role": str, "alive": bool, "number": int}}
    "chat_id": None,
    "mafia_target": None,
    "doctor_target": None,
    "commissioner_target": None,
    "commissioner_shot": None,
    "sheriff_action_done": False,
    "votes": {},         # {voter_id: target_id}
    "runoff_candidates": [], # Список кандидатів у разі нічиєї
    "timer_task": None
}

# --- КЛАВІАТУРИ ---
def get_join_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Увійти в гру", callback_data="join_game")],
        [InlineKeyboardButton(text="🚀 Почати гру", callback_data="start_game")]
    ])

def get_mafia_keyboard(players):
    buttons = [
        [InlineKeyboardButton(text=f"{p['number']}. {p['name']}", callback_data=f"kill_{uid}")]
        for uid, p in players.items() if p["alive"] and p["role"] != "mafia"
    ]
    buttons.append([InlineKeyboardButton(text="💤 Нікого не вбивати", callback_data="kill_skip")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_doctor_keyboard(players):
    buttons = [
        [InlineKeyboardButton(text=f"{p['number']}. {p['name']}", callback_data=f"heal_{uid}")]
        for uid, p in players.items() if p["alive"]
    ]
    buttons.append([InlineKeyboardButton(text="💤 Нікого не лікувати", callback_data="heal_skip")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_comm_keyboard(players):
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
    role_icons = {
        "mafia": "Мафія 🔪",
        "doctor": "Доктор 🩺",
        "commissioner": "Шериф 🕵️",
        "civilian": "Мирний житель 😇"
    }
    for p in sorted(game["players"].values(), key=lambda x: x["number"]):
        status = "💀 мертвий" if not p["alive"] else "🟢 вижив"
        text += f"• {p['number']}. {p['name']} — {role_icons.get(p['role'], p['role'])} ({status})\n"
    return text

# --- КОМАНДИ ---
@dp.message(F.text.startswith("/"))
async def cmd_commands(message: Message):
    text = message.text.lower().split('@')[0]
    
    if text in ["/mafia", "/start"]:
        game["status"] = "waiting"
        game["players"].clear()
        game["chat_id"] = message.chat.id
        await message.answer(
            "🕶 Місто засинає... Оголошено збір на Мафію!\n\n"
            "Тисніть кнопку нижче для участі:\n*(Не забудьте написати /start в ЛС боту!)*", 
            reply_markup=get_join_keyboard()
        )
        
    elif text == "/cancel":
        game["status"] = "waiting"
        game["players"].clear()
        if game["timer_task"]:
            game["timer_task"].cancel()
        await mute_chat(message.chat.id, False)
        await message.answer("❌ ГРУ СКАСОВАНО! Чат розблоковано. Можна розпочати нову за командою /mafia")

# --- ВХІД ТА СТАРТ ГРИ ---
@dp.callback_query(F.data == "join_game")
async def cb_join(callback: CallbackQuery):
    if game["status"] != "waiting":
        return await callback.answer("Гра вже почалася!", show_alert=True)
        
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
    if len(game["players"]) < 3:
        return await callback.answer("Потрібно мінімум 3 гравці для повноцінної гри!", show_alert=True)
    
    game["status"] = "night"
    game["mafia_target"] = None
    game["doctor_target"] = None
    game["commissioner_target"] = None
    game["commissioner_shot"] = None
    game["sheriff_action_done"] = False
    game["votes"].clear()
    game["runoff_candidates"].clear()
    
    user_ids = list(game["players"].keys())
    random.shuffle(user_ids)
    
    # Нумеруємо гравців від 1
    for i, uid in enumerate(user_ids, start=1):
        game["players"][uid]["number"] = i
    
    game["players"][user_ids[0]]["role"] = "mafia"
    game["players"][user_ids[1]]["role"] = "doctor"
    game["players"][user_ids[2]]["role"] = "commissioner"
    for uid in user_ids[3:]:
        game["players"][uid]["role"] = "civilian"
        
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    await mute_chat(game["chat_id"], True)
    await send_phase_photo(game["chat_id"], "night", "🌙 Місто засинає. Чат заблоковано. Ролі роздано в ЛС!")
    
    for uid, p in game["players"].items():
        try:
            if p["role"] == "mafia":
                await bot.send_message(uid, f"🔪 Ти МАФІЯ. Обирай жертву:\n\n{get_alive_list_text()}", reply_markup=get_mafia_keyboard(game["players"]))
            elif p["role"] == "doctor":
                await bot.send_message(uid, f"🩺 Ти ДОКТОР. Кого будеш лікувати:\n\n{get_alive_list_text()}", reply_markup=get_doctor_keyboard(game["players"]))
            elif p["role"] == "commissioner":
                await bot.send_message(uid, f"🕵️ Ти КОМІСАР. Обирай перевірку чи постріл:\n\n{get_alive_list_text()}", reply_markup=get_comm_keyboard(game["players"]))
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

# --- ОБРОБКА НІЧНИХ ДІЙ З ЗАХИСТОМ ВІД СТАРИХ КНОПОК ---
@dp.callback_query(F.data.startswith(("kill_", "heal_", "check_", "shot_", "kill_skip", "heal_skip")))
async def cb_night_actions(callback: CallbackQuery):
    if game["status"] != "night":
        return await callback.answer("❌ Ця ніч вже закінчилася або ще не почалася!", show_alert=True)
        
    data = callback.data.split("_")
    action = data[0]
    
    chat_messages = {
        "kill": "🌃 Ніч стає темнішою... Мафія підібралася до своєї жертви.",
        "kill_skip": "💤 Мафія сьогодні занадто милосердна. У місті тихо.",
        "heal": "🛡 Лікар на посту. Він робить все, щоб врятувати чиєсь життя.",
        "heal_skip": "🥃 Лікар вирішив відсидітися вдома. Сьогодні кожен сам за себе.",
        "check": "🔎 Шериф крадькома перевіряє підозрілих осіб.",
        "shot": "💥 Шериф зняв запобіжник. Повітря в місті стало напруженим..."
    }

    if action == "kill":
        game["mafia_target"] = int(data[1])
        await bot.send_message(game["chat_id"], chat_messages["kill"])
    elif action == "kill_skip":
        game["mafia_target"] = "skip"
        await bot.send_message(game["chat_id"], chat_messages["kill_skip"])
    elif action == "heal":
        game["doctor_target"] = int(data[1])
        await bot.send_message(game["chat_id"], chat_messages["heal"])
    elif action == "heal_skip":
        game["doctor_target"] = "skip"
        await bot.send_message(game["chat_id"], chat_messages["heal_skip"])
    elif action == "check":
        if game["sheriff_action_done"]:
            return await callback.answer("Ти вже зробив дію цієї ночі!", show_alert=True)
        target_id = int(data[1])
        target_role = game["players"].get(target_id, {}).get("role")
        res = "мафія 🔪" if target_role == "mafia" else "мирний житель 😇"
        await callback.message.answer(f"🔍 Перевірка завершена: {game['players'][target_id]['name']} виявився(-лась) — {res}")
        game["commissioner_target"] = target_id
        game["sheriff_action_done"] = True
        await bot.send_message(game["chat_id"], chat_messages["check"])
    elif action == "shot":
        if game["sheriff_action_done"]:
            return await callback.answer("Ти вже зробив дію цієї ночі!", show_alert=True)
        target_id = int(data[1])
        game["commissioner_shot"] = target_id
        game["sheriff_action_done"] = True
        await callback.message.answer(f"🔫 Ти вистрілив у гравця: {game['players'][target_id]['name']}")
        await bot.send_message(game["chat_id"], chat_messages["shot"])

    await callback.message.edit_text("✅ Вибір збережено.")
    await check_night_actions()

async def check_night_actions():
    mafia_done = game["mafia_target"] is not None
    doctor_done = game["doctor_target"] is not None
    
    comm_alive = any(p["alive"] for uid, p in game["players"].items() if p["role"] == "commissioner")
    comm_done = not comm_alive or game["sheriff_action_done"]
    
    if mafia_done and doctor_done and comm_done:
        if game["timer_task"]:
            game["timer_task"].cancel()
        await resolve_night()

async def resolve_night():
    if game["status"] != "night": return
    
    victim = game["mafia_target"]
    doctor = game["doctor_target"]
    comm_shot = game["commissioner_shot"]
    
    text = "🌅 Ранок у місті.\n\n"
    
    # Жертва мафії та розкриття ролі
    if victim and victim != "skip":
        if victim == doctor:
            text += f"🩺 Доктор врятував {game['players'][victim]['name']} від кулі мафії!\n"
        else:
            if game["players"].get(victim, {}).get("alive", False):
                game["players"][victim]["alive"] = False
                role_name = game["players"][victim]["role"]
                text += f"💀 Вбито мафією: **{game['players'][victim]['name']}**! Роль була: **{role_name}** 🪦\n"
    else:
        text += "Ніч від мафії пройшла спокійно.\n"
        
    # Постріл шерифа та розкриття ролі
    if comm_shot:
        if comm_shot == doctor and comm_shot != victim:
            text += f"🛡 Доктор також залікував рану від пострілу шерифа по {game['players'][comm_shot]['name']}!\n"
        else:
            if game["players"].get(comm_shot, {}).get("alive", False):
                game["players"][comm_shot]["alive"] = False
                shot_role = game["players"][comm_shot]["role"]
                text += f"🎯 Шериф здійснив постріл і вбив **{game['players'][comm_shot]['name']}**! Роль була: **{shot_role}** 🪦\n"

    text += "\n" + get_alive_list_text()

    await mute_chat(game["chat_id"], False)
    await send_phase_photo(game["chat_id"], "day", text)
    
    if check_win_condition():
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

# --- ГОЛОСУВАННЯ ТА СИСТЕМА НІЧИЙНОЇ ПЕРЕСТРІЛКИ ---
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
    game["votes"][voter_id] = target_id
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
        
        # Якщо це була перестрілка і знову нічия — нікого не виганяємо
        if game["runoff_candidates"] and len(candidates) > 1:
            text += "Нічия під час перестрілки! Місто вирішило нікого не виганяти цього разу."
            game["runoff_candidates"].clear()
            await finalize_voting_round(text)
            return

        # Якщо звичайна нічия (кілька кандидатів набрали однаково) — запускаємо перестрілку
        if len(candidates) > 1:
            game["runoff_candidates"] = candidates
            names_str = ", ".join([f"{game['players'][c]['number']}. {game['players'][c]['name']}" for c in candidates])
            text += f"⚖️ Нічия! Кілька гравців набрали однакову кількість голосів: **{names_str}**.\nЗапускаємо додатковий раунд голосування між ними!"
            await bot.send_message(game["chat_id"], text)
            await start_voting(candidate_ids=candidates)
            return
        
        # Якщо є один чіткий кандидат на вигнання
        exiled = candidates[0]
        game["players"][exiled]["alive"] = False
        role_name = game["players"][exiled]["role"]
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
    
    if check_win_condition():
        return
        
    # Новий раунд (ніч)
    game["status"] = "night"
    game["mafia_target"] = None
    game["doctor_target"] = None
    game["commissioner_target"] = None
    game["commissioner_shot"] = None
    game["sheriff_action_done"] = False
    game["votes"].clear()
    
    await mute_chat(game["chat_id"], True)
    await send_phase_photo(game["chat_id"], "night", "🌙 Місто знову засинає...")
    
    for uid, p in game["players"].items():
        try:
            if p["alive"]:
                if p["role"] == "mafia":
                    await bot.send_message(uid, f"🔪 Обирай жертву:\n\n{get_alive_list_text()}", reply_markup=get_mafia_keyboard(game["players"]))
                elif p["role"] == "doctor":
                    await bot.send_message(uid, f"🩺 Кого будеш лікувати:\n\n{get_alive_list_text()}", reply_markup=get_doctor_keyboard(game["players"]))
                elif p["role"] == "commissioner":
                    await bot.send_message(uid, f"🕵️ Обирай перевірку чи постріл:\n\n{get_alive_list_text()}", reply_markup=get_comm_keyboard(game["players"]))
        except Exception:
            pass

    if game["timer_task"]:
        game["timer_task"].cancel()
    game["timer_task"] = asyncio.create_task(night_timer())

def check_win_condition():
    alive_mafia = sum(1 for p in game["players"].values() if p["alive"] and p["role"] == "mafia")
    alive_civilians = sum(1 for p in game["players"].values() if p["alive"] and p["role"] != "mafia")
    
    if alive_mafia == 0:
        summary_text = "🎉 ПЕРЕМОГА МИРНИХ! Всю мафію знищено! 😇\n\n" + format_all_roles_summary()
        bot.loop.create_task(bot.send_message(game["chat_id"], summary_text))
        game["status"] = "waiting"
        return True
    elif alive_mafia >= alive_civilians:
        summary_text = "🔪 ПЕРЕМОГА МАФІЇ! Вони захопили місто! 😈\n\n" + format_all_roles_summary()
        bot.loop.create_task(bot.send_message(game["chat_id"], summary_text))
        game["status"] = "waiting"
        return True
    return False

async def main():
    print("Бот 'Мафія' оновлено і повністю готовий до роботи...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
