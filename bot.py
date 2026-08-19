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

# Словник для підтримки багатьох чатів одночасно: chat_id -> game_state
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
        f"• {p['number']}. {p['name']} — {ROLES[p['role']]} ({'🟢 вижив' if p['alive'] else '💀 мертвий'})\n"
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

def mafia_kb(g):
    btns = [[InlineKeyboardButton(text=f"{p['number']}. {p['name']}", callback_data=f"m:{uid}")]
            for uid, p in g["players"].items() if p["alive"] and p["role"] != "mafia"]
    btns.append([InlineKeyboardButton(text="💤 Пропуск", callback_data="m:skip")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def doctor_kb(g):
    btns = [[InlineKeyboardButton(text=f"{p['number']}. {p['name']}", callback_data=f"d:{uid}")]
            for uid, p in g["players"].items() if p["alive"]]
    btns.append([InlineKeyboardButton(text="💤 Пропуск", callback_data=f"d:skip")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def sheriff_kb(g, uid_self):
    btns = []
    for uid, p in g["players"].items():
        if p["alive"] and uid != uid_self:
            btns.append([InlineKeyboardButton(text=f"🔍 Перевірити {p['number']}. {p['name']}", callback_data=f"chk:{uid}")])
            btns.append([InlineKeyboardButton(text=f"🔫 Вистрілити {p['number']}. {p['name']}", callback_data=f"sht:{uid}")])
    btns.append([InlineKeyboardButton(text="💤 Нічого не робити", callback_data="sht:skip")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def vote_kb(g, candidates=None):
    allowed = candidates if candidates is not None else alive_ids(g)
    btns = [[InlineKeyboardButton(text=f"👉 {p['number']}. {p['name']}", callback_data=f"v:{uid}")]
            for uid, p in g["players"].items() if uid in allowed and p["alive"]]
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
        "*(Напиши боту в ЛС хоча б одне повідомлення, щоб він міг надсилати ролі!)*", 
        reply_markup=lobby_kb()
    )

@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.text.regexp(r"^/cancel($|@)"))
async def cmd_cancel(message: Message):
    chat_id = message.chat.id
    await reset_game(chat_id)
    await set_chat_locked(chat_id, False)
    await message.answer("❌ Гру примусово скасовано. Чат розблоковано.")

# --- ЛОБІ ТА РОЗДАЧА РОЛЕЙ ---
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

    logging.info(f"Гра {chat_id} стартувала. Гравців: {total}, мафії: {mafia_count}")

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

    await bot.send_message(chat_id, "🌙 НІЧ\n\n🔒 Чат закритий. Ролі роздано в ЛС (на хід 35 секунд).")

    for uid, p in g["players"].items():
        if not p["alive"]: 
            continue
        try:
            if p["role"] == "mafia":
                mafia_team = "\n".join(f"• {mp['name']}" for mu, mp in g["players"].items() if mp["role"] == "mafia")
                await bot.send_message(uid, f"🔪 **МАФІЯ**\nКоманда:\n{mafia_team}\n\nОбери жертву:\n\n{alive_text(g)}", reply_markup=mafia_kb(g))
            elif p["role"] == "doctor":
                await bot.send_message(uid, f"🩺 **ЛІКАР**\nОбери кого лікувати:\n\n{alive_text(g)}", reply_markup=doctor_kb(g))
            elif p["role"] == "sheriff":
                await bot.send_message(uid, f"🕵️ **ШЕРИФ**\nОбери дію:\n\n{alive_text(g)}", reply_markup=sheriff_kb(g, uid))
            elif p["role"] == "lucky":
                await bot.send_message(uid, f"🍀 **ЩАСЛИВЧИК**\nТи володієш пасивною вдачею (маєш 50% шанс уникнути смерті вночі).\n\n{alive_text(g)}")
            else:
                await bot.send_message(uid, "😇 **МИРНИЙ ЖИТЕЛЬ**\nСпи спокійно, місто засинає...")
        except Exception as e:
            logging.error(f"Помилка відправки ролі гравцю {uid}: {e}")
            await bot.send_message(chat_id, f"⚠️ {p['name']} не отримав роль. Відкрий бота в ЛС та напиши /start")

    start_timer(g, 35, resolve_night)

async def check_night_ready(g):
    alive_m = [u for u, p in g["players"].items() if p["alive"] and p["role"] == "mafia"]
    m_done = all(u in g["mafia_votes"] for u in alive_m)
    d_done = (not any(p["alive"] and p["role"] == "doctor" for p in g["players"].values())) or (g["doctor_target"] is not None)
    s_done = (not any(p["alive"] and p["role"] == "sheriff" for p in g["players"].values())) or (g["sheriff_action"] is not None)

    if m_done and d_done and s_done:
        cancel_timer(g)
        await resolve_night(g)

@dp.callback_query(F.data.startswith("m:"))
async def cb_mafia(callback: CallbackQuery):
    for chat_id, g in games.items():
        if g["status"] == "night" and callback.from_user.id in g["players"] and g["players"][callback.from_user.id]["role"] == "mafia":
            uid = callback.from_user.id
            if uid in g["mafia_votes"]:
                return await callback.answer("Ти вже вибрав жертву!", show_alert=True)
            
            val = callback.data.split(":")[1]
            if val != "skip":
                target = int(val)
                if target not in g["players"] or not g["players"][target]["alive"]:
                    return await callback.answer("Гравець вже мертвий.", show_alert=True)
                if g["players"][target]["role"] == "mafia":
                    return await callback.answer("Не можна вбити свого!", show_alert=True)
                g["mafia_votes"][uid] = target
            else:
                g["mafia_votes"][uid] = "skip"
            
            try: await callback.message.edit_text("🔪 Вибір мафії збережено.", reply_markup=None)
            except Exception: pass
            
            # Повідомити іншу мафію про вибір
            for mu, mp in g["players"].items():
                if mu != uid and mp["role"] == "mafia" and mp["alive"]:
                    try:
                        await bot.send_message(mu, f"💬 Напарник {g['players'][uid]['name']} зробив свій вибір.")
                    except Exception:
                        pass

            await callback.answer("Збережено!")
            await check_night_ready(g)
            return
    await callback.answer("Зараз не ніч або ти не мафія.", show_alert=True)

@dp.callback_query(F.data.startswith("d:"))
async def cb_doctor(callback: CallbackQuery):
    for chat_id, g in games.items():
        if g["status"] == "night" and callback.from_user.id in g["players"] and g["players"][callback.from_user.id]["role"] == "doctor":
            if g["doctor_target"] is not None:
                return await callback.answer("Ти вже зробив вибір!", show_alert=True)

            val = callback.data.split(":")[1]
            if val != "skip":
                target = int(val)
                if target not in g["players"] or not g["players"][target]["alive"]:
                    return await callback.answer("Гравець вже мертвий.", show_alert=True)
                g["doctor_target"] = target
            else:
                g["doctor_target"] = "skip"
            
            try: await callback.message.edit_text("🩺 Вибір лікаря збережено.", reply_markup=None)
            except Exception: pass
            
            await callback.answer("Збережено!")
            await check_night_ready(g)
            return
    await callback.answer("Зараз не ніч або ти не лікар.", show_alert=True)

@dp.callback_query(F.data.startswith(("chk:", "sht:")))
async def cb_sheriff(callback: CallbackQuery):
    for chat_id, g in games.items():
        if g["status"] == "night" and callback.from_user.id in g["players"] and g["players"][callback.from_user.id]["role"] == "sheriff":
            if g["sheriff_action"]:
                return await callback.answer("Ти вже зробив дію!", show_alert=True)

            prefix, val = callback.data.split(":")
            if val == "skip":
                g["sheriff_action"] = ("skip", None)
                try: await callback.message.edit_text("💤 Шериф нічого не робив.", reply_markup=None)
                except Exception: pass
            else:
                target = int(val)
                if target not in g["players"] or not g["players"][target]["alive"]:
                    return await callback.answer("Гравець вже мертвий.", show_alert=True)
                    
                if prefix == "chk":
                    res = "🔪 МАФІЯ" if g["players"][target]["role"] == "mafia" else "😇 НЕ МАФІЯ"
                    g["sheriff_action"] = ("check", target)
                    try: await callback.message.edit_text(f"🔍 Перевірка завершена.\n\nРезультат: Гравець №{g['players'][target]['number']} — {res}", reply_markup=None)
                    except Exception: pass
                else:
                    g["sheriff_action"] = ("shot", target)
                    try: await callback.message.edit_text("🔫 Постріл збережено.", reply_markup=None)
                    except Exception: pass
                    
            await callback.answer("Збережено!")
            await check_night_ready(g)
            return
    await callback.answer("Зараз не ніч або ти не шериф.", show_alert=True)

async def resolve_night(g):
    if g["status"] != "night": 
        return
    cancel_timer(g)

    counts = {}
    for uid in [u for u, p in g["players"].items() if p["alive"] and p["role"] == "mafia"]:
        t = g["mafia_votes"].get(uid)
        if isinstance(t, int): 
            counts[t] = counts.get(t, 0) + 1

    m_target = None
    if counts:
        max_votes = max(counts.values())
        top_targets = [t for t, c in counts.items() if c == max_votes]
        if len(top_targets) == 1:
            m_target = top_targets[0]

    d_target = g["doctor_target"]
    lucky_uid = next((u for u, p in g["players"].items() if p["role"] == "lucky"), None)
    
    text = "🌅 **РАНОК У МІСТІ**\n\n"

    if m_target is None:
        text += "🔪 Мафія не змогла домовитися або нікого не вбила.\n"
    elif m_target == d_target:
        text += f"🩺 Лікар врятував **{g['players'][m_target]['name']}**!\n"
    elif lucky_uid and m_target == lucky_uid and random.random() < 0.5:
        text += f"🍀 Щасливчик **{g['players'][lucky_uid]['name']}** дивом уникнув смертельної кулі мафії!\n"
    else:
        p = g["players"][m_target]
        p["alive"] = False
        text += f"💀 Мафія вбила **{p['name']}** ({ROLES[p['role']]}).\n"

    s_act = g["sheriff_action"]
    if s_act and s_act[0] == "shot":
        st = s_act[1]
        if st in g["players"] and g["players"][st]["alive"]:
            sp = g["players"][st]
            if st == d_target:
                text += f"🩺 Лікар також врятував **{sp['name']}** від шерифа!\n"
            else:
                sp["alive"] = False
                text += f"🔫 Шериф вбив **{sp['name']}** ({ROLES[sp['role']]}).\n"

    text += "\n" + alive_text(g)
    chat_id = g["chat_id"]
    await set_chat_locked(chat_id, False)
    await bot.send_message(chat_id, text)

    if await check_win(g): 
        return

    g["status"] = "day"
    await bot.send_message(
        chat_id, 
        "🗣 **ДЕНЬ**\n💬 Обговорення (60 секунд).",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏩ До голосування", callback_data="force_vote")]])
    )
    start_timer(g, 60, start_voting)

@dp.callback_query(F.data == "force_vote")
async def cb_force_vote(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    g = get_game(chat_id)
    if g["status"] != "day": 
        return await callback.answer("Зараз не день.", show_alert=True)
    cancel_timer(g)
    try: await callback.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    await callback.answer()
    await start_voting(g)

# --- ГОЛОСУВАННЯ ---
async def start_voting(g, candidates=None):
    if g["status"] not in {"day", "voting"}: 
        return
    cancel_timer(g)
    g["status"] = "voting"
    g["votes"].clear()
    chat_id = g["chat_id"]
    await set_chat_locked(chat_id, True)
    await bot.send_message(chat_id, "⚖️ **ГОЛОСУВАННЯ**\n🔒 Обирайте підозрюваного (на хід 30 секунд):", reply_markup=vote_kb(g, candidates))
    start_timer(g, 30, resolve_voting)

@dp.callback_query(F.data.startswith("v:"))
async def cb_vote(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    g = get_game(chat_id)
    if g["status"] != "voting": 
        return await callback.answer("Зараз не голосування.", show_alert=True)
    uid = callback.from_user.id
    if uid in g["votes"]:
        return await callback.answer("Ти вже проголосував!", show_alert=True)
    if uid not in g["players"] or not g["players"][uid]["alive"]:
        return await callback.answer("Мертві не голосують.", show_alert=True)
    
    target = int(callback.data.split(":")[1])
    if target not in g["players"] or not g["players"][target]["alive"]:
        return await callback.answer("Цей гравець вже мертвий.", show_alert=True)

    g["votes"][uid] = target
    try: await callback.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    await callback.answer("Голос прийнято!")

    if set(g["votes"].keys()) >= alive_ids(g):
        cancel_timer(g)
        await resolve_voting(g)

async def resolve_voting(g):
    if g["status"] != "voting": 
        return
    cancel_timer(g)

    counts = {}
    alive = alive_ids(g)
    for v, t in g["votes"].items():
        if v in alive and t in alive:
            counts[t] = counts.get(t, 0) + 1

    chat_id = g["chat_id"]
    if not counts:
        await finish_voting(g, "⚖️ Ніхто не проголосував.")
        return

    max_v = max(counts.values())
    cands = [u for u, c in counts.items() if c == max_v]

    if len(cands) > 1:
        if g["runoff"]:
            g["runoff"] = None
            await finish_voting(g, "⚖️ Нічия в перестрілці. Нікого не вигнано.")
        else:
            g["runoff"] = cands
            names_str = ", ".join([f"{g['players'][c]['number']}. {g['players'][c]['name']}" for c in cands])
            await bot.send_message(chat_id, f"⚖️ **НІЧИЯ!** Між: {names_str}. Перестрілка!")
            await start_voting(g, cands)
        return

    exiled = cands[0]
    g["players"][exiled]["alive"] = False
    g["runoff"] = None
    await finish_voting(g, f"⚖️ Вигнано **{g['players'][exiled]['name']}**.\nЙого роль: **{ROLES[g['players'][exiled]['role']]}**")

async def finish_voting(g, text):
    chat_id = g["chat_id"]
    await set_chat_locked(chat_id, False)
    await bot.send_message(chat_id, text + "\n\n" + alive_text(g))
    if await check_win(g): 
        return
    await start_night(g)

async def check_win(g):
    mafia = sum(1 for p in g["players"].values() if p["alive"] and p["role"] == "mafia")
    others = sum(1 for p in g["players"].values() if p["alive"] and p["role"] != "mafia")
    chat_id = g["chat_id"]

    if mafia == 0:
        cancel_timer(g)
        g["status"] = "finished"
        summary = role_summary(g)
        
        await bot.send_message(chat_id, "🎉 **ПЕРЕМОГА МИРНИХ!** Всю мафію знищено!\n\n" + summary)
        await set_chat_locked(chat_id, False)
        await reset_game(chat_id)
        return True
        
    if mafia >= others:
        cancel_timer(g)
        g["status"] = "finished"
        summary = role_summary(g)
        
        await bot.send_message(chat_id, "🔪 **ПЕРЕМОГА МАФІЇ!** Вони захопили місто!\n\n" + summary)
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
