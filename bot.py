import asyncio
import os
import random
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, ChatPermissions
)

# =========================
# RENDER
# =========================

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, *args):
        pass


def run_server():
    port = int(os.getenv("PORT", "10000"))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


threading.Thread(target=run_server, daemon=True).start()

# =========================
# BOT
# =========================

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не заданий у Render Environment")

bot = Bot(
    TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
)
dp = Dispatcher()

game = {
    "status": "idle",
    "chat_id": None,
    "players": {},
    "mafia_votes": {},
    "doctor_target": None,
    "sheriff_action": None,
    "votes": {},
    "runoff": None,
    "timer": None,
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

def alive_players():
    return {
        uid: p for uid, p in game["players"].items()
        if p["alive"]
    }


def alive_text():
    players = sorted(
        alive_players().values(),
        key=lambda p: p["number"]
    )

    text = f"📋 **Живі гравці ({len(players)}):**\n"
    for p in players:
        text += f"• {p['number']}. {p['name']}\n"
    return text


def roles_summary():
    text = "📜 **Хто ким був:**\n\n"

    for p in sorted(
        game["players"].values(),
        key=lambda p: p["number"]
    ):
        state = "🟢 вижив" if p["alive"] else "💀 мертвий"
        text += f"• {p['number']}. {p['name']} — {ROLES[p['role']]} ({state})\n"

    return text


def cancel_timer():
    task = game["timer"]

    if task and not task.done():
        task.cancel()

    game["timer"] = None


def reset_night():
    game["mafia_votes"].clear()
    game["doctor_target"] = None
    game["sheriff_action"] = None


def sheriff_id():
    for uid, p in game["players"].items():
        if p["role"] == "sheriff":
            return uid
    return None


async def set_chat_lock(lock: bool):
    if not game["chat_id"]:
        return

    try:
        await bot.set_chat_permissions(
            game["chat_id"],
            ChatPermissions(can_send_messages=not lock)
        )
    except Exception as e:
        print("Помилка прав чату:", e)


async def send_phase(phase, text):
    filename = f"{phase}.jpg"

    if os.path.exists(filename):
        await bot.send_photo(
            game["chat_id"],
            FSInputFile(filename),
            caption=text
        )
    else:
        await bot.send_message(game["chat_id"], text)


# =========================
# KEYBOARDS
# =========================

def join_keyboard():
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
    rows = []

    for uid, p in alive_players().items():
        if p["role"] != "mafia":
            rows.append([
                InlineKeyboardButton(
                    text=f"{p['number']}. {p['name']}",
                    callback_data=f"mafia:{uid}"
                )
            ])

    rows.append([
        InlineKeyboardButton(
            text="💤 Нікого не вбивати",
            callback_data="mafia:skip"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def doctor_keyboard():
    rows = []

    for uid, p in alive_players().items():
        rows.append([
            InlineKeyboardButton(
                text=f"{p['number']}. {p['name']}",
                callback_data=f"doctor:{uid}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="💤 Нікого не лікувати",
            callback_data="doctor:skip"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def sheriff_keyboard():
    rows = []

    for uid, p in alive_players().items():
        rows.append([
            InlineKeyboardButton(
                text=f"🔍 Перевірити {p['number']}. {p['name']}",
                callback_data=f"check:{uid}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="💤 Нічого не робити",
            callback_data="sheriff:skip"
        )
    ])

    sid = sheriff_id()

    for uid, p in alive_players().items():
        if uid != sid:
            rows.append([
                InlineKeyboardButton(
                    text=f"🔫 Вистрілити {p['number']}. {p['name']}",
                    callback_data=f"shot:{uid}"
                )
            ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def vote_keyboard(candidates=None):
    ids = list(alive_players())

    if candidates is not None:
        ids = candidates

    rows = []

    for uid in ids:
        p = game["players"].get(uid)

        if p and p["alive"]:
            rows.append([
                InlineKeyboardButton(
                    text=f"👉 {p['number']}. {p['name']}",
                    callback_data=f"vote:{uid}"
                )
            ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        game["status"] = "finished"
        cancel_timer()
        await set_chat_lock(False)

        await bot.send_message(
            game["chat_id"],
            "🎉 **ПЕРЕМОГА МИРНИХ!** 😇\n\n" +
            roles_summary()
        )
        return True

    if mafia >= others:
        game["status"] = "finished"
        cancel_timer()
        await set_chat_lock(False)

        await bot.send_message(
            game["chat_id"],
            "🔪 **ПЕРЕМОГА МАФІЇ!** 😈\n\n" +
            roles_summary()
        )
        return True

    return False


# =========================
# NIGHT
# =========================

async def send_night_actions():
    game["status"] = "night"

    await set_chat_lock(True)

    await send_phase(
        "night",
        "🌙 **Місто засинає.**\n\n"
        "🔒 Чат заблоковано.\n"
        "Ролі виконують нічні дії в особистих повідомленнях."
    )

    mafia_team = "\n".join(
        f"• {p['name']}"
        for p in game["players"].values()
        if p["role"] == "mafia"
    )

    for uid, p in game["players"].items():
        if not p["alive"]:
            continue

        try:
            if p["role"] == "mafia":
                await bot.send_message(
                    uid,
                    f"🔪 **Ти МАФІЯ.**\n\n"
                    f"Ваша команда:\n{mafia_team}\n\n"
                    f"Кого вбиваємо?",
                    reply_markup=mafia_keyboard()
                )

            elif p["role"] == "doctor":
                await bot.send_message(
                    uid,
                    "🩺 **Ти ЛІКАР.**\n\n"
                    "Кого будеш лікувати?",
                    reply_markup=doctor_keyboard()
                )

            elif p["role"] == "sheriff":
                await bot.send_message(
                    uid,
                    "🕵️ **Ти ШЕРИФ.**\n\n"
                    "Цієї ночі ти можеш:\n"
                    "🔍 перевірити гравця\n"
                    "🔫 вистрілити\n"
                    "💤 нічого не робити",
                    reply_markup=sheriff_keyboard()
                )
        except Exception as e:
            print(f"Не вдалося надіслати ЛС {uid}: {e}")

    cancel_timer()
    game["timer"] = asyncio.create_task(night_timer())


async def night_timer():
    try:
        await asyncio.sleep(45)

        if game["status"] == "night":
            await resolve_night()

    except asyncio.CancelledError:
        pass


async def night_ready():
    if game["status"] != "night":
        return

    mafia_ids = [
        uid for uid, p in game["players"].items()
        if p["alive"] and p["role"] == "mafia"
    ]

    mafia_done = all(
        uid in game["mafia_votes"]
        for uid in mafia_ids
    )

    doctor_alive = any(
        p["alive"] and p["role"] == "doctor"
        for p in game["players"].values()
    )

    doctor_done = (
        not doctor_alive
        or game["doctor_target"] is not None
    )

    sheriff_alive = any(
        p["alive"] and p["role"] == "sheriff"
        for p in game["players"].values()
    )

    sheriff_done = (
        not sheriff_alive
        or game["sheriff_action"] is not None
    )

    if mafia_done and doctor_done and sheriff_done:
        cancel_timer()
        await resolve_night()


async def resolve_night():
    if game["status"] != "night":
        return

    game["status"] = "resolving"

    counts = {}

    for mafia_uid, target in game["mafia_votes"].items():
        mafia = game["players"].get(mafia_uid)

        if not mafia or not mafia["alive"]:
            continue

        if target == "skip":
            continue

        counts[target] = counts.get(target, 0) + 1

    victim = None

    if counts:
        maximum = max(counts.values())
        victim = random.choice([
            uid for uid, count in counts.items()
            if count == maximum
        ])

    doctor_target = game["doctor_target"]
    sheriff_action = game["sheriff_action"]

    text = "🌅 **Настав ранок.**\n\n"

    # МАФІЯ
    if victim:
        p = game["players"].get(victim)

        if p and p["alive"]:
            if victim == doctor_target:
                text += (
                    f"🩺 Лікар врятував "
                    f"**{p['name']}** від мафії!\n"
                )

            elif (
                p["role"] == "lucky"
                and not p["lucky_used"]
            ):
                p["lucky_used"] = True

                text += (
                    f"🍀 **{p['name']}** дивом "
                    f"вижив після замаху мафії!\n"
                )

            else:
                p["alive"] = False

                text += (
                    f"💀 Мафія вбила "
                    f"**{p['name']}**.\n"
                    f"Роль: {ROLES[p['role']]}.\n"
                )

    else:
        text += "😴 Мафія нікого не вбила.\n"

    # ПОСТРІЛ ШЕРИФА
    if isinstance(sheriff_action, int):
        p = game["players"].get(sheriff_action)

        if p and p["alive"]:
            if sheriff_action == doctor_target:
                text += (
                    f"🩺 Лікар врятував "
                    f"**{p['name']}** від пострілу шерифа.\n"
                )
            else:
                p["alive"] = False

                text += (
                    f"🔫 Шериф вистрілив у "
                    f"**{p['name']}**.\n"
                    f"Роль: {ROLES[p['role']]}.\n"
                )

    text += "\n" + alive_text()

    await set_chat_lock(False)
    await send_phase("day", text)

    if await check_win():
        return

    game["status"] = "discussion"

    await bot.send_message(
        game["chat_id"],
        "🗣 **Обговорення — 60 секунд.**\n"
        "💬 Чат відкрито."
    )

    cancel_timer()
    game["timer"] = asyncio.create_task(
        discussion_timer()
    )


async def discussion_timer():
    try:
        await asyncio.sleep(60)

        if game["status"] == "discussion":
            await start_voting()

    except asyncio.CancelledError:
        pass


# =========================
# VOTING
# =========================

async def start_voting(candidates=None):
    game["status"] = "voting"
    game["votes"].clear()

    await set_chat_lock(True)

    if candidates:
        names = ", ".join(
            f"{game['players'][uid]['number']}. "
            f"{game['players'][uid]['name']}"
            for uid in candidates
        )

        text = (
            f"⚖️ **ПЕРЕСТРІЛКА!**\n\n"
            f"Кандидати: {names}\n"
            f"30 секунд на голосування."
        )
    else:
        text = (
            "⚖️ **ГОЛОСУВАННЯ!**\n\n" +
            alive_text()
        )

    await bot.send_message(
        game["chat_id"],
        text,
        reply_markup=vote_keyboard(candidates)
    )

    cancel_timer()
    game["timer"] = asyncio.create_task(
        voting_timer()
    )


async def voting_timer():
    try:
        await asyncio.sleep(30)

        if game["status"] == "voting":
            await resolve_voting()

    except asyncio.CancelledError:
        pass


async def resolve_voting():
    if game["status"] != "voting":
        return

    alive_ids = set(alive_players())

    counts = {}

    for voter, target in game["votes"].items():
        if voter in alive_ids and target in alive_ids:
            counts[target] = counts.get(target, 0) + 1

    if not counts:
        await finish_voting(
            "⚖️ Ніхто не проголосував."
        )
        return

    maximum = max(counts.values())

    candidates = [
        uid for uid, count in counts.items()
        if count == maximum
    ]

    if len(candidates) > 1:
        if game["runoff"] is not None:
            game["runoff"] = None

            await finish_voting(
                "⚖️ Знову нічия.\n"
                "Ніхто не вибуває."
            )
        else:
            game["runoff"] = candidates
            await start_voting(candidates)

        return

    expelled = candidates[0]
    p = game["players"][expelled]

    p["alive"] = False
    game["runoff"] = None

    await finish_voting(
        f"⚖️ Місто вигнало **{p['name']}**.\n"
        f"Роль: {ROLES[p['role']]}."
    )


async def finish_voting(text):
    cancel_timer()

    await set_chat_lock(False)

    await bot.send_message(
        game["chat_id"],
        text + "\n\n" + alive_text()
    )

    if await check_win():
        return

    reset_night()
    await send_night_actions()


# =========================
# COMMANDS
# =========================

@dp.message(F.text == "/start", F.chat.type == "private")
async def private_start(message: Message):
    await message.answer(
        "👋 **Бот Мафії**\n\n"
        "🔪 Мафія — вбиває.\n"
        "🩺 Лікар — лікує. Себе можна лікувати 1 раз за гру.\n"
        "🕵️ Шериф — кожної ночі може перевірити, "
        "вистрілити або нічого не робити.\n"
        "🍀 Щасливчик — один раз переживає замах мафії.\n"
        "😇 Мирний — бере участь в обговоренні та голосуванні."
    )


@dp.message(F.text.in_({"/mafia", "/start"}))
async def create_game(message: Message):
    if message.chat.type == "private":
        return

    if game["status"] not in (
        "idle",
        "finished",
        "stopped"
    ):
        await message.answer(
            "⚠️ Попередня гра ще триває."
        )
        return

    game["status"] = "waiting"
    game["chat_id"] = message.chat.id
    game["players"].clear()
    game["runoff"] = None

    cancel_timer()
    await set_chat_lock(False)

    await message.answer(
        "🎴 **ЗБІР НА МАФІЮ!**\n\n"
        "Натискайте «Увійти в гру».\n"
        "🚀 Почати гру може **будь-хто**, не тільки адмін.",
        reply_markup=join_keyboard()
    )


@dp.message(F.text == "/cancel")
async def cancel_game(message: Message):
    if game["chat_id"] != message.chat.id:
        return

    cancel_timer()

    game["status"] = "stopped"
    game["players"].clear()

    await set_chat_lock(False)

    await message.answer(
        "❌ **Гру скасовано.**\n"
        "🔓 Чат відкрито."
    )


# =========================
# JOIN / START
# =========================

@dp.callback_query(F.data == "join")
async def join_game(callback: CallbackQuery):
    if game["status"] != "waiting":
        await callback.answer(
            "Набір вже закритий.",
            show_alert=True
        )
        return

    uid = callback.from_user.id

    if uid in game["players"]:
        await callback.answer(
            "Ти вже в грі.",
            show_alert=True
        )
        return

    game["players"][uid] = {
        "name": callback.from_user.first_name or "Гравець",
        "role": "civilian",
        "alive": True,
        "number": 0,
        "lucky_used": False,
        "self_heal_used": False,
    }

    names = "\n".join(
        f"• {p['name']}"
        for p in game["players"].values()
    )

    await callback.answer("Ти в грі!")

    try:
        await callback.message.edit_text(
            f"🎴 **Гравці ({len(game['players'])}):**\n\n"
            f"{names}\n\n"
            f"Мінімум — 5 гравців.",
            reply_markup=join_keyboard()
        )
    except Exception as e:
        print("Помилка оновлення збору:", e)


@dp.callback_query(F.data == "start")
async def start_game(callback: CallbackQuery):
    if game["status"] != "waiting":
        await callback.answer(
            "Гра вже стартувала.",
            show_alert=True
        )
        return

    total = len(game["players"])

    if total < 5:
        await callback.answer(
            "Потрібно мінімум 5 гравців.",
            show_alert=True
        )
        return

    game["status"] = "night"

    ids = list(game["players"])
    random.shuffle(ids)

    for number, uid in enumerate(ids, 1):
        game["players"][uid].update({
            "number": number,
            "role": "civilian",
            "alive": True,
            "lucky_used": False,
            "self_heal_used": False,
        })

    if total <= 5:
        mafia_count = 1
        has_lucky = True
    elif total <= 8:
        mafia_count = 2
        has_lucky = True
    elif total <= 11:
        mafia_count = 3
        has_lucky = True
    else:
        mafia_count = 3
        has_lucky = False

    index = 0

    for _ in range(mafia_count):
        game["players"][ids[index]]["role"] = "mafia"
        index += 1

    game["players"][ids[index]]["role"] = "doctor"
    index += 1

    game["players"][ids[index]]["role"] = "sheriff"
    index += 1

    if has_lucky:
        game["players"][ids[index]]["role"] = "lucky"

    try:
        await callback.message.delete()
    except Exception:
        pass

    reset_night()

    await callback.answer("Гра почалась!")

    await send_night_actions()


# =========================
# NIGHT ACTIONS
# =========================

@dp.callback_query(F.data.startswith("mafia:"))
async def mafia_action(callback: CallbackQuery):
    if game["status"] != "night":
        await callback.answer("Ніч вже закінчилась.", show_alert=True)
        return

    player = game["players"].get(callback.from_user.id)

    if (
        not player
        or not player["alive"]
        or player["role"] != "mafia"
    ):
        await callback.answer(
            "Ця дія не для тебе.",
            show_alert=True
        )
        return

    value = callback.data.split(":")[1]

    if value == "skip":
        target = "skip"
    else:
        target = int(value)

        target_player = game["players"].get(target)

        if (
            not target_player
            or not target_player["alive"]
            or target_player["role"] == "mafia"
        ):
            await callback.answer(
                "Не можна обрати цього гравця.",
                show_alert=True
            )
            return

    game["mafia_votes"][callback.from_user.id] = target

    await callback.message.edit_text(
        "🔪 **Вибір мафії збережено.**"
    )

    await callback.answer("Збережено")
    await night_ready()


@dp.callback_query(F.data.startswith("doctor:"))
async def doctor_action(callback: CallbackQuery):
    if game["status"] != "night":
        await callback.answer("Ніч вже закінчилась.", show_alert=True)
        return

    player = game["players"].get(callback.from_user.id)

    if (
        not player
        or not player["alive"]
        or player["role"] != "doctor"
    ):
        await callback.answer(
            "Ця дія не для тебе.",
            show_alert=True
        )
        return

    value = callback.data.split(":")[1]

    if value == "skip":
        game["doctor_target"] = "skip"
    else:
        target = int(value)

        if (
            target not in game["players"]
            or not game["players"][target]["alive"]
        ):
            await callback.answer(
                "Гравець недоступний.",
                show_alert=True
            )
            return

        if target == callback.from_user.id:
            if player["self_heal_used"]:
                await callback.answer(
                    "Самолікування вже використано.",
                    show_alert=True
                )
                return

            player["self_heal_used"] = True

        game["doctor_target"] = target

    await callback.message.edit_text(
        "🩺 **Вибір лікаря збережено.**"
    )

    await callback.answer("Збережено")
    await night_ready()


@dp.callback_query(F.data.startswith("check:"))
async def sheriff_check(callback: CallbackQuery):
    if game["status"] != "night":
        await callback.answer("Ніч вже закінчилась.", show_alert=True)
        return

    player = game["players"].get(callback.from_user.id)

    if (
        not player
        or not player["alive"]
        or player["role"] != "sheriff"
    ):
        await callback.answer(
            "Ця дія не для тебе.",
            show_alert=True
        )
        return

    target = int(callback.data.split(":")[1])
    target_player = game["players"].get(target)

    if not target_player or not target_player["alive"]:
        await callback.answer(
            "Гравець недоступний.",
            show_alert=True
        )
        return

    result = (
        "мафія 🔪"
        if target_player["role"] == "mafia"
        else "не мафія 😇"
    )

    game["sheriff_action"] = "check"

    await callback.message.edit_text(
        f"🔍 **Перевірка:**\n"
        f"{target_player['name']} — {result}"
    )

    await callback.answer("Перевірено")
    await night_ready()


@dp.callback_query(F.data.startswith("shot:"))
async def sheriff_shot(callback: CallbackQuery):
    if game["status"] != "night":
        await callback.answer("Ніч вже закінчилась.", show_alert=True)
        return

    player = game["players"].get(callback.from_user.id)

    if (
        not player
        or not player["alive"]
        or player["role"] != "sheriff"
    ):
        await callback.answer(
            "Ця дія не для тебе.",
            show_alert=True
        )
        return

    target = int(callback.data.split(":")[1])

    if target == callback.from_user.id:
        await callback.answer(
            "У себе стріляти не можна.",
            show_alert=True
        )
        return

    target_player = game["players"].get(target)

    if not target_player or not target_player["alive"]:
        await callback.answer(
            "Гравець недоступний.",
            show_alert=True
        )
        return

    game["sheriff_action"] = target

    await callback.message.edit_text(
        f"🔫 **Постріл збережено.**\n"
        f"Ціль: {target_player['name']}"
    )

    await callback.answer("Збережено")
    await night_ready()


@dp.callback_query(F.data == "sheriff:skip")
async def sheriff_skip(callback: CallbackQuery):
    if game["status"] != "night":
        await callback.answer("Ніч вже закінчилась.", show_alert=True)
        return

    player = game["players"].get(callback.from_user.id)

    if (
        not player
        or not player["alive"]
        or player["role"] != "sheriff"
    ):
        await callback.answer(
            "Ця дія не для тебе.",
            show_alert=True
        )
        return

    game["sheriff_action"] = "skip"

    await callback.message.edit_text(
        "🕵️ **Шериф цієї ночі нічого не робить.**"
    )

    await callback.answer("Збережено")
    await night_ready()


# =========================
# VOTE
# =========================

@dp.callback_query(F.data.startswith("vote:"))
async def vote(callback: CallbackQuery):
    if game["status"] != "voting":
        await callback.answer(
            "Голосування вже закінчилось.",
            show_alert=True
        )
        return

    player = game["players"].get(callback.from_user.id)

    if not player or not player["alive"]:
        await callback.answer(
            "Ти не можеш голосувати.",
            show_alert=True
        )
        return

    target = int(callback.data.split(":")[1])

    if (
        target not in game["players"]
        or not game["players"][target]["alive"]
    ):
        await callback.answer(
            "Гравець недоступний.",
            show_alert=True
        )
        return

    game["votes"][callback.from_user.id] = target

    await callback.message.edit_text(
        f"🗳 **Голос прийнято.**\n"
        f"Твій голос: {game['players'][target]['name']}"
    )

    await callback.answer("Голос прийнято")

    voters = [
        uid for uid in game["votes"]
        if game["players"].get(uid, {}).get("alive")
    ]

    if len(voters) >= len(alive_players()):
        cancel_timer()
        await resolve_voting()


# =========================
# START
# =========================

async def main():
    print("Бот запущено успішно!")

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
