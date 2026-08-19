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
    ChatPermissions
)


# =========================================================
# WEB SERVER FOR RENDER
# =========================================================

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, format, *args):
        pass


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()


threading.Thread(
    target=run_web_server,
    daemon=True
).start()


# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN не знайдено у змінних середовища!")


bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.MARKDOWN
    )
)

dp = Dispatcher()


# =========================================================
# GAME STATE
# =========================================================

game = {
    "status": "waiting",

    # user_id:
    # {
    #   name,
    #   role,
    #   alive,
    #   number,
    #   lucky_used,
    #   self_heals_used
    # }
    "players": {},

    "chat_id": None,

    # Мафія
    "mafia_votes": {},

    # Лікар
    "doctor_target": None,

    # Шериф
    "sheriff_target": None,
    "sheriff_shot": None,
    "sheriff_action_done": False,

    # Денне голосування
    "votes": {},

    # Додаткове голосування при нічиїй
    "runoff_candidates": [],

    # Поточний таймер
    "timer_task": None
}


# =========================================================
# ROLES
# =========================================================

ROLE_ICONS = {
    "mafia": "Мафія 🔪",
    "doctor": "Лікар 🩺",
    "sheriff": "Шериф 🕵️",
    "lucky": "Щасливчик 🍀",
    "civilian": "Мирний житель 😇"
}


# =========================================================
# KEYBOARDS
# =========================================================

def get_join_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎮 Увійти в гру",
                    callback_data="join_game"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚀 Почати гру",
                    callback_data="start_game"
                )
            ]
        ]
    )


def get_mafia_keyboard(players):
    buttons = []

    for uid, p in players.items():
        if p["alive"] and p["role"] != "mafia":
            buttons.append([
                InlineKeyboardButton(
                    text=f"{p['number']}. {p['name']}",
                    callback_data=f"mkel_{uid}"
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            text="💤 Нікого не вбивати",
            callback_data="mkel_skip"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def get_doctor_keyboard(players):
    buttons = []

    for uid, p in players.items():
        if p["alive"]:
            buttons.append([
                InlineKeyboardButton(
                    text=f"{p['number']}. {p['name']}",
                    callback_data=f"heal_{uid}"
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            text="💤 Нікого не лікувати",
            callback_data="heal_skip"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def get_sheriff_keyboard(players, sheriff_id):
    buttons = []

    # Перевірка
    for uid, p in players.items():
        if p["alive"]:
            buttons.append([
                InlineKeyboardButton(
                    text=f"🔍 Перевірити: {p['number']}. {p['name']}",
                    callback_data=f"check_{uid}"
                )
            ])

    # Постріл
    for uid, p in players.items():
        if p["alive"] and uid != sheriff_id:
            buttons.append([
                InlineKeyboardButton(
                    text=f"🔫 Вистрілити: {p['number']}. {p['name']}",
                    callback_data=f"shot_{uid}"
                )
            ])

    # Нічого не робити
    buttons.append([
        InlineKeyboardButton(
            text="💤 Нічого не робити",
            callback_data="sheriff_skip"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def get_vote_keyboard(players, candidate_ids=None):
    if candidate_ids:
        target_players = {
            uid: p
            for uid, p in players.items()
            if uid in candidate_ids
        }
    else:
        target_players = players

    buttons = []

    for uid, p in target_players.items():
        if p["alive"]:
            buttons.append([
                InlineKeyboardButton(
                    text=f"👉 {p['number']}. {p['name']}",
                    callback_data=f"vote_{uid}"
                )
            ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# =========================================================
# HELPERS
# =========================================================

async def mute_chat(chat_id: int, mute: bool = True):
    try:
        await bot.set_chat_permissions(
            chat_id,
            ChatPermissions(
                can_send_messages=not mute
            )
        )
    except Exception as e:
        print(f"Помилка зміни прав чату: {e}")


async def send_phase_photo(chat_id: int, phase: str, caption: str):
    filename = f"{phase}.jpg"

    try:
        if os.path.exists(filename):
            photo = FSInputFile(filename)

            await bot.send_photo(
                chat_id,
                photo=photo,
                caption=caption
            )
        else:
            await bot.send_message(
                chat_id,
                caption
            )

    except Exception as e:
        print(f"Помилка відправки фото: {e}")

        try:
            await bot.send_message(
                chat_id,
                caption
            )
        except Exception:
            pass


def get_alive_list_text():
    alive_players = [
        p for p in game["players"].values()
        if p["alive"]
    ]

    alive_players.sort(
        key=lambda x: x["number"]
    )

    text = (
        f"📋 **Живі гравці у місті "
        f"({len(alive_players)}):**\n"
    )

    for p in alive_players:
        text += (
            f"• {p['number']}. {p['name']}\n"
        )

    return text


def format_all_roles_summary():
    text = (
        "📜 **Склад завершеної гри "
        "(хто ким був):**\n\n"
    )

    players = sorted(
        game["players"].values(),
        key=lambda x: x["number"]
    )

    for p in players:
        status = (
            "💀 мертвий"
            if not p["alive"]
            else
            "🟢 вижив"
        )

        role = ROLE_ICONS.get(
            p["role"],
            p["role"]
        )

        text += (
            f"• {p['number']}. "
            f"{p['name']} — "
            f"{role} "
            f"({status})\n"
        )

    return text


def get_mafia_team_str():
    mafia_members = [
        p["name"]
        for p in game["players"].values()
        if p["role"] == "mafia"
    ]

    return "\n".join(
        f"• {name}"
        for name in mafia_members
    )


def cancel_timer():
    task = game.get("timer_task")

    if task and not task.done():
        task.cancel()

    game["timer_task"] = None


# =========================================================
# /START IN PRIVATE
# =========================================================

@dp.message(
    F.text == "/start",
    F.chat.type == "private"
)
async def private_start(message: Message):
    user_name = message.from_user.first_name

    await message.answer(
        f"👋 Привіт, **{user_name}**!\n\n"
        "Вітаю тебе в боті для гри в **Мафію**.\n\n"

        "📜 **Ролі:**\n\n"

        "🔪 **Мафія** — "
        "вночі обирає жертву.\n\n"

        "🩺 **Лікар** — "
        "вночі може врятувати будь-кого. "
        "Себе можна врятувати лише 1 раз за гру.\n\n"

        "🕵️ **Шериф** — "
        "кожної ночі сам вирішує, що робити: "
        "🔍 перевірити гравця, "
        "🔫 вистрілити або 💤 нічого не робити.\n\n"

        "🍀 **Щасливчик** — "
        "має шанс пережити постріл мафії.\n\n"

        "😇 **Мирний житель** — "
        "бере участь у денному обговоренні "
        "та голосуванні."
    )


# =========================================================
# COMMANDS
# =========================================================

@dp.message(F.text.startswith("/"))
async def cmd_commands(message: Message):
    text = message.text.lower().split("@")[0]

    # -----------------------------------------------------
    # /mafia
    # -----------------------------------------------------

    if text in ["/mafia", "/start"]:

        # У приватному чаті /start вже оброблений вище
        if message.chat.type == "private":
            return

        if (
            game["status"]
            not in ["waiting", "finished", "stopped"]
            and game["players"]
        ):
            await message.answer(
                "⚠️ Неможливо почати нову гру.\n"
                "Попередня партія ще триває!"
            )
            return

        cancel_timer()

        game["status"] = "waiting"
        game["players"].clear()
        game["chat_id"] = message.chat.id

        game["mafia_votes"].clear()
        game["doctor_target"] = None
        game["sheriff_target"] = None
        game["sheriff_shot"] = None
        game["sheriff_action_done"] = False
        game["votes"].clear()
        game["runoff_candidates"].clear()

        await message.answer(
            "🎴 **Увага!**\n\n"
            "Оголошено збір на нову гру в **Мафію**!\n\n"
            "🎮 Натискайте кнопку нижче, "
            "щоб приєднатися.\n\n"
            "🚀 **Почати гру може будь-хто з учасників.**",
            reply_markup=get_join_keyboard()
        )

    # -----------------------------------------------------
    # /cancel
    # -----------------------------------------------------

    elif text == "/cancel":

        cancel_timer()

        game["status"] = "stopped"
        game["players"].clear()

        await mute_chat(
            message.chat.id,
            False
        )

        await message.answer(
            "❌ **ГРУ СКАСОВАНО!**\n\n"
            "🔓 Чат розблоковано.\n"
            "Можна розпочати нову гру командою /mafia"
        )


# =========================================================
# JOIN GAME
# =========================================================

@dp.callback_query(F.data == "join_game")
async def cb_join(callback: CallbackQuery):

    if game["status"] != "waiting":
        await callback.answer(
            "Зараз немає активного набору!",
            show_alert=True
        )
        return

    if (
        not callback.message
        or callback.message.chat.id != game["chat_id"]
    ):
        await callback.answer(
            "Ця гра вже неактивна.",
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
        "name": user.first_name,
        "role": "civilian",
        "alive": True,
        "number": 0,
        "lucky_used": False,
        "self_heals_used": 0
    }

    await callback.answer(
        "Ти увійшов у гру! 🎮"
    )

    names = [
        p["name"]
        for p in game["players"].values()
    ]

    text = (
        "🕶 **Збір гравців!**\n\n"
        f"👥 Учасники ({len(names)}):\n"
        +
        "\n".join(
            f"• {name}"
            for name in names
        )
        +
        "\n\n"
        "🚀 **Почати гру може будь-хто з учасників.**"
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_join_keyboard()
        )
    except Exception:
        pass


# =========================================================
# START GAME
# =========================================================

@dp.callback_query(F.data == "start_game")
async def cb_start(callback: CallbackQuery):

    if game["status"] != "waiting":
        await callback.answer(
            "Гра вже почалася або набір закритий!",
            show_alert=True
        )
        return

    if (
        not callback.message
        or callback.message.chat.id != game["chat_id"]
    ):
        await callback.answer(
            "Ця гра вже неактивна.",
            show_alert=True
        )
        return

    total_players = len(game["players"])

    if total_players < 5:
        await callback.answer(
            "Потрібно мінімум 5 гравців!",
            show_alert=True
        )
        return

    game["status"] = "night"

    game["mafia_votes"].clear()
    game["doctor_target"] = None
    game["sheriff_target"] = None
    game["sheriff_shot"] = None
    game["sheriff_action_done"] = False
    game["votes"].clear()
    game["runoff_candidates"].clear()

    # -----------------------------------------------------
    # Нумерація
    # -----------------------------------------------------

    user_ids = list(
        game["players"].keys()
    )

    random.shuffle(user_ids)

    for i, uid in enumerate(
        user_ids,
        start=1
    ):
        game["players"][uid]["number"] = i
        game["players"][uid]["alive"] = True
        game["players"][uid]["lucky_used"] = False
        game["players"][uid]["self_heals_used"] = 0
        game["players"][uid]["role"] = "civilian"

    # -----------------------------------------------------
    # Кількість мафії
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # Роздача ролей
    # -----------------------------------------------------

    idx = 0

    # Мафія
    for _ in range(mafia_count):
        game["players"][
            user_ids[idx]
        ]["role"] = "mafia"

        idx += 1

    # Лікар
    game["players"][
        user_ids[idx]
    ]["role"] = "doctor"

    idx += 1

    # Шериф
    game["players"][
        user_ids[idx]
    ]["role"] = "sheriff"

    idx += 1

    # Щасливчик
    if has_lucky:
        game["players"][
            user_ids[idx]
        ]["role"] = "lucky"

        idx += 1

    # Решта — мирні
    for i in range(idx, total_players):
        game["players"][
            user_ids[i]
        ]["role"] = "civilian"

    # -----------------------------------------------------
    # Прибираємо кнопку набору
    # -----------------------------------------------------

    try:
        await callback.message.delete()
    except Exception:
        pass

    # -----------------------------------------------------
    # Блокуємо чат
    # -----------------------------------------------------

    await mute_chat(
        game["chat_id"],
        True
    )

    await send_phase_photo(
        game["chat_id"],
        "night",
        "🌙 **Місто засинає.**\n\n"
        "🔒 Чат заблоковано.\n"
        "🎭 Ролі роздано в особисті повідомлення."
    )

    # -----------------------------------------------------
    # Розсилаємо ролі
    # -----------------------------------------------------

    mafia_team_text = get_mafia_team_str()

    for uid, p in game["players"].items():

        try:

            # =========================
            # МАФІЯ
            # =========================

            if p["role"] == "mafia":

                await bot.send_message(
                    uid,
                    "🔪 **ТИ — МАФІЯ**\n\n"
                    f"👥 **Ваша команда:**\n"
                    f"{mafia_team_text}\n\n"
                    "🎯 **Кого вбиваємо?**\n\n"
                    f"{get_alive_list_text()}",
                    reply_markup=get_mafia_keyboard(
                        game["players"]
                    )
                )

            # =========================
            # ЛІКАР
            # =========================

            elif p["role"] == "doctor":

                await bot.send_message(
                    uid,
                    "🩺 **ТИ — ЛІКАР**\n\n"
                    "Ти можеш кожної ночі "
                    "врятувати одного гравця.\n\n"
                    "❤️ Себе можна врятувати "
                    "лише **1 раз за всю гру**.\n\n"
                    f"{get_alive_list_text()}",
                    reply_markup=get_doctor_keyboard(
                        game["players"]
                    )
                )

            # =========================
            # ШЕРИФ
            # =========================

            elif p["role"] == "sheriff":

                await bot.send_message(
                    uid,
                    "🕵️ **ТИ — ШЕРИФ**\n\n"
                    "Кожної ночі ти сам вирішуєш, "
                    "що робити:\n\n"
                    "🔍 **Перевірити** гравця — "
                    "дізнатися, чи він мафія.\n"
                    "🔫 **Вистрілити** — "
                    "спробувати вбити гравця.\n"
                    "💤 **Нічого не робити**.\n\n"
                    "Обмежень між ночами немає.\n\n"
                    f"{get_alive_list_text()}",
                    reply_markup=get_sheriff_keyboard(
                        game["players"],
                        uid
                    )
                )

            # =========================
            # ЩАСЛИВЧИК
            # =========================

            elif p["role"] == "lucky":

                await bot.send_message(
                    uid,
                    "🍀 **ТИ — ЩАСЛИВЧИК**\n\n"
                    "Якщо мафія обере тебе "
                    "своєю жертвою, "
                    "ти маєш шанс дивом вижити.\n\n"
                    f"{get_alive_list_text()}"
                )

            # =========================
            # МИРНИЙ
            # =========================

            else:

                await bot.send_message(
                    uid,
                    "😇 **ТИ — МИРНИЙ ЖИТЕЛЬ**\n\n"
                    "Уночі ти нічого не робиш.\n"
                    "Вдень бери участь "
                    "в обговоренні та голосуванні.\n\n"
                    f"{get_alive_list_text()}"
                )

        except Exception as e:
            # Користувач міг не відкрити ЛС боту
            print(
                f"Не вдалося надіслати роль "
                f"{uid}: {e}"
            )

    # -----------------------------------------------------
    # Таймер ночі
    # -----------------------------------------------------

    cancel_timer()

    game["timer_task"] = asyncio.create_task(
        night_timer()
    )


# =========================================================
# NIGHT TIMER
# =========================================================

async def night_timer():

    try:
        await asyncio.sleep(40)

        if game["status"] == "night":
            await resolve_night()

    except asyncio.CancelledError:
        pass


# =========================================================
# NIGHT ACTIONS
# =========================================================

@dp.callback_query(
    F.data.startswith(
        (
            "mkel_",
            "heal_",
            "check_",
            "shot_",
            "sheriff_skip"
        )
    )
)
async def cb_night_actions(
    callback: CallbackQuery
):

    if game["status"] != "night":
        await callback.answer(
            "❌ Ця ніч вже закінчилася!",
            show_alert=True
        )
        return

    user_id = callback.from_user.id

    player = game["players"].get(
        user_id
    )

    if not player:
        await callback.answer(
            "❌ Ти не береш участі в цій грі!",
            show_alert=True
        )
        return

    if not player["alive"]:
        await callback.answer(
            "❌ Мертві не можуть діяти!",
            show_alert=True
        )
        return

    data = callback.data.split("_")
    action = data[0]

    choice_text = "✅ Вибір збережено."

    # =====================================================
    # МАФІЯ
    # =====================================================

    if action == "mkel":

        if player["role"] != "mafia":
            await callback.answer(
                "❌ Ця дія доступна лише мафії!",
                show_alert=True
            )
            return

        if data[1] == "skip":
            target_val = "skip"

            choice_text = (
                "💤 Ви вирішили "
                "цієї ночі нікого не вбивати."
            )

        else:
            try:
                target_val = int(data[1])
            except ValueError:
                await callback.answer(
                    "❌ Некоректна ціль!",
                    show_alert=True
                )
                return

            target_player = game["players"].get(
                target_val
            )

            if (
                not target_player
                or not target_player["alive"]
            ):
                await callback.answer(
                    "❌ Цей гравець уже мертвий!",
                    show_alert=True
                )
                return

            if target_player["role"] == "mafia":
                await callback.answer(
                    "❌ Мафія не може вбити мафію!",
                    show_alert=True
                )
                return

            choice_text = (
                f"✅ Ціль обрана: "
                f"**{target_player['name']}**"
            )

        game["mafia_votes"][user_id] = target_val

    # =====================================================
    # ЛІКАР
    # =====================================================

    elif action == "heal":

        if player["role"] != "doctor":
            await callback.answer(
                "❌ Ця дія доступна лише лікарю!",
                show_alert=True
            )
            return

        if data[1] == "skip":

            game["doctor_target"] = "skip"

            choice_text = (
                "💤 Ви вирішили "
                "нікого не лікувати."
            )

        else:

            try:
                target_id = int(data[1])
            except ValueError:
                await callback.answer(
                    "❌ Некоректна ціль!",
                    show_alert=True
                )
                return

            target_player = game["players"].get(
                target_id
            )

            if (
                not target_player
                or not target_player["alive"]
            ):
                await callback.answer(
                    "❌ Цей гравець уже мертвий!",
                    show_alert=True
                )
                return

            # Самолікування — максимум один раз
            if target_id == user_id:

                if player["self_heals_used"] >= 1:
                    await callback.answer(
                        "❌ Ти вже використовував "
                        "самолікування!",
                        show_alert=True
                    )
                    return

                player["self_heals_used"] += 1

            game["doctor_target"] = target_id

            choice_text = (
                f"🩺 Ви лікуєте: "
                f"**{target_player['name']}**"
            )

    # =====================================================
    # ШЕРИФ
    # =====================================================

    elif action in ["check", "shot"]:

        if player["role"] != "sheriff":
            await callback.answer(
                "❌ Ця дія доступна лише шерифу!",
                show_alert=True
            )
            return

        # Один вибір за ніч.
        # Наступної ночі знову можна
        # робити будь-яку дію.
        if game["sheriff_action_done"]:
            await callback.answer(
                "❌ Ти вже вибрав дію цієї ночі!",
                show_alert=True
            )
            return

        try:
            target_id = int(data[1])
        except ValueError:
            await callback.answer(
                "❌ Некоректна ціль!",
                show_alert=True
            )
            return

        target_player = game["players"].get(
            target_id
        )

        if (
            not target_player
            or not target_player["alive"]
        ):
            await callback.answer(
                "❌ Цей гравець уже мертвий!",
                show_alert=True
            )
            return

        # -------------------------------------------------
        # ПЕРЕВІРКА
        # -------------------------------------------------

        if action == "check":

            target_role = target_player["role"]

            if target_role == "mafia":
                result = "🔪 МАФІЯ"
            else:
                result = "😇 НЕ МАФІЯ"

            await callback.message.answer(
                f"🔍 **Результат перевірки:**\n\n"
                f"Гравець "
                f"**{target_player['name']}** — "
                f"{result}"
            )

            game["sheriff_target"] = target_id
            game["sheriff_action_done"] = True

            choice_text = (
                f"🔍 Перевірено: "
                f"**{target_player['name']}**\n\n"
                f"Результат: {result}"
            )

        # -------------------------------------------------
        # ПОСТРІЛ
        # -------------------------------------------------

        elif action == "shot":

            if target_id == user_id:
                await callback.answer(
                    "❌ Не можна стріляти в себе!",
                    show_alert=True
                )
                return

            game["sheriff_shot"] = target_id
            game["sheriff_action_done"] = True

            choice_text = (
                f"🔫 Постріл зроблено в "
                f"**{target_player['name']}**."
            )

    # =====================================================
    # ШЕРИФ НІЧОГО НЕ РОБИТЬ
    # =====================================================

    elif action == "sheriff":

        if player["role"] != "sheriff":
            await callback.answer(
                "❌ Ця дія доступна лише шерифу!",
                show_alert=True
            )
            return

        if game["sheriff_action_done"]:
            await callback.answer(
                "❌ Ти вже вибрав дію цієї ночі!",
                show_alert=True
            )
            return

        game["sheriff_action_done"] = True

        choice_text = (
            "💤 Шериф вирішив "
            "цієї ночі нічого не робити."
        )

    # =====================================================
    # ЗАКРІПЛЕННЯ ВИБОРУ
    # =====================================================

    try:
        await callback.message.edit_text(
            choice_text,
            reply_markup=None
        )
    except Exception:
        pass

    await callback.answer(
        "✅ Дію збережено!"
    )

    await check_night_actions()


# =========================================================
# CHECK NIGHT ACTIONS
# =========================================================

async def check_night_actions():

    if game["status"] != "night":
        return

    # -----------------------------------------------------
    # Мафія
    # -----------------------------------------------------

    alive_mafias = [
        uid
        for uid, p in game["players"].items()
        if p["alive"]
        and p["role"] == "mafia"
    ]

    mafia_done = all(
        uid in game["mafia_votes"]
        for uid in alive_mafias
    )

    # -----------------------------------------------------
    # Лікар
    # -----------------------------------------------------

    doctor_alive = any(
        p["alive"]
        and p["role"] == "doctor"
        for p in game["players"].values()
    )

    if doctor_alive:
        doctor_done = (
            game["doctor_target"]
            is not None
        )
    else:
        doctor_done = True

    # -----------------------------------------------------
    # Шериф
    # -----------------------------------------------------

    sheriff_alive = any(
        p["alive"]
        and p["role"] == "sheriff"
        for p in game["players"].values()
    )

    if sheriff_alive:
        sheriff_done = (
            game["sheriff_action_done"]
        )
    else:
        sheriff_done = True

    # -----------------------------------------------------
    # Усі зробили дії
    # -----------------------------------------------------

    if (
        mafia_done
        and doctor_done
        and sheriff_done
    ):
        cancel_timer()
        await resolve_night()


# =========================================================
# RESOLVE NIGHT
# =========================================================

async def resolve_night():

    if game["status"] != "night":
        return

    victim = None

    # =====================================================
    # ВИБІР МАФІЇ
    # =====================================================

    alive_mafias = [
        uid
        for uid, p in game["players"].items()
        if p["alive"]
        and p["role"] == "mafia"
    ]

    target_counts = {}

    skip_count = 0

    for mafia_id, target_id in game[
        "mafia_votes"
    ].items():

        mafia_player = game["players"].get(
            mafia_id
        )

        if (
            not mafia_player
            or not mafia_player["alive"]
            or mafia_player["role"] != "mafia"
        ):
            continue

        if target_id == "skip":
            skip_count += 1
        else:
            target_counts[target_id] = (
                target_counts.get(
                    target_id,
                    0
                ) + 1
            )

    if alive_mafias:

        if skip_count == len(alive_mafias):
            victim = "skip"

        elif target_counts:

            max_votes = max(
                target_counts.values()
            )

            candidates = [
                uid
                for uid, count
                in target_counts.items()
                if count == max_votes
            ]

            victim = random.choice(
                candidates
            )

    # =====================================================
    # ДІЇ
    # =====================================================

    doctor_target = game[
        "doctor_target"
    ]

    sheriff_shot = game[
        "sheriff_shot"
    ]

    text = (
        "🌅 **Ранок у місті.**\n\n"
    )

    # =====================================================
    # МАФІЯ
    # =====================================================

    if (
        victim
        and victim != "skip"
    ):

        victim_player = game[
            "players"
        ].get(victim)

        if (
            victim_player
            and victim_player["alive"]
        ):

            # Лікар врятував
            if victim == doctor_target:

                text += (
                    f"🩺 **Лікар врятував "
                    f"{victim_player['name']} "
                    f"від кулі мафії!**\n"
                )

            # Щасливчик
            elif (
                victim_player["role"] == "lucky"
                and not victim_player["lucky_used"]
            ):

                victim_player[
                    "lucky_used"
                ] = True

                if random.random() < 0.5:

                    text += (
                        f"🍀 **{victim_player['name']}** "
                        "дивом вижив після "
                        "нападу мафії!\n"
                    )

                else:

                    victim_player[
                        "alive"
                    ] = False

                    role_name = ROLE_ICONS.get(
                        victim_player["role"]
                    )

                    text += (
                        f"💀 **{victim_player['name']}** "
                        "вбито мафією!\n"
                        f"Його роль: **{role_name}** 🪦\n"
                    )

            # Звичайна смерть
            else:

                victim_player[
                    "alive"
                ] = False

                role_name = ROLE_ICONS.get(
                    victim_player["role"]
                )

                text += (
                    f"💀 **{victim_player['name']}** "
                    "вбито мафією!\n"
                    f"Його роль: **{role_name}** 🪦\n"
                )

    else:

        text += (
            "🌙 Цієї ночі "
            "мафія нікого не вбила.\n"
        )

    # =====================================================
    # ПОСТРІЛ ШЕРИФА
    # =====================================================

    if sheriff_shot:

        shot_player = game[
            "players"
        ].get(sheriff_shot)

        if (
            shot_player
            and shot_player["alive"]
        ):

            # Лікар може врятувати від пострілу
            if (
                sheriff_shot == doctor_target
            ):

                text += (
                    f"🩺 **Лікар врятував "
                    f"{shot_player['name']} "
                    "від пострілу шерифа!**\n"
                )

            else:

                shot_player[
                    "alive"
                ] = False

                role_name = ROLE_ICONS.get(
                    shot_player["role"]
                )

                text += (
                    f"🎯 **Шериф застрелив "
                    f"{shot_player['name']}!**\n"
                    f"Його роль: **{role_name}** 🪦\n"
                )

    # =====================================================
    # СПИСОК ЖИВИХ
    # =====================================================

    text += (
        "\n"
        + get_alive_list_text()
    )

    await mute_chat(
        game["chat_id"],
        False
    )

    await send_phase_photo(
        game["chat_id"],
        "day",
        text
    )

    # =====================================================
    # ПЕРЕВІРКА ПЕРЕМОГИ
    # =====================================================

    if await check_win_condition():
        return

    # =====================================================
    # ОБГОВОРЕННЯ
    # =====================================================

    game["status"] = "discussion"

    await bot.send_message(
        game["chat_id"],
        "🗣 **Чат відкрито!**\n\n"
        "Обговорення — **1 хвилина** ⏳"
    )

    cancel_timer()

    game["timer_task"] = asyncio.create_task(
        discussion_timer()
    )


# =========================================================
# DISCUSSION TIMER
# =========================================================

async def discussion_timer():

    try:

        await asyncio.sleep(60)

        if game["status"] == "discussion":

            await bot.send_message(
                game["chat_id"],
                "⏳ **Час обговорення вийшов!**\n\n"
                "Переходимо до голосування ⚖️"
            )

            await start_voting()

    except asyncio.CancelledError:
        pass


# =========================================================
# START VOTING
# =========================================================

async def start_voting(
    candidate_ids=None
):

    game["status"] = "voting"
    game["votes"].clear()

    await mute_chat(
        game["chat_id"],
        True
    )

    if candidate_ids:

        names = ", ".join(
            f"{game['players'][uid]['number']}. "
            f"{game['players'][uid]['name']}"
            for uid in candidate_ids
        )

        msg_text = (
            "⚖️ **ПЕРЕСТРІЛКА!**\n\n"
            f"Голоси розділилися між:\n"
            f"**{names}**\n\n"
            "У вас **30 секунд** "
            "на вирішальне голосування!"
        )

    else:

        msg_text = (
            "⚖️ **ЧАС ГОЛОСУВАННЯ!**\n\n"
            "Оберіть гравця, "
            "якого місто хоче вигнати.\n\n"
            +
            get_alive_list_text()
        )

    await bot.send_message(
        game["chat_id"],
        msg_text,
        reply_markup=get_vote_keyboard(
            game["players"],
            candidate_ids
        )
    )

    cancel_timer()

    game["timer_task"] = asyncio.create_task(
        voting_timer()
    )


# =========================================================
# VOTING TIMER
# =========================================================

async def voting_timer():

    try:

        await asyncio.sleep(30)

        if game["status"] == "voting":
            await resolve_voting()

    except asyncio.CancelledError:
        pass


# =========================================================
# VOTE
# =========================================================

@dp.callback_query(
    F.data.startswith("vote_")
)
async def cb_vote(
    callback: CallbackQuery
):

    if game["status"] != "voting":
        await callback.answer(
            "❌ Голосування не триває!",
            show_alert=True
        )
        return

    voter_id = callback.from_user.id

    player = game["players"].get(
        voter_id
    )

    if (
        not player
        or not player["alive"]
    ):
        await callback.answer(
            "❌ Мертві не голосують!",
            show_alert=True
        )
        return

    try:
        target_id = int(
            callback.data.split("_")[1]
        )
    except ValueError:
        await callback.answer(
            "❌ Некоректний голос!",
            show_alert=True
        )
        return

    target_player = game[
        "players"
    ].get(target_id)

    if (
        not target_player
        or not target_player["alive"]
    ):
        await callback.answer(
            "❌ Не можна голосувати "
            "за мертвого!",
            show_alert=True
        )
        return

    # Якщо це перестрілка —
    # голосувати можна тільки за кандидатів
    if (
        game["runoff_candidates"]
        and target_id
        not in game["runoff_candidates"]
    ):
        await callback.answer(
            "❌ Можна голосувати "
            "лише за кандидатів перестрілки!",
            show_alert=True
        )
        return

    game["votes"][
        voter_id
    ] = target_id

    try:

        await callback.message.edit_text(
            "🗳 **Ваш голос прийнято!**\n\n"
            f"Ви проголосували за "
            f"**{target_player['name']}**.\n\n"
            "Очікуємо інших...",
            reply_markup=None
        )

    except Exception:
        pass

    await callback.answer(
        "✅ Голос прийнято!"
    )

    alive_players_ids = {
        uid
        for uid, p
        in game["players"].items()
        if p["alive"]
    }

    voted_alive_count = sum(
        1
        for voter_id in game["votes"]
        if voter_id in alive_players_ids
    )

    # Всі живі проголосували
    if (
        voted_alive_count
        >= len(alive_players_ids)
    ):

        cancel_timer()

        await resolve_voting()


# =========================================================
# RESOLVE VOTING
# =========================================================

async def resolve_voting():

    if game["status"] != "voting":
        return

    alive_players_ids = {
        uid
        for uid, p
        in game["players"].items()
        if p["alive"]
    }

    vote_counts = {}

    for voter, target in game[
        "votes"
    ].items():

        if (
            voter in alive_players_ids
            and target in alive_players_ids
        ):
            vote_counts[target] = (
                vote_counts.get(
                    target,
                    0
                ) + 1
            )

    text = (
        "📊 **Результати голосування:**\n\n"
    )

    # Ніхто не голосував
    if not vote_counts:

        text += (
            "Ніхто не проголосував.\n"
        )

        game["runoff_candidates"].clear()

        await finalize_voting_round(
            text
        )

        return

    max_votes = max(
        vote_counts.values()
    )

    candidates = [
        uid
        for uid, count
        in vote_counts.items()
        if count == max_votes
    ]

    # =====================================================
    # НІЧИЯ
    # =====================================================

    if len(candidates) > 1:

        # Якщо це вже була перестрілка
        # і знову нічия — ніхто не вибуває
        if game["runoff_candidates"]:

            names = ", ".join(
                game["players"][uid]["name"]
                for uid in candidates
            )

            text += (
                f"⚖️ **Знову нічия!**\n\n"
                f"Кандидати: **{names}**\n\n"
                "Місто нікого не виганяє."
            )

            game[
                "runoff_candidates"
            ].clear()

            await finalize_voting_round(
                text
            )

            return

        # Перша нічия
        game[
            "runoff_candidates"
        ] = candidates

        names_str = ", ".join(
            f"{game['players'][uid]['number']}. "
            f"{game['players'][uid]['name']}"
            for uid in candidates
        )

        text += (
            "⚖️ **НІЧИЯ!**\n\n"
            f"Однакова кількість голосів у:\n"
            f"**{names_str}**\n\n"
            "Запускаємо додатковий раунд!"
        )

        await bot.send_message(
            game["chat_id"],
            text
        )

        await start_voting(
            candidate_ids=candidates
        )

        return

    # =====================================================
    # Є ПЕРЕМОЖЕЦЬ ГОЛОСУВАННЯ
    # =====================================================

    exiled = candidates[0]

    game["players"][
        exiled
    ]["alive"] = False

    role_key = game[
        "players"
    ][exiled]["role"]

    role_name = ROLE_ICONS.get(
        role_key,
        role_key
    )

    text += (
        f"⚖️ **Місто вигнало "
        f"{game['players'][exiled]['name']}!**\n\n"
        f"Його роль: **{role_name}** 🪦"
    )

    game[
        "runoff_candidates"
    ].clear()

    await finalize_voting_round(
        text
    )


# =========================================================
# FINISH VOTING ROUND
# =========================================================

async def finalize_voting_round(
    text: str
):

    text += (
        "\n\n"
        + get_alive_list_text()
    )

    await mute_chat(
        game["chat_id"],
        False
    )

    await bot.send_message(
        game["chat_id"],
        text
    )

    # Перевірка перемоги
    if await check_win_condition():
        return

    # =====================================================
    # НОВА НІЧ
    # =====================================================

    game["status"] = "night"

    game["mafia_votes"].clear()
    game["doctor_target"] = None
    game["sheriff_target"] = None
    game["sheriff_shot"] = None
    game["sheriff_action_done"] = False
    game["votes"].clear()

    await mute_chat(
        game["chat_id"],
        True
    )

    await send_phase_photo(
        game["chat_id"],
        "night",
        "🌙 **Місто знову засинає...**\n\n"
        "🔒 Чат заблоковано."
    )

    mafia_team_text = get_mafia_team_str()

    # -----------------------------------------------------
    # Відправка нічних дій
    # -----------------------------------------------------

    for uid, p in game["players"].items():

        if not p["alive"]:
            continue

        try:

            if p["role"] == "mafia":

                await bot.send_message(
                    uid,
                    "🔪 **Мафія, час діяти.**\n\n"
                    f"👥 Ваша команда:\n"
                    f"{mafia_team_text}\n\n"
                    "🎯 Кого вбиваємо?\n\n"
                    f"{get_alive_list_text()}",
                    reply_markup=get_mafia_keyboard(
                        game["players"]
                    )
                )

            elif p["role"] == "doctor":

                await bot.send_message(
                    uid,
                    "🩺 **Лікар, час діяти.**\n\n"
                    "Кого будеш лікувати?\n"
                    "❤️ Себе можна лікувати "
                    "лише 1 раз за гру.\n\n"
                    f"{get_alive_list_text()}",
                    reply_markup=get_doctor_keyboard(
                        game["players"]
                    )
                )

            elif p["role"] == "sheriff":

                await bot.send_message(
                    uid,
                    "🕵️ **Шериф, час діяти.**\n\n"
                    "Твій вибір:\n"
                    "🔍 перевірити\n"
                    "🔫 застрелити\n"
                    "💤 нічого не робити\n\n"
                    f"{get_alive_list_text()}",
                    reply_markup=get_sheriff_keyboard(
                        game["players"],
                        uid
                    )
                )

        except Exception as e:

            print(
                f"Помилка надсилання "
                f"нічної дії {uid}: {e}"
            )

    cancel_timer()

    game["timer_task"] = asyncio.create_task(
        night_timer()
    )


# =========================================================
# WIN CONDITION
# =========================================================

async def check_win_condition():

    alive_mafia = sum(
        1
        for p in game["players"].values()
        if p["alive"]
        and p["role"] == "mafia"
    )

    alive_non_mafia = sum(
        1
        for p in game["players"].values()
        if p["alive"]
        and p["role"] != "mafia"
    )

    # -----------------------------------------------------
    # Перемога мирних
    # -----------------------------------------------------

    if alive_mafia == 0:

        game["status"] = "finished"

        cancel_timer()

        await mute_chat(
            game["chat_id"],
            False
        )

        summary_text = (
            "🎉 **ПЕРЕМОГА МИРНИХ!** 😇\n\n"
            "Всю мафію знищено!\n\n"
            +
            format_all_roles_summary()
        )

        await bot.send_message(
            game["chat_id"],
            summary_text
        )

        return True

    # -----------------------------------------------------
    # Перемога мафії
    # -----------------------------------------------------

    if alive_mafia >= alive_non_mafia:

        game["status"] = "finished"

        cancel_timer()

        await mute_chat(
            game["chat_id"],
            False
        )

        summary_text = (
            "🔪 **ПЕРЕМОГА МАФІЇ!** 😈\n\n"
            "Мафія отримала контроль "
            "над містом!\n\n"
            +
            format_all_roles_summary()
        )

        await bot.send_message(
            game["chat_id"],
            summary_text
        )

        return True

    return False


# =========================================================
# MAIN
# =========================================================

async def main():

    print("🤖 Бот запущено успішно!")

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(bot)


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    asyncio.run(main())
