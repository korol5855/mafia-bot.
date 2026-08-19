import asyncio
import os
import random
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatPermissions,
)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не знайдено")

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()

game = {
    "status": "idle",
    "chat_id": None,
    "players": {},
    "timer_task": None,
    "mafia_votes": {},
    "doctor_target": None,
    "doctor_self_used": False,
    "sheriff_action": None,
    "votes": {},
    "runoff": None,
}

ROLES = {
    "mafia": "Мафія 🔪",
    "doctor": "Лікар 🩺",
    "sheriff": "Шериф 🕵️",
    "lucky": "Щасливчик 🍀",
    "civilian": "Мирний житель 😇",
}

# =========================
# БЕЗПЕЧНІ АСИНХРОННІ ТАЙМЕРИ
# =========================

def cancel_timer():
    if game["timer_task"] and not game["timer_task"].done():
        game["timer_task"].cancel()
    game["timer_task"] = None

def start_async_timer(seconds, coro_func):
    cancel_timer()
    
    async def worker():
        try:
            await asyncio.sleep(seconds)
            await coro_func()
        except asyncio.CancelledError:
            pass

    game["timer_task"] = asyncio.create_task(worker())

# =========================
# ДОПОМІЖНІ ФУНКЦІЇ
# =========================

def alive_ids():
    return {uid for uid, p in game["players"].items() if p["alive"]}

def alive_text():
    players = sorted([p for p in game["players"].values() if p["alive"]], key=lambda p: p["number"])
    return f"📋 Живі гравці ({len(players)}):\n" + "".join(f"• {p['number']}. {p['name']}\n" for p in players)

def role_summary():
    return "📜 Ролі у грі:\n\n" + "".join(
        f"• {p['number']}. {p['name']} — {ROLES[p['role']]} ({'🟢 вижив' if p['alive'] else '💀 мертвий'})\n"
        for p in sorted(game["players"].values(), key=lambda x: x["number"])
    )

async def set_chat_locked(locked: bool):
    if not game["chat_id"]:
        return
    try:
        await bot.set_chat_permissions(game["chat_id"], ChatPermissions(can_send_messages=not locked))
    except Exception:
        pass

# =========================
# КЛАВІАТУРИ
# =========================

def lobby_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Увійти в гру", callback_data="join")],
        [InlineKeyboardButton(text="🚀 Почати гру", callback_data="start")]
    ])

def mafia_keyboard():
    buttons = [[InlineKeyboardButton(text=f"{p['number']}. {p['name']}", callback_data=f"mafia:{uid}")]
               for uid, p in game["players"].items() if p["alive"] and p["role"] != "mafia"]
    buttons.append([InlineKeyboardButton(text="💤 Пропуск", callback_data="mafia:skip")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def doctor_keyboard():
    buttons = [[InlineKeyboardButton(text=f"{p['number']}. {p['name']}", callback_data=f"doctor:{uid}")]
               for uid, p in game["players"].items() if p["alive"]]
    buttons.append([InlineKeyboardButton(text="💤 Пропуск", callback_data="doctor:skip")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def sheriff_keyboard(user_id):
    buttons = []
    for uid, p in game["players"].items():
        if p["alive"] and uid != user_id:
            buttons.append([InlineKeyboardButton(text=f"🔍 Перевірити {p['number']}. {p['name']}", callback_data=f"check:{uid}")])
            buttons.append([InlineKeyboardButton(text=f"🔫 Вистрілити {p['number']}. {p['name']}", callback_data=f"shot:{uid}")])
    buttons.append([InlineKeyboardButton(text="💤 Нічого не робити", callback_data="sheriff:skip")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def vote_keyboard(candidates=None):
    allowed = candidates if candidates is not None else alive_ids()
    buttons = [
        [InlineKeyboardButton(text=f"👉 {p['number']}. {p['name']}", callback_data=f"vote:{uid}")]
        for uid, p in game["players"].items() if uid in allowed and p["alive"]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# =========================
# КОМАНДИ ГРУПИ
# =========================

@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.text.startswith(("/start", "/mafia")))
async def start_game_cmd(message: Message):
    game["status"] = "lobby"
    game["chat_id"] = message.chat.id
    game["players"].clear()
    game["mafia_votes"].clear()
    game["votes"].clear()
    cancel_timer()
    await set_chat_locked(False)
    await message.answer("🎴 НОВА ГРА В МАФІЮ\n\nНатискайте «Увійти в гру» (мінімум 4 гравці).", reply_markup=lobby_keyboard())

@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.text.startswith("/cancel"))
async def cancel_game_cmd(message: Message):
    cancel_timer()
    game["status"] = "idle"
    game["players"].clear()
    game["chat_id"] = None
    await set_chat_locked(False)
    await message.answer("❌ Гру скасовано. Чат відкритий.")

# =========================
# ЛОГІКА ГРИ
# =========================

@dp.callback_query(F.data == "join")
async def join_game(callback: CallbackQuery):
    if game["status"] != "lobby":
        return await callback.answer("Зараз немає набору.", show_alert=True)
    
    uid = callback.from_user.id
    if uid in game["players"]:
        return await callback.answer("Ти вже у грі!", show_alert=True)

    game["players"][uid] = {"name": callback.from_user.first_name or "Гравець", "role": "civilian", "alive": True, "number": 0}
    names = "".join(f"• {p['name']}\n" for p in game["players"].values())
    
    try:
        await callback.message.edit_text(f"🎴 ЗБІР ГРАВЦІВ\n\nУчасники ({len(game['players'])}):\n{names}", reply_markup=lobby_keyboard())
    except Exception:
        pass
    await callback.answer("Ти у грі!")

@dp.callback_query(F.data == "start")
async def launch_game(callback: CallbackQuery):
    if game["status"] != "lobby":
        return await callback.answer("Гра вже йде.", show_alert=True)
    if len(game["players"]) < 4:
        return await callback.answer("Треба мінімум 4 гравці!", show_alert=True)

    ids = list(game["players"].keys())
    random.shuffle(ids)
    for i, uid in enumerate(ids, 1):
        game["players"][uid]["number"] = i

    total = len(ids)
    m_count = 1 if total <= 5 else (2 if total <= 8 else 3)
    
    idx = 0
    for _ in range(m_count):
        game["players"][ids[idx]]["role"] = "mafia"
        idx += 1
    game["players"][ids[idx]]["role"] = "doctor"
    idx += 1
    game["players"][ids[idx]]["role"] = "sheriff"
    idx += 1
    if total > 5:
        game["players"][ids[idx]]["role"] = "lucky"
        idx += 1
    for uid in ids[idx:]:
        game["players"][uid]["role"] = "civilian"

    await callback.message.edit_text("🌙 Місто засинає...")
    await start_night()

async def start_night():
    cancel_timer()
    game["status"] = "night"
    game["mafia_votes"].clear()
    game["doctor_target"] = None
    game["sheriff_action"] = None
    await set_chat_locked(True)

    await bot.send_message(game["chat_id"], "🌙 НІЧ\n\n🔒 Чат закритий. Ролі у ЛС.")
    mafia_team = "\n".join(f"• {p['name']}" for p in game["players"].values() if p["role"] == "mafia")

    for uid, p in game["players"].items():
        if not p["alive"]: continue
        try:
            if p["role"] == "mafia":
                await bot.send_message(uid, f"🔪 **МАФІЯ**\nКоманда:\n{mafia_team}\n\nОбери жертву:\n\n{alive_text()}", reply_markup=mafia_keyboard())
            elif p["role"] == "doctor":
                await bot.send_message(uid, "🩺 **ЛІКАР**\nОбери кого лікувати:", reply_markup=doctor_keyboard())
            elif p["role"] == "sheriff":
                await bot.send_message(uid, "🕵️ **ШЕРИФ**\nОбери дію:", reply_markup=sheriff_keyboard(uid))
            elif p["role"] == "lucky":
                await bot.send_message(uid, "🍀 **ЩАСЛИВЧИК**\nТи маєш шанс пережити кулю.")
            else:
                await bot.send_message(uid, "😇 **МИРНИЙ**\nСпи мирно.")
        except Exception:
            pass

    start_async_timer(40, resolve_night)

async def check_night_ready():
    alive_m = [u for u, p in game["players"].items() if p["alive"] and p["role"] == "mafia"]
    m_done = all(u in game["mafia_votes"] for u in alive_m)
    
    d_alive = any(p["alive"] and p["role"] == "doctor" for p in game["players"].values())
    d_done = not d_alive or game["doctor_target"] is not None

    s_alive = any(p["alive"] and p["role"] == "sheriff" for p in game["players"].values())
    s_done = not s_alive or game["sheriff_action"] is not None

    if m_done and d_done and s_done:
        cancel_timer()
        await resolve_night()

@dp.callback_query(F.data.startswith("mafia:"))
async def m_action(callback: CallbackQuery):
    if game["status"] != "night": return
    uid = callback.from_user.id
    val = callback.data.split(":")[1]
    game["mafia_votes"][uid] = "skip" if val == "skip" else int(val)
    await callback.message.edit_text("💤 Пропуск" if val == "skip" else "🔪 Ціль обрано")
    await callback.answer("Збережено")
    await check_night_ready()

@dp.callback_query(F.data.startswith("doctor:"))
async def d_action(callback: CallbackQuery):
    if game["status"] != "night": return
    val = callback.data.split(":")[1]
    game["doctor_target"] = "skip" if val == "skip" else int(val)
    await callback.message.edit_text("💤 Пропуск" if val == "skip" else "🩺 Лікування обрано")
    await callback.answer("Збережено")
    await check_night_ready()

@dp.callback_query(F.data.startswith(("check:", "shot:", "sheriff:skip")))
async def s_action(callback: CallbackQuery):
    if game["status"] != "night": return
    data = callback.data
    if data == "sheriff:skip":
        game["sheriff_action"] = ("skip", None)
        await callback.message.edit_text("💤 Пропуск")
    else:
        action, target = data.split(":")
        target = int(target)
        game["sheriff_action"] = (action, target)
        if action == "check":
            res = "🔪 МАФІЯ" if game["players"][target]["role"] == "mafia" else "😇 НЕ МАФІЯ"
            await callback.message.edit_text(f"🔍 Результат: {res}")
        else:
            await callback.message.edit_text("🔫 Постріл збережено")
    await callback.answer("Збережено")
    await check_night_ready()

async def resolve_night():
    if game["status"] != "night": return
    cancel_timer()

    counts = {}
    for uid in [u for u, p in game["players"].items() if p["alive"] and p["role"] == "mafia"]:
        t = game["mafia_votes"].get(uid)
        if isinstance(t, int): counts[t] = counts.get(t, 0) + 1

    m_target = max(counts, key=counts.get) if counts else None
    d_target = game["doctor_target"]
    text = "🌅 **РАНОК**\n\n"

    if m_target is None:
        text += "🔪 Мафія нікого не вбила.\n"
    elif m_target == d_target:
        text += f"🩺 Лікар врятував **{game['players'][m_target]['name']}**!\n"
    else:
        p = game["players"][m_target]
        if p["role"] == "lucky" and not p.get("lucky_used"):
            p["lucky_used"] = True
            text += f"🍀 **{p['name']}** пережив кулю!\n"
        else:
            p["alive"] = False
            text += f"💀 Мафія вбила **{p['name']}** ({ROLES[p['role']]}).\n"

    s_act = game["sheriff_action"]
    if s_act and s_act[0] == "shot":
        st = s_act[1]
        if st in game["players"] and game["players"][st]["alive"]:
            sp = game["players"][st]
            if st == d_target:
                text += f"🩺 Лікар врятував **{sp['name']}** від шерифа!\n"
            else:
                sp["alive"] = False
                text += f"🔫 Шериф застрелив **{sp['name']}** ({ROLES[sp['role']]}).\n"

    text += "\n" + alive_text()
    await set_chat_locked(False)
    await bot.send_message(game["chat_id"], text)

    if await check_win(): return

    game["status"] = "day"
    await bot.send_message(
        game["chat_id"], 
        "🗣 **ДЕНЬ**\n💬 Чат відкритий на 60 секунд.\n*(Або натисніть кнопку нижче, щоб завершити обговорення достроково)*",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏩ Завершити обговорення", callback_data="force_voting")]])
    )
    
    start_async_timer(60, start_voting)

@dp.callback_query(F.data == "force_voting")
async def force_voting_handler(callback: CallbackQuery):
    if game["status"] != "day":
        return await callback.answer("Зараз не день.", show_alert=True)
    cancel_timer()
    await callback.answer("Переходимо до голосування!")
    await start_voting()

async def start_voting(candidates=None):
    if game["status"] not in {"day", "voting"}:
        return
    cancel_timer()
    game["status"] = "voting"
    game["votes"].clear()
    await set_chat_locked(True)
    
    await bot.send_message(game["chat_id"], "⚖️ **ГОЛОСУВАННЯ**\n🔒 Чат закритий.", reply_markup=vote_keyboard(candidates))
    start_async_timer(30, resolve_voting)

@dp.callback_query(F.data.startswith("vote:"))
async def vote_action(callback: CallbackQuery):
    if game["status"] != "voting": return
    uid = callback.from_user.id
    if uid not in game["players"] or not game["players"][uid]["alive"]:
        return await callback.answer("Мертві не голосують.", show_alert=True)
    
    target = int(callback.data.split(":")[1])
    game["votes"][uid] = target
    try:
        await callback.message.edit_text(f"🗳 Голос прийнято за {game['players'][target]['name']}.")
    except Exception:
        pass
    await callback.answer("Голос прийнято")

    if set(game["votes"].keys()) >= alive_ids():
        cancel_timer()
        await resolve_voting()

async def resolve_voting():
    if game["status"] != "voting": return
    cancel_timer()

    counts = {}
    alive = alive_ids()
    for v, t in game["votes"].items():
        if v in alive and t in alive:
            counts[t] = counts.get(t, 0) + 1

    if not counts:
        await finish_voting("⚖️ Ніхто не проголосував.")
        return

    max_v = max(counts.values())
    cands = [u for u, c in counts.items() if c == max_v]

    if len(cands) > 1:
        if game["runoff"]:
            game["runoff"] = None
            await finish_voting("⚖️ Нічия. Ніхто не вигнаний.")
        else:
            game["runoff"] = cands
            await start_voting(cands)
        return

    exiled = cands[0]
    game["players"][exiled]["alive"] = False
    game["runoff"] = None
    await finish_voting(f"⚖️ Вигнано **{game['players'][exiled]['name']}** ({ROLES[game['players'][exiled]['role']]})")

async def finish_voting(text):
    await set_chat_locked(False)
    await bot.send_message(game["chat_id"], text + "\n\n" + alive_text())
    if await check_win(): return
    await start_night()

async def check_win():
    mafia = sum(1 for p in game["players"].values() if p["alive"] and p["role"] == "mafia")
    others = sum(1 for p in game["players"].values() if p["alive"] and p["role"] != "mafia")

    if mafia == 0:
        cancel_timer()
        game["status"] = "finished"
        await set_chat_locked(False)
        await bot.send_message(game["chat_id"], "🎉 **ПЕРЕМОГА МИРНИХ!**\n\n" + role_summary())
        return True
    if mafia >= others:
        cancel_timer()
        game["status"] = "finished"
        await set_chat_locked(False)
        await bot.send_message(game["chat_id"], "🔪 **ПЕРЕМОГА МАФІЇ!**\n\n" + role_summary())
        return True
    return False

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
