import asyncio
import os
import random
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

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

# =========================
# RENDER WEB SERVER
# =========================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive")

    def log_message(self, format, *args):
        pass


def run_web():
    port = int(os.getenv("PORT", "10000"))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


threading.Thread(target=run_web, daemon=True).start()

# =========================
# BOT
# =========================

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN не знайдено в Environment Variables")

bot = Bot(
    TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
)
dp = Dispatcher()

# Одна гра = один чат
game = {
    "status": "idle",
    "chat_id": None,
    "players": {},
    "timer": None,

    "mafia_votes": {},
    "doctor_target": None,
    "doctor_self_used": False,

    "sheriff_action": None,   # ("check", uid), ("shot", uid), ("skip", None)

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
# HELPERS
# =========================

def cancel_timer():
    task = game["timer"]
    if task:
        task.cancel()
    game["timer"] = None


def alive_players():
    return [
        (uid, p)
        for uid, p in game["players"].items()
        if p["alive"]
    ]


def alive_ids():
    return {uid for uid, p in game["players"].items() if p["alive"]}


def alive_text():
    players = sorted(
        [p for p in game["players"].values() if p["alive"]],
        key=lambda p: p["number"]
    )

    text = f"📋 Живі гравці ({len(players)}):\n"
    for p in players:
        text += f"• {p['number']}. {p['name']}\n"
    return text


def role_summary():
    text = "📜 Ролі у грі:\n\n"
    for p in sorted(game["players"].values(), key=lambda x: x["number"]):
        status = "🟢 вижив" if p["alive"] else "💀 мертвий"
        text += f"• {p['number']}. {p['name']} — {ROLES[p['role']]} ({status})\n"
    return text


def mafia_names():
    return "\n".join(
        f"• {p['name']}"
        for p in game["players"].values()
        if p["role"] == "mafia"
    )


async def set_chat_locked(locked: bool):
    if not game["chat_id"]:
        return

    try:
        if locked:
            await bot.set_chat_permissions(
                game["chat_id"],
                ChatPermissions(can_send_messages=False)
            )
        else:
            await bot.set_chat_permissions(
                game["chat_id"],
                ChatPermissions(can_send_messages=True)
            )
    except Exception as e:
        print("Помилка блокування чату:", e)


# =========================
# KEYBOARDS
# =========================

def lobby_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🎮 Увійти в гру",
                callback_data="join"
            )
        ],
        [
            InlineKeyboardButton(
                text="🚀 Почати гру",
                callback_data="start"
            )
        ]
    ])


def mafia_keyboard():
    buttons = []

    for uid, p in game["players"].items():
        if p["alive"] and p["role"] != "mafia":
            buttons.append([
                InlineKeyboardButton(
                    text=f"{p['number']}. {p['name']}",
                    callback_data=f"mafia:{uid}"
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            text="💤 Пропуск",
            callback_data="mafia:skip"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def doctor_keyboard(user_id):
    buttons = []

    for uid, p in game["players"].items():
        if p["alive"]:
            buttons.append([
                InlineKeyboardButton(
                    text=f"{p['number']}. {p['name']}",
                    callback_data=f"doctor:{uid}"
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            text="💤 Пропуск",
            callback_data="doctor:skip"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def sheriff_keyboard(user_id):
    buttons = []

    for uid, p in game["players"].items():
        if p["alive"] and uid != user_id:
            buttons.append([
                InlineKeyboardButton(
                    text=f"🔍 Перевірити {p['number']}. {p['name']}",
                    callback_data=f"check:{uid}"
                )
            ])

    for uid, p in game["players"].items():
        if p["alive"] and uid != user_id:
            buttons.append([
                InlineKeyboardButton(
                    text=f"🔫 Вистрілити {p['number']}. {p['name']}",
                    callback_data=f"shot:{uid}"
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            text="💤 Нічого не робити",
            callback_data="sheriff:skip"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def vote_keyboard(candidates=None):
    allowed = candidates if candidates is not None else alive_ids()

    buttons = []
    for uid, p in game["players"].items():
        if uid in allowed and p["alive"]:
            buttons.append([
                InlineKeyboardButton(
                    text=f"👉 {p['number']}. {p['name']}",
                    callback_data=f"vote:{uid}"
                )
            ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# =========================
# PRIVATE START
# =========================

@dp.message(F.chat.type == ChatType.PRIVATE, F.text == "/start")
async def private_start(message: Message):
    await message.answer(
        "👋 Привіт!\n\n"
        "Ти активував бота для особистих повідомлень.\n"
        "Тепер під час гри я зможу надіслати тобі роль та нічні дії."
    )


# =========================
# GROUP /start
# =========================

@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.text.startswith("/start"))
async def group_start(message: Message):
    await create_game(message)


@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.text.startswith("/mafia"))
async def group_mafia(message: Message):
    await create_game(message)


async def create_game(message: Message):
    if game["status"] in {"night", "day", "voting"}:
        await message.answer("⚠️ Гра вже триває.")
        return

    game["status"] = "lobby"
    game["chat_id"] = message.chat.id
    game["players"].clear()
    game["mafia_votes"].clear()
    game["doctor_target"] = None
    game["doctor_self_used"] = False
    game["sheriff_action"] = None
    game["votes"].clear()
    game["runoff"] = None
    cancel_timer()

    await set_chat_locked(False)

    await message.answer(
        "🎴 НОВА ГРА В МАФІЮ\n\n"
        "Натискайте «Увійти в гру».\n"
        "Мінімум — 4 гравці.\n\n"
        "🚀 Почати гру може будь-хто.",
        reply_markup=lobby_keyboard()
    )


@dp.message(
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
    F.text == "/cancel"
)
async def cancel_game(message: Message):
    if game["chat_id"] != message.chat.id:
        return

    cancel_timer()
    game["status"] = "idle"
    game["players"].clear()
    game["chat_id"] = None

    await set_chat_locked(False)
    await message.answer("❌ Гру скасовано. Чат відкритий.")


# =========================
# JOIN
# =========================

@dp.callback_query(F.data == "join")
async def join_game(callback: CallbackQuery):
    if game["status"] != "lobby":
        await callback.answer("Зараз немає набору.", show_alert=True)
        return

    uid = callback.from_user.id

    if uid in game["players"]:
        await callback.answer("Ти вже у грі!", show_alert=True)
        return

    game["players"][uid] = {
        "name": callback.from_user.first_name or "Гравець",
        "role": "civilian",
        "alive": True,
        "number": 0,
    }

    names = "\n".join(
        f"• {p['name']}" for p in game["players"].values()
    )

    try:
        await callback.message.edit_text(
            f"🎴 ЗБІР ГРАВЦІВ\n\n"
            f"Учасники ({len(game['players'])}):\n{names}\n\n"
            "Мінімум — 4.",
            reply_markup=lobby_keyboard()
        )
    except Exception:
        pass

    await callback.answer("Ти у грі!")


# =========================
# START GAME
# =========================

@dp.callback_query(F.data == "start")
async def start_game(callback: CallbackQuery):
    if game["status"] != "lobby":
        await callback.answer("Гра вже запущена.", show_alert=True)
        return

    if len(game["players"]) < 4:
        await callback.answer(
            "Потрібно мінімум 4 гравці!",
            show_alert=True
        )
        return

    await callback.answer("Гра починається!")

    ids = list(game["players"].keys())
    random.shuffle(ids)

    for number, uid in enumerate(ids, 1):
        game["players"][uid]["number"] = number

    total = len(ids)

    if total <= 5:
        mafia_count = 1
        lucky = True
    elif total <= 8:
        mafia_count = 2
        lucky = True
    elif total <= 11:
        mafia_count = 3
        lucky = True
    else:
        mafia_count = 3
        lucky = False

    # Ролі
    index = 0

    for _ in range(mafia_count):
        game["players"][ids[index]]["role"] = "mafia"
        index += 1

    game["players"][ids[index]]["role"] = "doctor"
    index += 1

    game["players"][ids[index]]["role"] = "sheriff"
    index += 1

    if lucky and index < total:
        game["players"][ids[index]]["role"] = "lucky"
        index += 1

    for uid in ids[index:]:
        game["players"][uid]["role"] = "civilian"

    try:
        await callback.message.edit_text("🌙 Місто засинає...")
    except Exception:
        pass

    await start_night(first=True)


# =========================
# NIGHT
# =========================

async def start_night(first=False):
    cancel_timer()

    game["status"] = "night"
    game["mafia_votes"].clear()
    game["doctor_target"] = None
    game["sheriff_action"] = None

    await set_chat_locked(True)

    if first:
        await bot.send_message(
            game["chat_id"],
            "🌙 НІЧ\n\n"
            "🔒 Чат закритий.\n"
            "Ролі та нічні дії приходять у ЛС."
        )
    else:
        await bot.send_message(
            game["chat_id"],
            "🌙 Місто засинає.\n🔒 Чат закритий."
        )

    mafia_team = mafia_names()

    for uid, p in game["players"].items():
        if not p["alive"]:
            continue

        try:
            if p["role"] == "mafia":
                await bot.send_message(
                    uid,
                    f"🔪 **Ти МАФІЯ**\n\n"
                    f"Твоя команда:\n{mafia_team}\n\n"
                    f"Обери жертву:\n\n{alive_text()}",
                    reply_markup=mafia_keyboard()
                )

            elif p["role"] == "doctor":
                await bot.send_message(
                    uid,
                    "🩺 **Ти ЛІКАР**\n\n"
                    "Обери кого лікувати.\n"
                    "Себе можна лікувати лише 1 раз за гру.",
                    reply_markup=doctor_keyboard(uid)
                )

            elif p["role"] == "sheriff":
                await bot.send_message(
                    uid,
                    "🕵️ **Ти ШЕРИФ**\n\n"
                    "Цієї ночі можеш перевірити, вистрілити або нічого не робити.\n"
                    "Жодних обмежень на перевірки між ночами.",
                    reply_markup=sheriff_keyboard(uid)
                )

            elif p["role"] == "lucky":
                await bot.send_message(
                    uid,
                    "🍀 **Ти ЩАСЛИВЧИК**\n\n"
                    "Один раз можеш пережити кулю мафії."
                )

            else:
                await bot.send_message(
                    uid,
                    "😇 **Ти МИРНИЙ ЖИТЕЛЬ**\n\n"
                    "Цієї ночі відпочивай."
                )

        except Exception as e:
            print(f"Не вдалося написати {uid}: {e}")
            try:
                await bot.send_message(
                    game["chat_id"],
                    f"⚠️ {p['name']} не отримав ЛС від бота.\n"
                    "Він має відкрити бота та натиснути Start."
                )
            except Exception:
                pass

    game["timer"] = asyncio.create_task(night_timeout())


async def night_timeout():
    await asyncio.sleep(40)

    if game["status"] == "night":
        await resolve_night()


async def night_ready():
    alive_mafia = [
        uid for uid, p in game["players"].items()
        if p["alive"] and p["role"] == "mafia"
    ]

    mafia_done = all(uid in game["mafia_votes"] for uid in alive_mafia)

    doctor_alive = any(
        p["alive"] and p["role"] == "doctor"
        for p in game["players"].values()
    )
    doctor_done = (
        not doctor_alive or
        game["doctor_target"] is not None
    )

    sheriff_alive = any(
        p["alive"] and p["role"] == "sheriff"
        for p in game["players"].values()
    )
    sheriff_done = (
        not sheriff_alive or
        game["sheriff_action"] is not None
    )

    if mafia_done and doctor_done and sheriff_done:
        cancel_timer()
        await resolve_night()


# =========================
# NIGHT ACTIONS
# =========================

@dp.callback_query(F.data.startswith("mafia:"))
async def mafia_action(callback: CallbackQuery):
    if game["status"] != "night":
        await callback.answer("Ніч вже закінчилась.", show_alert=True)
        return

    uid = callback.from_user.id
    p = game["players"].get(uid)

    if not p or not p["alive"] or p["role"] != "mafia":
        await callback.answer("Ця дія недоступна.", show_alert=True)
        return

    value = callback.data.split(":")[1]

    if value == "skip":
        game["mafia_votes"][uid] = "skip"
        await callback.message.edit_text("💤 Мафія пропускає.")
    else:
        target = int(value)

        if target not in game["players"] or not game["players"][target]["alive"]:
            await callback.answer("Гравець вже мертвий.", show_alert=True)
            return

        game["mafia_votes"][uid] = target
        await callback.message.edit_text(
            f"🔪 Ціль обрана: {game['players'][target]['name']}"
        )

    await callback.answer("Збережено")
    await night_ready()


@dp.callback_query(F.data.startswith("doctor:"))
async def doctor_action(callback: CallbackQuery):
    if game["status"] != "night":
        await callback.answer("Ніч вже закінчилась.", show_alert=True)
        return

    uid = callback.from_user.id
    p = game["players"].get(uid)

    if not p or not p["alive"] or p["role"] != "doctor":
        await callback.answer("Ця дія недоступна.", show_alert=True)
        return

    value = callback.data.split(":")[1]

    if value == "skip":
        game["doctor_target"] = "skip"
        await callback.message.edit_text("💤 Лікар нікого не лікує.")
    else:
        target = int(value)

        if target not in game["players"] or not game["players"][target]["alive"]:
            await callback.answer("Гравець вже мертвий.", show_alert=True)
            return

        if target == uid:
            if game["doctor_self_used"]:
                await callback.answer(
                    "❌ Самолікування вже використано.",
                    show_alert=True
                )
                return

            game["doctor_self_used"] = True

        game["doctor_target"] = target

        await callback.message.edit_text(
            f"🩺 Лікування: {game['players'][target]['name']}"
        )

    await callback.answer("Збережено")
    await night_ready()


@dp.callback_query(F.data.startswith("check:"))
async def sheriff_check(callback: CallbackQuery):
    await sheriff_action(callback, "check")


@dp.callback_query(F.data.startswith("shot:"))
async def sheriff_shot(callback: CallbackQuery):
    await sheriff_action(callback, "shot")


@dp.callback_query(F.data == "sheriff:skip")
async def sheriff_skip(callback: CallbackQuery):
    if game["status"] != "night":
        await callback.answer("Ніч вже закінчилась.", show_alert=True)
        return

    uid = callback.from_user.id
    p = game["players"].get(uid)

    if not p or not p["alive"] or p["role"] != "sheriff":
        await callback.answer("Ця дія недоступна.", show_alert=True)
        return

    game["sheriff_action"] = ("skip", None)

    await callback.message.edit_text("💤 Шериф нічого не робить.")
    await callback.answer("Збережено")
    await night_ready()


async def sheriff_action(callback: CallbackQuery, action):
    if game["status"] != "night":
        await callback.answer("Ніч вже закінчилась.", show_alert=True)
        return

    uid = callback.from_user.id
    p = game["players"].get(uid)

    if not p or not p["alive"] or p["role"] != "sheriff":
        await callback.answer("Ця дія недоступна.", show_alert=True)
        return

    target = int(callback.data.split(":")[1])

    if target == uid:
        await callback.answer("Не можна обрати себе.", show_alert=True)
        return

    target_player = game["players"].get(target)

    if not target_player or not target_player["alive"]:
        await callback.answer("Гравець вже мертвий.", show_alert=True)
        return

    if action == "check":
        result = (
            "🔪 МАФІЯ"
            if target_player["role"] == "mafia"
            else "😇 НЕ МАФІЯ"
        )

        game["sheriff_action"] = ("check", target)

        await callback.message.edit_text(
            f"🔍 Перевірка:\n"
            f"{target_player['name']} — {result}"
        )

    else:
        game["sheriff_action"] = ("shot", target)

        await callback.message.edit_text(
            f"🔫 Постріл:\n"
            f"{target_player['name']}"
        )

    await callback.answer("Збережено")
    await night_ready()


# =========================
# RESOLVE NIGHT
# =========================

async def resolve_night():
    if game["status"] != "night":
        return

    cancel_timer()

    mafia_alive = [
        uid for uid, p in game["players"].items()
        if p["alive"] and p["role"] == "mafia"
    ]

    counts = {}

    for uid in mafia_alive:
        target = game["mafia_votes"].get(uid)

        if isinstance(target, int):
            if target in game["players"] and game["players"][target]["alive"]:
                counts[target] = counts.get(target, 0) + 1

    mafia_target = None

    if counts:
        maximum = max(counts.values())
        targets = [
            uid for uid, count in counts.items()
            if count == maximum
        ]
        mafia_target = random.choice(targets)

    doctor_target = game["doctor_target"]

    text = "🌅 **РАНОК**\n\n"

    # Мафія
    if mafia_target is None:
        text += "🔪 Мафія нікого не вбила.\n"
    else:
        victim = game["players"][mafia_target]

        if mafia_target == doctor_target:
            text += f"🩺 Лікар врятував **{victim['name']}**!\n"

        elif victim["role"] == "lucky" and not victim.get("lucky_used", False):
            victim["lucky_used"] = True
            text += (
                f"🍀 **{victim['name']}** дивом пережив "
                "першу кулю мафії!\n"
            )

        else:
            victim["alive"] = False
            text += (
                f"💀 Мафія вбила **{victim['name']}**.\n"
                f"Роль: {ROLES[victim['role']]}\n"
            )

    # Шериф
    action = game["sheriff_action"]

    if action and action[0] == "shot":
        target = action[1]

        if target in game["players"] and game["players"][target]["alive"]:
            victim = game["players"][target]

            # Якщо лікар лікував саме ціль шерифа
            if target == doctor_target:
                text += (
                    f"🩺 Лікар врятував **{victim['name']}** "
                    "від пострілу шерифа!\n"
                )
            else:
                victim["alive"] = False
                text += (
                    f"🔫 Шериф застрелив **{victim['name']}**.\n"
                    f"Роль: {ROLES[victim['role']]}\n"
                )

    text += "\n" + alive_text()

    await set_chat_locked(False)
    await bot.send_message(game["chat_id"], text)

    if await check_win():
        return

    game["status"] = "day"

    await bot.send_message(
        game["chat_id"],
        "🗣 **ДЕНЬ**\n\n"
        "💬 Чат відкритий.\n"
        "У вас 60 секунд на обговорення."
    )

    game["timer"] = asyncio.create_task(day_timer())


# =========================
# DAY
# =========================

async def day_timer():
    await asyncio.sleep(60)

    if game["status"] == "day":
        await start_voting()


async def start_voting(candidates=None):
    if game["status"] not in {"day", "voting"}:
        return

    cancel_timer()

    game["status"] = "voting"
    game["votes"].clear()

    await set_chat_locked(True)

    if candidates:
        names = ", ".join(
            f"{game['players'][uid]['number']}. "
            f"{game['players'][uid]['name']}"
            for uid in candidates
        )

        text = (
            "⚖️ **ПЕРЕСТРІЛКА**\n\n"
            f"Голосуємо між: {names}\n"
            "30 секунд."
        )
    else:
        text = (
            "⚖️ **ГОЛОСУВАННЯ**\n\n"
            "🔒 Чат закритий.\n"
            "Оберіть гравця, якого виганяємо."
        )

    await bot.send_message(
        game["chat_id"],
        text,
        reply_markup=vote_keyboard(candidates)
    )

    game["timer"] = asyncio.create_task(voting_timer())


async def voting_timer():
    await asyncio.sleep(30)

    if game["status"] == "voting":
        await resolve_voting()


@dp.callback_query(F.data.startswith("vote:"))
async def vote(callback: CallbackQuery):
    if game["status"] != "voting":
        await callback.answer("Голосування завершено.", show_alert=True)
        return

    uid = callback.from_user.id

    if uid not in game["players"] or not game["players"][uid]["alive"]:
        await callback.answer("Мертві не голосують.", show_alert=True)
        return

    target = int(callback.data.split(":")[1])

    if target not in game["players"] or not game["players"][target]["alive"]:
        await callback.answer("Гравець вже мертвий.", show_alert=True)
        return

    # Якщо це перестрілка — можна голосувати тільки за кандидатів
    if game["runoff"] and target not in game["runoff"]:
        await callback.answer("Обирай тільки кандидатів.", show_alert=True)
        return

    game["votes"][uid] = target

    try:
        await callback.message.edit_text(
            f"🗳 Голос прийнято за {game['players'][target]['name']}."
        )
    except Exception:
        pass

    await callback.answer("Голос прийнято")

    if set(game["votes"].keys()) >= alive_ids():
        cancel_timer()
        await resolve_voting()


async def resolve_voting():
    if game["status"] != "voting":
        return

    cancel_timer()

    alive = alive_ids()
    counts = {}

    for voter, target in game["votes"].items():
        if voter in alive and target in alive:
            counts[target] = counts.get(target, 0) + 1

    if not counts:
        game["runoff"] = None
        await finish_voting("⚖️ Ніхто не проголосував.")
        return

    maximum = max(counts.values())
    candidates = [
        uid for uid, count in counts.items()
        if count == maximum
    ]

    # Нічия
    if len(candidates) > 1:
        if game["runoff"]:
            game["runoff"] = None
            await finish_voting(
                "⚖️ Знову нічия. Ніхто не вигнаний."
            )
        else:
            game["runoff"] = candidates
            await start_voting(candidates)
        return

    exiled = candidates[0]
    player = game["players"][exiled]
    player["alive"] = False

    game["runoff"] = None

    await finish_voting(
        f"⚖️ Місто вигнало **{player['name']}**.\n"
        f"Роль: {ROLES[player['role']]}"
    )


async def finish_voting(text):
    await set_chat_locked(False)

    await bot.send_message(
        game["chat_id"],
        text + "\n\n" + alive_text()
    )

    if await check_win():
        return

    await start_night()


# =========================
# WIN
# =========================

async def check_win():
    mafia = sum(
        1 for p in game["players"].values()
        if p["alive"] and p["role"] == "mafia"
    )

    others = sum(
        1 for p in game["players"].values()
        if p["alive"] and p["role"] != "mafia"
    )

    if mafia == 0:
        cancel_timer()
        game["status"] = "finished"

        await set_chat_locked(False)

        await bot.send_message(
            game["chat_id"],
            "🎉 **ПЕРЕМОГА МИРНИХ!**\n\n"
            "Мафію знищено!\n\n" +
            role_summary()
        )
        return True

    if mafia >= others:
        cancel_timer()
        game["status"] = "finished"

        await set_chat_locked(False)

        await bot.send_message(
            game["chat_id"],
            "🔪 **ПЕРЕМОГА МАФІЇ!**\n\n"
            "Мафія захопила місто!\n\n" +
            role_summary()
        )
        return True

    return False


# =========================
# ERROR LOG
# =========================

@dp.errors()
async def errors(event):
    print("BOT ERROR:", event.exception)


# =========================
# MAIN
# =========================

async def main():
    print("Бот запускається...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
