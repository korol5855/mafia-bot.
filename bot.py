import asyncio
import os
import random
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    ChatPermissions,
)


# =========================================================
# RENDER WEB SERVER
# =========================================================

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, format, *args):
        return


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()


threading.Thread(target=run_web_server, daemon=True).start()


# =========================================================
# BOT
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN не знайдено!")

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(
        parse_mode=None
    )
)

dp = Dispatcher()


# =========================================================
# GAME
# =========================================================

game = {
    "status": "idle",
    "chat_id": None,

    "players": {},

    "mafia_votes": {},

    "doctor_target": None,

    "sheriff_action": None,
    "sheriff_target": None,

    "votes": {},

    "runoff_candidates": [],

    "timer_task": None,
}


# =========================================================
# ROLES
# =========================================================

ROLE_NAMES = {
    "mafia": "Мафія 🔪",
    "doctor": "Лікар 🩺",
    "sheriff": "Шериф 🕵️",
    "lucky": "Щасливчик 🍀",
    "civilian": "Мирний житель 😇",
}


# =========================================================
# KEYBOARDS
# =========================================================

def join_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
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
            ],
        ]
    )


def mafia_keyboard():
    buttons = []

    for uid, player in game["players"].items():
        if not player["alive"]:
            continue

        if player["role"] == "mafia":
            continue

        buttons.append([
            InlineKeyboardButton(
                text=f"{player['number']}. {player['name']}",
                callback_data=f"mafia_{uid}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="💤 Пропустити",
            callback_data="mafia_skip"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def doctor_keyboard():
    buttons = []

    for uid, player in game["players"].items():
        if not player["alive"]:
            continue

        buttons.append([
            InlineKeyboardButton(
                text=f"{player['number']}. {player['name']}",
                callback_data=f"heal_{uid}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="💤 Нікого не лікувати",
            callback_data="heal_skip"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def sheriff_keyboard(user_id):
    buttons = []

    # Перевірка
    for uid, player in game["players"].items():
        if not player["alive"]:
            continue

        buttons.append([
            InlineKeyboardButton(
                text=f"🔍 Перевірити {player['number']}. {player['name']}",
                callback_data=f"check_{uid}"
            )
        ])

    # Постріл
    for uid, player in game["players"].items():
        if not player["alive"]:
            continue

        if uid == user_id:
            continue

        buttons.append([
            InlineKeyboardButton(
                text=f"🔫 Вистрілити {player['number']}. {player['name']}",
                callback_data=f"shot_{uid}"
            )
        ])

    # Нічого
    buttons.append([
        InlineKeyboardButton(
            text="💤 Нічого не робити",
            callback_data="sheriff_skip"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def vote_keyboard(candidate_ids=None):
    buttons = []

    if candidate_ids is None:
        ids = list(game["players"].keys())
    else:
        ids = candidate_ids

    for uid in ids:
        player = game["players"].get(uid)

        if not player:
            continue

        if not player["alive"]:
            continue

        buttons.append([
            InlineKeyboardButton(
                text=f"👉 {player['number']}. {player['name']}",
                callback_data=f"vote_{uid}"
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# =========================================================
# HELPERS
# =========================================================

async def cancel_timer():
    task = game.get("timer_task")

    if task:
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    game["timer_task"] = None


async def start_timer(seconds, function):
    await cancel_timer()

    game["timer_task"] = asyncio.create_task(
        timer_wrapper(seconds, function)
    )


async def timer_wrapper(seconds, function):
    try:
        await asyncio.sleep(seconds)

        if game["status"] in (
            "night",
            "discussion",
            "voting",
        ):
            await function()

    except asyncio.CancelledError:
        pass

    except Exception as e:
        print("Помилка таймера:", e)


async def open_chat():
    chat_id = game["chat_id"]

    if not chat_id:
        return

    try:
        await bot.set_chat_permissions(
            chat_id,
            ChatPermissions(
                can_send_messages=True
            )
        )
    except Exception as e:
        print("Не вдалося відкрити чат:", e)


async def close_chat():
    chat_id = game["chat_id"]

    if not chat_id:
        return

    try:
        await bot.set_chat_permissions(
            chat_id,
            ChatPermissions(
                can_send_messages=False
            )
        )
    except Exception as e:
        print("Не вдалося закрити чат:", e)


async def send_photo(phase, text):
    chat_id = game["chat_id"]

    filename = f"{phase}.jpg"

    try:
        if os.path.exists(filename):
            await bot.send_photo(
                chat_id,
                FSInputFile(filename),
                caption=text
            )
        else:
            await bot.send_message(
                chat_id,
                text
            )
    except Exception as e:
        print("Помилка відправки фото:", e)

        try:
            await bot.send_message(
                chat_id,
                text
            )
        except Exception:
            pass


def alive_players():
    return [
        (uid, p)
        for uid, p in game["players"].items()
        if p["alive"]
    ]


def alive_ids():
    return {
        uid
        for uid, p in game["players"].items()
        if p["alive"]
    }


def alive_text():
    players = [
        p for p in game["players"].values()
        if p["alive"]
    ]

    players.sort(key=lambda x: x["number"])

    text = f"📋 Живі гравці ({len(players)}):\n"

    for p in players:
        text += f"• {p['number']}. {p['name']}\n"

    return text


def mafia_team():
    mafia = [
        p["name"]
        for p in game["players"].values()
        if p["role"] == "mafia"
    ]

    if not mafia:
        return "Немає живої мафії."

    return "\n".join(
        f"• {name}"
        for name in mafia
    )


def roles_summary():
    text = "📜 Ролі гравців:\n\n"

    players = sorted(
        game["players"].values(),
        key=lambda x: x["number"]
    )

    for p in players:
        status = "🟢 живий" if p["alive"] else "💀 мертвий"

        text += (
            f"{p['number']}. {p['name']} — "
            f"{ROLE_NAMES[p['role']]} — {status}\n"
        )

    return text


def reset_night_actions():
    game["mafia_votes"].clear()
    game["doctor_target"] = None
    game["sheriff_action"] = None
    game["sheriff_target"] = None


# =========================================================
# WIN CONDITION
# =========================================================

async def check_win():
    mafia_alive = sum(
        1
        for p in game["players"].values()
        if p["alive"] and p["role"] == "mafia"
    )

    others_alive = sum(
        1
        for p in game["players"].values()
        if p["alive"] and p["role"] != "mafia"
    )

    if mafia_alive == 0:
        await cancel_timer()

        game["status"] = "finished"

        await open_chat()

        await bot.send_message(
            game["chat_id"],
            "🎉 ПЕРЕМОГА МИРНИХ!\n\n"
            "Всю мафію знищено!\n\n"
            + roles_summary()
        )

        return True

    if mafia_alive >= others_alive:
        await cancel_timer()

        game["status"] = "finished"

        await open_chat()

        await bot.send_message(
            game["chat_id"],
            "🔪 ПЕРЕМОГА МАФІЇ!\n\n"
            "Мафія захопила місто!\n\n"
            + roles_summary()
        )

        return True

    return False


# =========================================================
# START / COMMANDS
# =========================================================

@dp.message(F.text == "/start", F.chat.type == "private")
async def private_start(message: Message):

    await message.answer(
        "👋 Привіт!\n\n"
        "Це бот для гри в Мафію.\n\n"
        "🔪 Мафія — вбиває вночі.\n"
        "🩺 Лікар — лікує гравців.\n"
        "🕵️ Шериф — кожної ночі може перевірити, "
        "вистрілити або нічого не робити.\n"
        "🍀 Щасливчик — один раз може пережити напад мафії.\n"
        "😇 Мирний житель — обговорює та голосує вдень."
    )


@dp.message(F.text.startswith("/"))
async def commands(message: Message):

    command = message.text.lower().split("@")[0]

    if message.chat.type == "private":
        return

    if command == "/mafia":

        if game["status"] in (
            "night",
            "discussion",
            "voting"
        ):
            await message.answer(
                "⚠️ Гра вже триває."
            )
            return

        await cancel_timer()
        await open_chat()

        game["status"] = "waiting"
        game["chat_id"] = message.chat.id

        game["players"].clear()
        game["mafia_votes"].clear()
        game["doctor_target"] = None
        game["sheriff_action"] = None
        game["sheriff_target"] = None
        game["votes"].clear()
        game["runoff_candidates"].clear()

        await message.answer(
            "🎴 Збір на нову гру в Мафію!\n\n"
            "Натискайте «Увійти в гру».\n"
            "Почати гру може будь-хто з учасників.",
            reply_markup=join_keyboard()
        )

    elif command == "/cancel":

        await cancel_timer()

        game["status"] = "stopped"

        game["players"].clear()

        await open_chat()

        await message.answer(
            "❌ Гру скасовано.\n\n"
            "🔓 Чат відкрито."
        )


# =========================================================
# JOIN
# =========================================================

@dp.callback_query(F.data == "join")
async def join_game(callback: CallbackQuery):

    if game["status"] != "waiting":
        await callback.answer(
            "Набір уже закінчено.",
            show_alert=True
        )
        return

    if callback.message.chat.id != game["chat_id"]:
        await callback.answer(
            "Це не та гра.",
            show_alert=True
        )
        return

    user = callback.from_user

    if user.id in game["players"]:
        await callback.answer(
            "Ти вже в грі!",
            show_alert=True
        )
        return

    game["players"][user.id] = {
        "name": user.first_name or "Гравець",
        "role": "civilian",
        "alive": True,
        "number": 0,
        "lucky_used": False,
        "self_heals_used": 0,
    }

    names = [
        p["name"]
        for p in game["players"].values()
    ]

    text = (
        "🎴 Збір гравців!\n\n"
        f"Учасників: {len(names)}\n\n"
    )

    for i, name in enumerate(names, 1):
        text += f"{i}. {name}\n"

    text += "\n🚀 Почати гру може будь-хто."

    try:
        await callback.message.edit_text(
            text,
            reply_markup=join_keyboard()
        )
    except Exception:
        pass

    await callback.answer("Ти в грі!")


# =========================================================
# START GAME
# =========================================================

@dp.callback_query(F.data == "start")
async def start_game(callback: CallbackQuery):

    if game["status"] != "waiting":
        await callback.answer(
            "Зараз гру почати не можна.",
            show_alert=True
        )
        return

    if callback.from_user.id not in game["players"]:
        await callback.answer(
            "Спочатку увійди в гру.",
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

    await cancel_timer()

    reset_night_actions()

    game["votes"].clear()
    game["runoff_candidates"].clear()

    ids = list(game["players"].keys())

    random.shuffle(ids)

    # Номери
    for number, uid in enumerate(ids, 1):
        game["players"][uid]["number"] = number
        game["players"][uid]["alive"] = True
        game["players"][uid]["lucky_used"] = False
        game["players"][uid]["self_heals_used"] = 0

    # Кількість мафії
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

    # Всім мирний
    for uid in ids:
        game["players"][uid]["role"] = "civilian"

    index = 0

    # Мафія
    for _ in range(mafia_count):
        game["players"][ids[index]]["role"] = "mafia"
        index += 1

    # Лікар
    game["players"][ids[index]]["role"] = "doctor"
    index += 1

    # Шериф
    game["players"][ids[index]]["role"] = "sheriff"
    index += 1

    # Щасливчик
    if lucky and index < total:
        game["players"][ids[index]]["role"] = "lucky"
        index += 1

    # Видалити кнопку старту
    try:
        await callback.message.delete()
    except Exception:
        pass

    await close_chat()

    await send_photo(
        "night",
        "🌙 Місто засинає.\n\n"
        "🔒 Чат закрито.\n"
        "📩 Ролі роздано в особисті повідомлення."
    )

    # Розсилка ролей
    mafia_text = mafia_team()

    for uid, player in game["players"].items():

        try:

            if player["role"] == "mafia":

                await bot.send_message(
                    uid,
                    "🔪 ТИ МАФІЯ!\n\n"
                    f"Твоя команда:\n{mafia_text}\n\n"
                    "Кого вбиваємо?",
                    reply_markup=mafia_keyboard()
                )

            elif player["role"] == "doctor":

                await bot.send_message(
                    uid,
                    "🩺 ТИ ЛІКАР!\n\n"
                    "Обери, кого лікувати.\n"
                    "Себе можна вилікувати тільки 1 раз за гру.",
                    reply_markup=doctor_keyboard()
                )

            elif player["role"] == "sheriff":

                await bot.send_message(
                    uid,
                    "🕵️ ТИ ШЕРИФ!\n\n"
                    "Цієї ночі ти можеш:\n"
                    "🔍 перевірити гравця;\n"
                    "🔫 вистрілити;\n"
                    "💤 нічого не робити.\n\n"
                    "Так можна робити КОЖНОЇ ночі.",
                    reply_markup=sheriff_keyboard(uid)
                )

            elif player["role"] == "lucky":

                await bot.send_message(
                    uid,
                    "🍀 ТИ ЩАСЛИВЧИК!\n\n"
                    "Якщо мафія вперше обере тебе жертвою, "
                    "ти можеш дивом вижити."
                )

            else:

                await bot.send_message(
                    uid,
                    "😇 ТИ МИРНИЙ ЖИТЕЛЬ!\n\n"
                    "Спи спокійно."
                )

        except Exception as e:

            print(
                f"Не вдалося написати гравцю {uid}: {e}"
            )

    await callback.answer("Гру розпочато!")

    await start_timer(
        40,
        resolve_night
    )


# =========================================================
# NIGHT ACTIONS
# =========================================================

@dp.callback_query(
    F.data.regexp(
        r"^(mafia_|heal_|check_|shot_|sheriff_skip$)"
    )
)
async def night_action(callback: CallbackQuery):

    if game["status"] != "night":
        await callback.answer(
            "Ця ніч уже закінчилася.",
            show_alert=True
        )
        return

    uid = callback.from_user.id
    player = game["players"].get(uid)

    if not player or not player["alive"]:
        await callback.answer(
            "Ти мертвий.",
            show_alert=True
        )
        return

    data = callback.data

    # =====================================================
    # МАФІЯ
    # =====================================================

    if data.startswith("mafia_"):

        if player["role"] != "mafia":
            await callback.answer(
                "Ця дія тільки для мафії.",
                show_alert=True
            )
            return

        value = data.replace("mafia_", "")

        if value == "skip":

            game["mafia_votes"][uid] = "skip"

            text = "💤 Ти вирішив нікого не вбивати."

        else:

            try:
                target_id = int(value)
            except ValueError:
                await callback.answer("Помилка.", show_alert=True)
                return

            target = game["players"].get(target_id)

            if not target or not target["alive"]:
                await callback.answer(
                    "Гравець уже мертвий.",
                    show_alert=True
                )
                return

            if target["role"] == "mafia":
                await callback.answer(
                    "Мафію вбивати не можна.",
                    show_alert=True
                )
                return

            game["mafia_votes"][uid] = target_id

            text = (
                f"🔪 Ціль обрана: {target['name']}"
            )

        try:
            await callback.message.edit_text(
                text,
                reply_markup=None
            )
        except Exception:
            pass

        await callback.answer("Збережено!")

        await check_night_ready()

        return

    # =====================================================
    # ЛІКАР
    # =====================================================

    if data.startswith("heal_"):

        if player["role"] != "doctor":
            await callback.answer(
                "Ця дія тільки для Лікаря.",
                show_alert=True
            )
            return

        value = data.replace("heal_", "")

        if value == "skip":

            game["doctor_target"] = "skip"

            text = "💤 Цієї ночі ти нікого не лікуєш."

        else:

            try:
                target_id = int(value)
            except ValueError:
                await callback.answer("Помилка.", show_alert=True)
                return

            target = game["players"].get(target_id)

            if not target or not target["alive"]:
                await callback.answer(
                    "Гравець уже мертвий.",
                    show_alert=True
                )
                return

            if target_id == uid:

                if player["self_heals_used"] >= 1:
                    await callback.answer(
                        "Ти вже використовував самолікування.",
                        show_alert=True
                    )
                    return

                player["self_heals_used"] += 1

            game["doctor_target"] = target_id

            text = (
                f"🩺 Ти лікуєш: {target['name']}"
            )

        try:
            await callback.message.edit_text(
                text,
                reply_markup=None
            )
        except Exception:
            pass

        await callback.answer("Збережено!")

        await check_night_ready()

        return

    # =====================================================
    # ШЕРИФ — НІЧОГО
    # =====================================================

    if data == "sheriff_skip":

        if player["role"] != "sheriff":
            await callback.answer(
                "Ця дія тільки для Шерифа.",
                show_alert=True
            )
            return

        game["sheriff_action"] = "skip"
        game["sheriff_target"] = None

        try:
            await callback.message.edit_text(
                "💤 Ти нічого не робиш цієї ночі.",
                reply_markup=None
            )
        except Exception:
            pass

        await callback.answer("Збережено!")

        await check_night_ready()

        return

    # =====================================================
    # ШЕРИФ — ПЕРЕВІРКА
    # =====================================================

    if data.startswith("check_"):

        if player["role"] != "sheriff":
            await callback.answer(
                "Ця дія тільки для Шерифа.",
                show_alert=True
            )
            return

        try:
            target_id = int(
                data.replace("check_", "")
            )
        except ValueError:
            await callback.answer("Помилка.", show_alert=True)
            return

        target = game["players"].get(target_id)

        if not target or not target["alive"]:
            await callback.answer(
                "Гравець уже мертвий.",
                show_alert=True
            )
            return

        if target["role"] == "mafia":
            result = "🔪 МАФІЯ"
        else:
            result = "😇 НЕ МАФІЯ"

        game["sheriff_action"] = "check"
        game["sheriff_target"] = target_id

        try:
            await callback.message.edit_text(
                f"🔍 Перевірка: {target['name']}\n\n"
                f"Результат: {result}",
                reply_markup=None
            )
        except Exception:
            pass

        await callback.answer(
            f"Результат: {result}"
        )

        await check_night_ready()

        return

    # =====================================================
    # ШЕРИФ — ПОСТРІЛ
    # =====================================================

    if data.startswith("shot_"):

        if player["role"] != "sheriff":
            await callback.answer(
                "Ця дія тільки для Шерифа.",
                show_alert=True
            )
            return

        try:
            target_id = int(
                data.replace("shot_", "")
            )
        except ValueError:
            await callback.answer("Помилка.", show_alert=True)
            return

        if target_id == uid:
            await callback.answer(
                "Не можна стріляти в себе.",
                show_alert=True
            )
            return

        target = game["players"].get(target_id)

        if not target or not target["alive"]:
            await callback.answer(
                "Гравець уже мертвий.",
                show_alert=True
            )
            return

        game["sheriff_action"] = "shot"
        game["sheriff_target"] = target_id

        try:
            await callback.message.edit_text(
                f"🔫 Ти стріляєш у {target['name']}.",
                reply_markup=None
            )
        except Exception:
            pass

        await callback.answer("Постріл зроблено!")

        await check_night_ready()

        return


# =========================================================
# CHECK NIGHT
# =========================================================

async def check_night_ready():

    if game["status"] != "night":
        return

    # Мафія
    living_mafia = [
        uid
        for uid, p in game["players"].items()
        if p["alive"] and p["role"] == "mafia"
    ]

    mafia_ready = all(
        uid in game["mafia_votes"]
        for uid in living_mafia
    )

    # Лікар
    doctor_alive = any(
        p["alive"] and p["role"] == "doctor"
        for p in game["players"].values()
    )

    if doctor_alive:
        doctor_ready = (
            game["doctor_target"] is not None
        )
    else:
        doctor_ready = True

    # Шериф
    sheriff_alive = any(
        p["alive"] and p["role"] == "sheriff"
        for p in game["players"].values()
    )

    if sheriff_alive:
        sheriff_ready = (
            game["sheriff_action"] is not None
        )
    else:
        sheriff_ready = True

    if (
        mafia_ready
        and doctor_ready
        and sheriff_ready
    ):
        await cancel_timer()
        await resolve_night()


# =========================================================
# RESOLVE NIGHT
# =========================================================

async def resolve_night():

    if game["status"] != "night":
        return

    game["status"] = "resolving"

    await cancel_timer()

    victim = None

    # -----------------------------------------------------
    # МАФІЯ ОБИРАЄ ЦІЛЬ
    # -----------------------------------------------------

    counts = {}

    living_mafia = [
        uid
        for uid, p in game["players"].items()
        if p["alive"] and p["role"] == "mafia"
    ]

    for mafia_id in living_mafia:

        target = game["mafia_votes"].get(mafia_id)

        if target == "skip":
            continue

        if target in game["players"]:

            if game["players"][target]["alive"]:
                counts[target] = (
                    counts.get(target, 0) + 1
                )

    if counts:

        max_votes = max(counts.values())

        candidates = [
            uid
            for uid, count in counts.items()
            if count == max_votes
        ]

        victim = random.choice(candidates)

    doctor_target = game["doctor_target"]

    sheriff_target = None

    if game["sheriff_action"] == "shot":
        sheriff_target = game["sheriff_target"]

    text = "🌅 Ранок у місті.\n\n"

    # -----------------------------------------------------
    # МАФІЯ
    # -----------------------------------------------------

    if victim is None:

        text += (
            "🌙 Мафія цієї ночі нікого не вбила.\n"
        )

    else:

        victim_player = game["players"].get(victim)

        if victim_player and victim_player["alive"]:

            # ЛІКАР
            if doctor_target == victim:

                text += (
                    f"🩺 Лікар врятував "
                    f"{victim_player['name']} від мафії!\n"
                )

            # ЩАСЛИВЧИК
            elif (
                victim_player["role"] == "lucky"
                and not victim_player["lucky_used"]
            ):

                victim_player["lucky_used"] = True

                text += (
                    f"🍀 {victim_player['name']} "
                    "дивом пережив напад мафії!\n"
                )

            else:

                victim_player["alive"] = False

                text += (
                    f"💀 Мафія вбила "
                    f"{victim_player['name']}!\n"
                    f"Роль: {ROLE_NAMES[victim_player['role']]}\n"
                )

    # -----------------------------------------------------
    # ПОСТРІЛ ШЕРИФА
    # -----------------------------------------------------

    if sheriff_target:

        target = game["players"].get(
            sheriff_target
        )

        if target and target["alive"]:

            # Якщо лікар лікував ціль шерифа
            if (
                doctor_target == sheriff_target
                and sheriff_target != victim
            ):

                text += (
                    f"🩺 Лікар врятував "
                    f"{target['name']} від пострілу Шерифа!\n"
                )

            else:

                target["alive"] = False

                text += (
                    f"🔫 Шериф застрелив "
                    f"{target['name']}!\n"
                    f"Роль: {ROLE_NAMES[target['role']]}\n"
                )

    text += "\n" + alive_text()

    # -----------------------------------------------------
    # ДЕНЬ
    # -----------------------------------------------------

    await open_chat()

    await send_photo(
        "day",
        text
    )

    reset_night_actions()

    if await check_win():
        return

    game["status"] = "discussion"

    await bot.send_message(
        game["chat_id"],
        "🗣 Чат відкрито.\n\n"
        "⏳ Обговорення — 60 секунд."
    )

    await start_timer(
        60,
        discussion_finished
    )


# =========================================================
# DISCUSSION
# =========================================================

async def discussion_finished():

    if game["status"] != "discussion":
        return

    await bot.send_message(
        game["chat_id"],
        "⏳ Час обговорення закінчився.\n\n"
        "⚖️ Переходимо до голосування."
    )

    await start_voting()


# =========================================================
# VOTING
# =========================================================

async def start_voting(candidate_ids=None):

    game["status"] = "voting"

    game["votes"].clear()

    await close_chat()

    if candidate_ids:

        names = []

        for uid in candidate_ids:

            p = game["players"].get(uid)

            if p and p["alive"]:
                names.append(
                    f"{p['number']}. {p['name']}"
                )

        text = (
            "⚖️ ДОДАТКОВЕ ГОЛОСУВАННЯ!\n\n"
            "Нічия між:\n"
            + "\n".join(names)
            + "\n\n"
            "⏳ 30 секунд."
        )

    else:

        text = (
            "⚖️ ГОЛОСУВАННЯ!\n\n"
            "Оберіть гравця, якого потрібно вигнати.\n\n"
            + alive_text()
        )

    await bot.send_message(
        game["chat_id"],
        text,
        reply_markup=vote_keyboard(candidate_ids)
    )

    await start_timer(
        30,
        resolve_voting
    )


# =========================================================
# VOTE
# =========================================================

@dp.callback_query(
    F.data.regexp(r"^vote_\d+$")
)
async def vote(callback: CallbackQuery):

    if game["status"] != "voting":

        await callback.answer(
            "Голосування вже закінчилося.",
            show_alert=True
        )

        return

    voter_id = callback.from_user.id

    voter = game["players"].get(voter_id)

    if not voter or not voter["alive"]:

        await callback.answer(
            "Мертві не голосують.",
            show_alert=True
        )

        return

    try:
        target_id = int(
            callback.data.replace(
                "vote_",
                ""
            )
        )
    except ValueError:

        await callback.answer(
            "Помилка.",
            show_alert=True
        )

        return

    target = game["players"].get(target_id)

    if not target or not target["alive"]:

        await callback.answer(
            "Цей гравець уже мертвий.",
            show_alert=True
        )

        return

    # Якщо перестрілка
    if game["runoff_candidates"]:

        if target_id not in game["runoff_candidates"]:

            await callback.answer(
                "Можна голосувати тільки за кандидатів перестрілки.",
                show_alert=True
            )

            return

    game["votes"][voter_id] = target_id

    try:
        await callback.message.edit_text(
            f"🗳 Твій голос: {target['name']}",
            reply_markup=None
        )
    except Exception:
        pass

    await callback.answer("Голос прийнято!")

    alive = alive_ids()

    voted = {
        uid
        for uid in game["votes"]
        if uid in alive
    }

    if len(voted) >= len(alive):

        await cancel_timer()

        await resolve_voting()


# =========================================================
# RESOLVE VOTING
# =========================================================

async def resolve_voting():

    if game["status"] != "voting":
        return

    await cancel_timer()

    alive = alive_ids()

    counts = {}

    for voter, target in game["votes"].items():

        if voter not in alive:
            continue

        if target not in alive:
            continue

        counts[target] = (
            counts.get(target, 0) + 1
        )

    text = "📊 РЕЗУЛЬТАТИ ГОЛОСУВАННЯ\n\n"

    # Ніхто не голосував
    if not counts:

        text += "Ніхто не проголосував."

        game["runoff_candidates"].clear()

        await finish_voting_round(text)

        return

    max_votes = max(
        counts.values()
    )

    candidates = [
        uid
        for uid, count in counts.items()
        if count == max_votes
    ]

    # -----------------------------------------------------
    # НІЧИЯ
    # -----------------------------------------------------

    if len(candidates) > 1:

        # Якщо це вже була перестрілка
        if game["runoff_candidates"]:

            names = ", ".join(
                game["players"][uid]["name"]
                for uid in candidates
            )

            text += (
                f"⚖️ Знову нічия: {names}\n\n"
                "Ніхто не покидає місто."
            )

            game["runoff_candidates"].clear()

            await finish_voting_round(text)

            return

        # Перша нічия
        game["runoff_candidates"] = candidates

        names = []

        for uid in candidates:

            p = game["players"][uid]

            names.append(
                f"{p['number']}. {p['name']}"
            )

        text += (
            "⚖️ Нічия!\n\n"
            "Кандидати:\n"
            + "\n".join(names)
            + "\n\n"
            "Буде додаткове голосування."
        )

        await bot.send_message(
            game["chat_id"],
            text
        )

        await start_voting(
            candidate_ids=candidates
        )

        return

    # -----------------------------------------------------
    # ВИГНАННЯ
    # -----------------------------------------------------

    exiled = candidates[0]

    player = game["players"][exiled]

    player["alive"] = False

    text += (
        f"⚖️ Місто вигнало "
        f"{player['name']}.\n\n"
        f"Його роль: "
        f"{ROLE_NAMES[player['role']]}"
    )

    game["runoff_candidates"].clear()

    await finish_voting_round(text)


# =========================================================
# FINISH DAY
# =========================================================

async def finish_voting_round(text):

    text += "\n\n" + alive_text()

    await open_chat()

    await bot.send_message(
        game["chat_id"],
        text
    )

    if await check_win():
        return

    # Нова ніч
    game["status"] = "night"

    reset_night_actions()

    game["votes"].clear()

    await close_chat()

    await send_photo(
        "night",
        "🌙 Місто засинає.\n\n"
        "🔒 Чат закрито."
    )

    mafia_text = mafia_team()

    for uid, player in game["players"].items():

        if not player["alive"]:
            continue

        try:

            if player["role"] == "mafia":

                await bot.send_message(
                    uid,
                    "🔪 Ти МАФІЯ!\n\n"
                    f"Команда:\n{mafia_text}\n\n"
                    "Обери жертву.",
                    reply_markup=mafia_keyboard()
                )

            elif player["role"] == "doctor":

                await bot.send_message(
                    uid,
                    "🩺 Ти ЛІКАР!\n\n"
                    "Обери, кого лікувати.",
                    reply_markup=doctor_keyboard()
                )

            elif player["role"] == "sheriff":

                await bot.send_message(
                    uid,
                    "🕵️ Ти ШЕРИФ!\n\n"
                    "Що робимо цієї ночі?",
                    reply_markup=sheriff_keyboard(uid)
                )

        except Exception as e:

            print(
                f"Помилка ЛС {uid}: {e}"
            )

    await start_timer(
        40,
        resolve_night
    )


# =========================================================
# MAIN
# =========================================================

async def main():

    print("Бот запущено!")

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
