import asyncio
import logging
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не знайдено в середовищі!")

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()

games = {}

ROLES = {
    "mafia": "Мафія 🔪",
    "doctor": "Лікар 🩺",
    "sheriff": "Шериф 🕵️",
    "lucky": "Щасливчик 🍀",
    "civilian": "Мирний житель 😇",
}

def get_game(chat_id: int):
    if chat_id not in games:
        games[chat_id] = {
            "status": "idle",
            "chat_id": chat_id,
            "players": {},
            "timer_task": None,
            "mafia_votes": {},
            "doctor_target": None,
            "sheriff_action": None,
            "votes": {},
            "runoff": None,
        }
    return games[chat_id]

def cancel_timer(g):
    if g["timer_task"] and not g["timer_task"].done():
        g["timer_task"].cancel()
    g["timer_task"] = None

def start_timer(g, seconds, coro):
    cancel_timer(g)
    async def worker():
        try:
            await asyncio.sleep(seconds)
            await coro(g)
        except asyncio.CancelledError:
            pass
    g["timer_task"] = asyncio.create_task(worker())

async def reset_game(chat_id: int):
    if chat_id in games:
        cancel_timer(games[chat_id])
        del games[chat_id]
    logging.info(f"Гра в чаті {chat_id} повністю скинута.")

def alive_ids(g):
    return {uid for uid, p in g["players"].items() if p["alive"]}

def alive_text(g):
    players = sorted([p for p in g["players"].values() if p["alive"]], key=lambda p: p["number"])
    return f"📋 Живі гравці ({len(players)}):\n" + "".join(f"• {p['number']}. {p['name']}\n" for p in players)

def role_summary(g):
    return "📜 Ролі у грі:\n\n" + "".join(
        f"• {p['number']}. {p['name']} — {ROLES[p['role파일명'] if 'role' in p else 'civilian']} ({'🟢 вижив' if p['alive'] else '💀 мертвий'})\n"
        for p in sorted(g["players"].values(), key=lambda x: x["number"])
    )

async def set_chat_locked(chat_id: int, locked: bool):
    try:
        await bot.set_chat_permissions(
            chat_id,
            ChatPermissions(
                can_send_messages=not locked,
                can_send_media_messages=not locked,
                can_send_polls=not locked,
                can_send_other_messages=not locked,
                can_add_web_page_previews=not locked
            )
        )
    except Exception as e:
        logging.error(f"Помилка зміни прав чату {chat_id}: {e}")

# --- КЛАВІАТУРИ ---
def lobby_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Увійти в гру", callback_data="join")],
        [InlineKeyboardButton(text="🚀 Почати гру", callback_data="start")]
    ])

def mafia_kb(g, chat_id):
    btns = [[InlineKeyboardButton(text=f"{p['number']}. {p['name']}", callback_data=f"m:{uid}:{chat_id}")]
            for uid, p in g["players"].items() if p["alive"] and p["role"] != "mafia"]
    btns.append([InlineKeyboardButton(text="💤 Пропуск", callback_data=f"m:skip:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def doctor_kb(g, chat_id):
    btns = [[InlineKeyboardButton(text=f"{p['number']}. {p['name']}", callback_data=f"d:{uid}:{chat_id}")]
            for uid, p in g["players"].items() if p["alive"]]
    btns.append([InlineKeyboardButton(text="💤 Пропуск", callback_data=f"d:skip:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def sheriff_kb(g, uid_self, chat_id):
    btns = []
    for uid, p in g["players"].items():
        if p["alive"] and uid != uid_self:
            btns.append([InlineKeyboardButton(text=f"🔍 Перевірити {p['number']}. {p['name']}", callback_data=f"chk:{uid}:{chat_id}")])
            btns.append([InlineKeyboardButton(text=f"🔫 Вистрілити {p['number']}. {p['name']}", callback_data=f"sht:{uid}:{chat_id}")])
    btns.append([InlineKeyboardButton(text="💤 Нічого не робити", callback_data=f"sht:skip:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def vote_kb(g, chat_id, candidates=None):
    allowed = candidates if candidates is not None else alive_ids(g)
    btns = [[InlineKeyboardButton(text=f"👉 {p['number']}. {p['name']}", callback_data=f"v:{uid}:{chat_id}")]
            for uid, p in g["players"].items() if uid in allowed and p["alive"]]
    btns.append([InlineKeyboardButton(text="💤 Пропуск / Утриматись", callback_data=f"v:skip:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

# --- СТАРТ ТА СКАСУВАННЯ ---
@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.text.regexp(r"^/mafia($|@)"))
async def cmd_start_game(message: Message):
    chat_id = message.chat.id
    await reset_game(chat_id)
    g = get_game(chat_id)
    g["status"] = "lobby"
    await set_chat_locked(chat_id, False)

    logging.info(f"Створено нове лобі гри в чаті {chat_id}")
    await message.answer(
        "🎴 НОВА ГРА В МАФІЮ\n\n"
        "Натискайте «Увійти в гру» (мінімум 4 гравці).\n"
        "*(Обов'язково напишіть боту в ЛС хоча б одне повідомлення або /start, інакше він не зможе надіслати ролі та голосування!)*", 
        reply_markup=lobby_kb()
    )

@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.text.regexp(r"^/cancel($|@)"))
async def cmd_cancel(message: Message):
    chat_id = message.chat.id
    await reset_game(chat_id)
    await set_chat_locked(chat_id, False)
    await message.answer("❌ Гру примусово скасовано. Чат розблоковано.")

@dp.callback_query(F.data == "join")
async def cb_join(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    g = get_game(chat_id)
    if g["status"] != "lobby":
        return await callback.answer("Зараз немає набору в гру.", show_alert=True)
    uid = callback.from_user.id
    if uid in g["players"]:
        return await callback.answer("Ти вже у грі!", show_alert=True)

    g["players"][uid] = {"name": callback.from_user.first_name or "Гравець", "role": "civilian", "alive": True, "number": 0}
    names = "".join(f"• {p['name']}\n" for p in g["players"].values())
    
    try:
        await callback.message.edit_text(f"🎴 ЗБІР ГРАВЦІВ\n\nУчасники ({len(g['players'])}):\n{names}", reply_markup=lobby_kb())
    except Exception:
        pass  
        
    await callback.answer("Ти у грі!")

@dp.callback_query(F.data == "start")
async def cb_launch(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    g = get_game(chat_id)
    if g["status"] != "lobby":
        return await callback.answer("Гра вже йде.", show_alert=True)
    if len(g["players"]) < 4:
        return await callback.answer("Треба мінімум 4 гравці!", show_alert=True)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    ids = list(g["players"].keys())
    random.shuffle(ids)
    for i, uid in enumerate(ids, 1):
        g["players"][uid]["number"] = i

    total = len(ids)
    idx = 0
    mafia_count = 1 if total <= 6 else 2

    for _ in range(mafia_count):
        if idx < total:
            g["players"][ids[idx]]["role"] = "mafia"
            idx += 1

    if idx < total:
        g["players"][ids[idx]]["role"] = "doctor"
        idx += 1

    if idx < total:
        g["players"][ids[idx]]["role"] = "sheriff"
        idx += 1

    if total >= 7 and idx < total:
        g["players"][ids[idx]]["role"] = "lucky"
        idx += 1

    for uid in ids[idx:]:
        g["players"][uid]["role"] = "civilian"

    try:
        await callback.message.edit_text("🌙 Місто засинає...")
    except Exception:
        pass
    await start_night(g)

# --- НІЧНА ФАЗА ---
async def start_night(g):
    cancel_timer(g)
    g["status"] = "night"
    g["mafia_votes"].clear()
    g["doctor_target"] = None
    g["sheriff_action"] = None
    chat_id = g["chat_id"]
    await set_chat_locked(chat_id, True)

    await bot.send_message(chat_id, "🌙 НІЧ\n\n🔒 Чат закритий. Ролі роздано в ЛС (на хід 30 секунд).")

    for uid, p in g["players"].items():
        if not p["alive"]: 
            continue
        try:
            if p["role"] == "mafia":
                mafia_team = "\n".join(f"• {mp['name']}" for mu, mp in g["players"].items() if mp["role"] == "mafia")
                await bot.send_message(uid, f"🔪 **МАФІЯ**\nКоманда:\n{mafia_team}\n\nОбери жертву:\n\n{alive_text(g)}", reply_markup=mafia_kb(g, chat_id))
            elif p["role"] == "doctor":
                await bot.send_message(uid, f"🩺 **ЛІКАР**\nОбери кого лікувати:\n\n{alive_text(g)}", reply_markup=doctor_kb(g, chat_id))
            elif p["role"] == "sheriff":
                await bot.send_message(uid, f"🕵️ **ШЕРИФ**\nОбери дію:\n\n{alive_text(g)}", reply_markup=sheriff_kb(g, uid, chat_id))
            elif p["role"] == "lucky":
                await bot.send_message(uid, f"🍀 **ЩАСЛИВЧИК**\nТи володієш пасивною вдачею (маєш 50% шанс уникнути смерті вночі).\n\n{alive_text(g)}")
            else:
                await bot.send_message(uid, "😇 **МИРНИЙ ЖИТЕЛЬ**\nСпи спокійно, місто засинає...")
        except Exception as e:
            logging.error(f"Помилка відправки ролі гравцю {uid}: {e}")

    # Жорсткий таймер на 30 секунд — навіть якщо хтось завис, ніч завершиться автоматично
    start_timer(g, 30, resolve_night)

async def check_night_ready(g):
    if g["status"] != "night":
        return
    
    alive_m = [u for u, p in g["players"].items() if p["alive"] and p["role"] == "mafia"]
    m_done = all(u in g["mafia_votes"] for u in alive_m)
    
    doc_alive = any(p["alive"] and p["role"] == "doctor" for p in g["players"].values())
    d_done = (not doc_alive) or (g["doctor_target"] is not None)

    sher_alive = any(p["alive"] and p["role"] == "sheriff" for p in g["players"].values())
    s_done = (not sher_alive) or (g["sheriff_action"] is not None)

    if m_done and d_done and s_done:
        cancel_timer(g)
        await resolve_night(g)

@dp.callback_query(F.data.startswith("m:"))
async def cb_mafia(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 3: return
    val, chat_id_str = parts[1], parts[2]
    chat_id = int(chat_id_str)
    if chat_id not in games: return
    g = games[chat_id]
    if g["status"] != "night": 
        return await callback.answer("Зараз не ніч.", show_alert=True)
    
    uid = callback.from_user.id
    if uid not in g["players"] or g["players"][uid]["role"] != "mafia": return
    
    g["mafia_votes"][uid] = int(val) if val != "skip" else "skip"
    try: await callback.message.edit_text("🔪 Вибір мафії збережено.", reply_markup=None)
    except Exception: pass
    
    await callback.answer("Збережено!")
    await check_night_ready(g)

@dp.callback_query(F.data.startswith("d:"))
async def cb_doctor(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 3: return
    val, chat_id_str = parts[1], parts[2]
    chat_id = int(chat_id_str)
    if chat_id not in games: return
    g = games[chat_id]
    if g["status"] != "night": 
        return await callback.answer("Зараз не ніч.", show_alert=True)
        
    uid = callback.from_user.id
    if uid not in g["players"] or g["players"][uid]["role"] != "doctor": return

    g["doctor_target"] = int(val) if val != "skip" else "skip"
    try: await callback.message.edit_text("🩺 Вибір лікаря збережено.", reply_markup=None)
    except Exception: pass
    
    await callback.answer("Збережено!")
    await check_night_ready(g)

@dp.callback_query(F.data.startswith(("chk:", "sht:")))
async def cb_sheriff(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 3: return
    prefix, val, chat_id_str = parts[0], parts[1], parts[2]
    chat_id = int(chat_id_str)
    if chat_id not in games: return
    g = games[chat_id]
    if g["status"] != "night": 
        return await callback.answer("Зараз не ніч.", show_alert=True)
        
    uid = callback.from_user.id
    if uid not in g["players"] or g["players"][uid]["role"] != "sheriff": return

    if val == "skip":
        g["sheriff_action"] = ("skip", None)
    else:
        target = int(val)
        if prefix == "chk":
            res = "🔪 МАФІЯ" if g["players"][target]["role"] == "mafia" else "😇 НЕ МАФІЯ"
            g["sheriff_action"] = ("check", target)
            try: await callback.message.edit_text(f"🔍 Перевірка: №{g['players'][target]['number']} — {res}", reply_markup=None)
            except Exception: pass
        else:
            g["sheriff_action"] = ("shot", target)
            try: await callback.message.edit_text("🔫 Постріл збережено.", reply_markup=None)
            except Exception: pass
            
    await callback.answer("Збережено!")
    await check_night_ready(g)

async def resolve_night(g):
    if g["status"] != "night": return
    cancel_timer(g)

    counts = {}
    for uid in [u for u, p in g["players"].items() if p["alive"] and p["role"] == "mafia"]:
        t = g["mafia_votes"].get(uid)
        if isinstance(t, int): counts[t] = counts.get(t, 0) + 1

    m_target = max(counts, key=counts.get) if counts else None
    d_target = g["doctor_target"]
    lucky_uid = next((u for u, p in g["players"].items() if p["role"] == "lucky"), None)
    
    text = "🌅 **РАНОК У МІСТІ**\n\n"
    if m_target is None:
        text += "🔪 Ніхто не постраждав вночі.\n"
    elif m_target == d_target:
        text += f"🩺 Лікар врятував **{g['players'][m_target]['name']}**!\n"
    elif lucky_uid and m_target == lucky_uid and random.random() < 0.5:
        text += f"🍀 Щасливчик врятувався від кулі!\n"
    else:
        g["players"][m_target]["alive"] = False
        text += f"💀 Мафія вбила **{g['players'][m_target]['name']}** ({ROLES[g['players'][m_target]['role']]}).\n"

    text += "\n" + alive_text(g)
    chat_id = g["chat_id"]
    await set_chat_locked(chat_id, False)
    await bot.send_message(chat_id, text)

    if await check_win(g): return

    g["status"] = "day"
    await bot.send_message(
        chat_id, 
        "🗣 **ДЕНЬ**\n💬 Обговорення (60 секунд).",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏩ До голосування", callback_data=f"force_vote:{chat_id}")]])
    )
    start_timer(g, 60, start_voting)

@dp.callback_query(F.data.startswith("force_vote:"))
async def cb_force_vote(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[1])
    if chat_id not in games: return
    g = games[chat_id]
    if g["status"] != "day": return
    cancel_timer(g)
    try: await callback.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    await callback.answer()
    await start_voting(g)

# --- ГОЛОСУВАННЯ В ЛС ---
async def start_voting(g, candidates=None):
    if g["status"] not in {"day", "voting"}: return
    cancel_timer(g)
    g["status"] = "voting"
    g["votes"].clear()
    chat_id = g["chat_id"]
    await set_chat_locked(chat_id, True)

    await bot.send_message(chat_id, "⚖️ **ГОЛОСУВАННЯ РОЗПОЧАТО!**\n🔒 Зазирніть у ЛС до бота — обирайте підозрюваного там (на хід 30 секунд).")

    for uid, p in g["players"].items():
        if not p["alive"]:
            continue
        try:
            await bot.send_message(
                uid,
                f"⚖️ **ГОЛОСУВАННЯ У МІСТІ**\nОберіть гравця для вигнання:\n\n{alive_text(g)}",
                reply_markup=vote_kb(g, chat_id, candidates)
            )
        except Exception as e:
            logging.error(f"Не вдалося надіслати голосування в ЛС гравцю {uid}: {e}")

    start_timer(g, 30, resolve_voting)

@dp.callback_query(F.data.startswith("v:"))
async def cb_vote(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) < 3: return
    val, chat_id_str = parts[1], parts[2]
    chat_id = int(chat_id_str)
    
    if chat_id not in games:
        return await callback.answer("Гра вже закінчилась.", show_alert=True)
        
    g = games[chat_id]
    if g["status"] != "voting":
        return await callback.answer("Зараз немає активного голосування.", show_alert=True)
        
    uid = callback.from_user.id
    if uid not in g["players"] or not g["players"][uid]["alive"]:
        return await callback.answer("Ви не живий гравець цієї партії.", show_alert=True)
        
    if uid in g["votes"]:
        return await callback.answer("Ти вже проголосував!", show_alert=True)

    if val == "skip":
        g["votes"][uid] = "skip"
        try: await callback.message.edit_text("💤 Ваш голос: Пропуск.", reply_markup=None)
        except Exception: pass
    else:
        target = int(val)
        if target not in g["players"] or not g["players"][target]["alive"]:
            return await callback.answer("Цей гравець вже мертвий.", show_alert=True)
        g["votes"][uid] = target
        try: await callback.message.edit_text(f"✅ Ваш голос за: {g['players'][target]['number']}. {g['players'][target]['name']}", reply_markup=None)
        except Exception: pass

    await callback.answer("Голос прийнято!")

    if set(g["votes"].keys()) >= alive_ids(g):
        cancel_timer(g)
        await resolve_voting(g)

async def resolve_voting(g):
    if g["status"] != "voting": return
    cancel_timer(g)

    counts = {}
    alive = alive_ids(g)
    for v, t in g["votes"].items():
        if v in alive and isinstance(t, int) and t in alive:
            counts[t] = counts.get(t, 0) + 1

    chat_id = g["chat_id"]
    if not counts:
        await finish_voting(g, "⚖️ Ніхто не проголосував проти живих гравців.")
        return

    max_v = max(counts.values())
    cands = [u for u, c in counts.items() if c == max_v]

    if len(cands) > 1:
        if g["runoff"]:
            g["runoff"] = None
            await finish_voting(g, "⚖️ Нічия в перестрілці. Нікого не вигнано.")
        else:
            g["runoff"] = cands
            names_list = ", ".join([f"{g['players'][c]['number']}. {g['players'][c]['name']}" for c in cands])
            await bot.send_message(chat_id, f"⚖️ **НІЧИЯ!** Між кандидатами: {names_list}. Перестрілка!")
            await start_voting(g, cands)
        return

    exiled = cands[0]
    g["players"][exiled]["alive"] = False
    g["runoff"] = None
    
    exiled_name = g["players"][exiled]["name"]
    exiled_role = ROLES[g["players"][exiled]["role"]]
    final_text = f"⚖️ Вигнано **{exiled_name}** ({exiled_role})"
    
    await finish_voting(g, final_text)

async def finish_voting(g, text):
    chat_id = g["chat_id"]
    await set_chat_locked(chat_id, False)
    await bot.send_message(chat_id, text + "\n\n" + alive_text(g))
    if await check_win(g): return
    await start_night(g)

async def check_win(g):
    mafia = sum(1 for p in g["players"].values() if p["alive"] and p["role"] == "mafia")
    others = sum(1 for p in g["players"].values() if p["alive"] and p["role"] != "mafia")
    chat_id = g["chat_id"]

    if mafia == 0:
        cancel_timer(g)
        await bot.send_message(chat_id, "🎉 **ПЕРЕМОГА МИРНИХ!**\n\n" + role_summary(g))
        await set_chat_locked(chat_id, False)
        await reset_game(chat_id)
        return True
    if mafia >= others:
        cancel_timer(g)
        await bot.send_message(chat_id, "🔪 **ПЕРЕМОГА МАФІЇ!**\n\n" + role_summary(g))
        await set_chat_locked(chat_id, False)
        await reset_game(chat_id)
        return True
    return False

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Бот успішно запущено!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
