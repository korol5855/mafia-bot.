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
# 1. МІКРО-СЕРВЕР ДЛЯ RENDER
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

    server = HTTPServer(
        ("0.0.0.0", port),
        SimpleHandler
    )

    print(f"Web server started on port {port}")
    server.serve_forever()


threading.Thread(
    target=run_web_server,
    daemon=True
).start()


# =========================================================
# 2. БОТ
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
# 3. СТАН ГРИ
# =========================================================

game = {
    "status": "waiting",

    "players": {},

    "chat_id": None,

    # Ніч
    "mafia_votes": {},
    "doctor_target": None,
    "sheriff_target": None,
    "sheriff_shot": None,
    "sheriff_action_done": False,

    # День
    "votes": {},

    # Перестрілка при нічиїй
    "runoff_candidates": [],

    # Поточний таймер
    "timer_task": None
}


# =========================================================
# 4. РОЛІ
# =========================================================

ROLE_ICONS = {
    "mafia": "Мафія 🔪",
    "doctor": "Лікар 🩺",
    "sheriff": "Шериф 🕵️",
    "lucky": "Щасливчик 🍀",
    "civilian": "Мирний житель 😇"
}


# =========================================================
# 5. КЛАВІАТУРИ
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


# ---------------------------------------------------------
# Мафія
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# Лікар
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# Шериф
# ---------------------------------------------------------

def get_sheriff_keyboard(players, user_id):

    buttons = []

    # Перевірка
    for uid, p in players.items():

        if p["alive"]:

            buttons.append([
                InlineKeyboardButton(
                    text=f"🔍 Перевірити {p['number']}. {p['name']}",
                    callback_data=f"check_{uid}"
                )
            ])

    # Постріли
    for uid, p in players.items():

        if p["alive"] and uid != user_id:

            buttons.append([
                InlineKeyboardButton(
                    text=f"🔫 Вистрілити {p['number']}. {p['name']}",
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

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ---------------------------------------------------------
# Голосування
# ---------------------------------------------------------

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
# 6. ДОПОМІЖНІ ФУНКЦІЇ
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


async def send_phase_photo(
    chat_id: int,
    phase: str,
    caption: str
):

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

        print(f"Помилка відправки фази: {e}")

        try:

            await bot.send_message(
                chat_id,
                caption
            )

        except Exception:
            pass


def get_alive_list_text():

    alive_players = [
        p
        for p in game["players"].values()
        if p["alive"]
    ]

    text = (
        f"📋 **Живі гравці у місті "
        f"({len(alive_players)}):**\n"
    )

    for p in sorted(
        alive_players,
        key=lambda x: x["number"]
    ):

        text += (
            f"• {p['number']}. "
            f"{p['name']}\n"
        )

    return text


def format_all_roles_summary():

    text = (
        "📜 **Склад завершеної гри "
        "(хто ким був):**\n\n"
    )

    for p in sorted(
        game["players"].values(),
        key=lambda x: x["number"]
    ):

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
            f"{role} ({status})\n"
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

    if game["timer_task"]:

        try:
            game["timer_task"].cancel()
        except Exception:
            pass

        game["timer_task"] = None


# =========================================================
# 7. /START У ЛС
# =========================================================

@dp.message(
    F.text == "/start",
    F.chat.type == "private"
)
async def private_start(message: Message):

    user_name = message.from_user.first_name

    await message.answer(

        f"👋 Привіт, **{user_name}**!\n\n"

        "🎴 Це бот для гри в **Мафію**.\n\n"

        "📜 **Ролі:**\n"

        "🔪 **Мафія** — вночі обирає жертву.\n"

        "🩺 **Лікар** — може врятувати "
        "гравця від кулі мафії. "
        "Себе можна лікувати лише 1 раз за гру.\n"

        "🕵️ **Шериф** — кожної ночі може "
        "перевірити гравця, зробити постріл "
        "або нічого не робити.\n"

        "🍀 **Щасливчик** — може пережити "
        "першу спробу вбивства мафією.\n"

        "😇 **Мирний житель** — бере участь "
        "у денному обговоренні та голосуванні.\n\n"

        "ℹ️ Щоб отримувати роль та нічні дії "
        "в особисті повідомлення, спочатку "
        "напиши цьому боту /start."
    )


# =========================================================
# 8. КОМАНДИ В ГРУПІ
# =========================================================

@dp.message(F.text.startswith("/"))
async def cmd_commands(message: Message):

    text = (
        message.text
        .lower()
        .split("@")[0]
    )

    # -----------------------------------------------------
    # НОВА ГРА
    # -----------------------------------------------------

    if (
        text in ["/mafia", "/start"]
        and message.chat.type != "private"
    ):

        if (
            game["status"]
            not in ["waiting", "finished", "stopped"]
            and game["players"]
        ):

            await message.answer(
                "⚠️ Попередня гра ще триває!"
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
            "Оголошено збір на нову гру "
            "в **Мафію**!\n\n"

            "🎮 Натискайте **«Увійти в гру»**.\n"
            "🚀 Коли буде достатньо гравців — "
            "будь-хто може натиснути "
            "**«Почати гру»**.\n\n"

            "⚠️ Мінімум — **5 гравців**.",

            reply_markup=get_join_keyboard()
        )

    # -----------------------------------------------------
    # СКАСУВАННЯ
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
            "Можна почати нову гру командою /mafia"
        )


# =========================================================
# 9. ВХІД У ГРУ
# =========================================================

@dp.callback_query(
    F.data == "join_game"
)
async def cb_join(callback: CallbackQuery):

    if game["status"] != "waiting":

        await callback.answer(
            "Зараз немає активного набору!",
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
        "🎮 Ти увійшов у гру!"
    )

    names = [
        p["name"]
        for p in game["players"].values()
    ]

    try:

        await callback.message.edit_text(

            "🕶 **Збір гравців!**\n\n"

            f"Учасники ({len(names)}):\n"

            +
            "\n".join(
                f"• {name}"
                for name in names
            ),

            reply_markup=get_join_keyboard()
        )

    except Exception:
        pass


# =========================================================
# 10. ПОЧАТОК ГРИ
# =========================================================

@dp.callback_query(
    F.data == "start_game"
)
async def cb_start(callback: CallbackQuery):

    total_players = len(
        game["players"]
    )

    if total_players < 5:

        await callback.answer(
            "❌ Потрібно мінімум 5 гравців!",
            show_alert=True
        )

        return

    # Будь-хто може натиснути кнопку.
    # Ніякої перевірки на адміністратора немає.

    game["status"] = "night"

    cancel_timer()

    game["mafia_votes"].clear()
    game["doctor_target"] = None
    game["sheriff_target"] = None
    game["sheriff_shot"] = None
    game["sheriff_action_done"] = False
    game["votes"].clear()
    game["runoff_candidates"].clear()

    user_ids = list(
        game["players"].keys()
    )

    random.shuffle(user_ids)

    # Номери
    for i, uid in enumerate(
        user_ids,
        start=1
    ):

        game["players"][uid]["number"] = i
        game["players"][uid]["alive"] = True
        game["players"][uid]["lucky_used"] = False
        game["players"][uid]["self_heals_used"] = 0

    # -----------------------------------------------------
    # КІЛЬКІСТЬ МАФІЇ
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

    # Спочатку всім мирні
    for uid in user_ids:

        game["players"][uid]["role"] = "civilian"

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

    try:
        await callback.message.delete()
    except Exception:
        pass

    # -----------------------------------------------------
    # БЛОКУЄМО ЧАТ
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
        "🎴 Ролі роздано в особисті повідомлення."
    )

    mafia_team_text = get_mafia_team_str()

    # -----------------------------------------------------
    # РОЗДАЧА РОЛЕЙ
    # -----------------------------------------------------

    failed_private = []

    for uid, p in game["players"].items():

        try:

            if p["role"] == "mafia":

                await bot.send_message(

                    uid,

                    f"🔪 **Ти МАФІЯ!**\n\n"

                    f"👥 **Ваша команда:**\n"
                    f"{mafia_team_text}\n\n"

                    "🎯 **Кого вбиваємо?**\n\n"

                    f"{get_alive_list_text()}",

                    reply_markup=get_mafia_keyboard(
                        game["players"]
                    )
                )

            elif p["role"] == "doctor":

                await bot.send_message(

                    uid,

                    "🩺 **Ти ЛІКАР!**\n\n"

                    "Ти можеш врятувати "
                    "гравця від кулі мафії.\n\n"

                    "⚠️ Себе можна лікувати "
                    "лише **1 раз за гру**.\n\n"

                    f"{get_alive_list_text()}",

                    reply_markup=get_doctor_keyboard(
                        game["players"]
                    )
                )

            elif p["role"] == "sheriff":

                await bot.send_message(

                    uid,

                    "🕵️ **Ти ШЕРИФ!**\n\n"

                    "Кожної ночі ти можеш "
                    "сам вирішити, що робити:\n\n"

                    "🔍 перевірити гравця;\n"
                    "🔫 вистрілити в гравця;\n"
                    "💤 нічого не робити.\n\n"

                    "⚠️ Ти можеш перевіряти "
                    "кожної ночі без обмежень.",

                    reply_markup=get_sheriff_keyboard(
                        game["players"],
                        uid
                    )
                )

            elif p["role"] == "lucky":

                await bot.send_message(

                    uid,

                    "🍀 **Ти ЩАСЛИВЧИК!**\n\n"

                    "Якщо мафія обере тебе "
                    "своєю жертвою, ти можеш "
                    "дивом вижити під час "
                    "першого замаху.\n\n"

                    f"{get_alive_list_text()}"
                )

            else:

                await bot.send_message(

                    uid,

                    "😇 **Ти МИРНИЙ ЖИТЕЛЬ!**\n\n"

                    "Спи спокійно.\n"
                    "Вдень обговорюй та голосуй.\n\n"

                    f"{get_alive_list_text()}"
                )

        except Exception as e:

            print(
                f"Не вдалося написати "
                f"{uid}: {e}"
            )

            failed_private.append(
                p["name"]
            )

    # -----------------------------------------------------
    # ПОПЕРЕДЖЕННЯ
    # -----------------------------------------------------

    if failed_private:

        await bot.send_message(

            game["chat_id"],

            "⚠️ **УВАГА!**\n\n"

            "Не вдалося відправити роль "
            "деяким гравцям у ЛС:\n\n"

            +
            "\n".join(
                f"• {name}"
                for name in failed_private
            )

            +

            "\n\n👉 Вони повинні відкрити "
            "бота та натиснути /start."
        )

    # -----------------------------------------------------
    # ТАЙМЕР НОЧІ
    # -----------------------------------------------------

    game["timer_task"] = asyncio.create_task(
        night_timer()
    )


# =========================================================
# 11. ТАЙМЕР НОЧІ
# =========================================================

async def night_timer():

    try:

        await asyncio.sleep(40)

        if game["status"] == "night":

            await resolve_night()

    except asyncio.CancelledError:

        pass


# =========================================================
# 12. НІЧНІ ДІЇ
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
            "❌ Зараз нічні дії недоступні!",
            show_alert=True
        )

        return

    user_id = callback.from_user.id

    player = game["players"].get(
        user_id
    )

    if not player:

        await callback.answer(
            "❌ Ти не береш участі у грі!",
            show_alert=True
        )

        return

    if not player["alive"]:

        await callback.answer(
            "❌ Ти мертвий!",
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
                "❌ Це дія тільки мафії!",
                show_alert=True
            )

            return

        target_val = (
            "skip"
            if data[1] == "skip"
            else int(data[1])
        )

        if target_val != "skip":

            target_player = game["players"].get(
                target_val
            )

            if (
                not target_player
                or not target_player["alive"]
            ):

                await callback.answer(
                    "❌ Гравець уже мертвий!",
                    show_alert=True
                )

                return

            if target_player["role"] == "mafia":

                await callback.answer(
                    "❌ Мафію вбивати не можна!",
                    show_alert=True
                )

                return

        game["mafia_votes"][
            user_id
        ] = target_val

        if target_val == "skip":

            choice_text = (
                "💤 Ви обрали "
                "нікого не вбивати."
            )

        else:

            target_name = game["players"][
                target_val
            ]["name"]

            choice_text = (
                f"🎯 Ваша ціль: "
                f"**{target_name}**"
            )


    # =====================================================
    # ЛІКАР
    # =====================================================

    elif action == "heal":

        if player["role"] != "doctor":

            await callback.answer(
                "❌ Це дія тільки лікаря!",
                show_alert=True
            )

            return

        if data[1] == "skip":

            game["doctor_target"] = "skip"

            choice_text = (
                "💤 Цієї ночі "
                "ви нікого не лікуєте."
            )

        else:

            target_id = int(data[1])

            target_player = game["players"].get(
                target_id
            )

            if (
                not target_player
                or not target_player["alive"]
            ):

                await callback.answer(
                    "❌ Гравець уже мертвий!",
                    show_alert=True
                )

                return

            # Самолікування
            if target_id == user_id:

                if player["self_heals_used"] >= 1:

                    await callback.answer(
                        "❌ Самолікування вже використано!",
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
    # ЛІКАР — ПРОПУСК
    # =====================================================

    elif action == "heal" and data[1] == "skip":

        game["doctor_target"] = "skip"

        choice_text = (
            "💤 Цієї ночі "
            "ви нікого не лікуєте."
        )


    # =====================================================
    # ШЕРИФ
    # =====================================================

    elif action in ["check", "shot"]:

        if player["role"] != "sheriff":

            await callback.answer(
                "❌ Це дія тільки шерифа!",
                show_alert=True
            )

            return

        target_id = int(data[1])

        target_player = game["players"].get(
            target_id
        )

        if (
            not target_player
            or not target_player["alive"]
        ):

            await callback.answer(
                "❌ Гравець уже мертвий!",
                show_alert=True
            )

            return

        # -------------------------------------------------
        # ПЕРЕВІРКА
        # -------------------------------------------------

        if action == "check":

            target_role = target_player["role"]

            if target_role == "mafia":

                result = "МАФІЯ 🔪"

            else:

                result = "НЕ МАФІЯ 😇"

            await callback.message.answer(

                f"🔍 **Результат перевірки**\n\n"
                f"Гравець: "
                f"**{target_player['name']}**\n"
                f"Результат: **{result}**"
            )

            game["sheriff_target"] = target_id

            game["sheriff_action_done"] = True

            choice_text = (
                f"🔍 Перевірено: "
                f"**{target_player['name']}**\n"
                f"Результат: **{result}**"
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
                f"🔫 Ви вистрілили в "
                f"**{target_player['name']}**"
            )


    # =====================================================
    # ШЕРИФ — НІЧОГО НЕ РОБИТИ
    # =====================================================

    elif action == "sheriff":

        if player["role"] != "sheriff":

            await callback.answer(
                "❌ Це дія тільки шерифа!",
                show_alert=True
            )

            return

        game["sheriff_target"] = None
        game["sheriff_shot"] = None
        game["sheriff_action_done"] = True

        choice_text = (
            "💤 Цієї ночі "
            "ви нічого не робите."
        )


    # =====================================================
    # ЗАКРИТИ КНОПКИ
    # =====================================================

    try:

        await callback.message.edit_text(
            choice_text,
            reply_markup=None
        )

    except Exception:
        pass

    await callback.answer(
        "✅ Вибір збережено!"
    )

    await check_night_actions()


# =========================================================
# 13. ПЕРЕВІРКА ЧИ ВСІ ЗРОБИЛИ ДІЇ
# =========================================================

async def check_night_actions():

    if game["status"] != "night":
        return

    # Мафія
    alive_mafias = [

        uid

        for uid, p
        in game["players"].items()

        if (
            p["alive"]
            and p["role"] == "mafia"
        )
    ]

    mafia_done = all(
        uid in game["mafia_votes"]
        for uid in alive_mafias
    )

    # Лікар
    doctor_alive = any(

        p["alive"]
        and p["role"] == "doctor"

        for p in game["players"].values()
    )

    doctor_done = (
        game["doctor_target"] is not None
        if doctor_alive
        else True
    )

    # Шериф
    sheriff_alive = any(

        p["alive"]
        and p["role"] == "sheriff"

        for p in game["players"].values()
    )

    sheriff_done = (
        game["sheriff_action_done"]
        if sheriff_alive
        else True
    )

    if (
        mafia_done
        and doctor_done
        and sheriff_done
    ):

        cancel_timer()

        await resolve_night()


# =========================================================
# 14. РОЗВ'ЯЗАННЯ НОЧІ
# =========================================================

async def resolve_night():

    if game["status"] != "night":
        return

    cancel_timer()

    victim = None

    # -----------------------------------------------------
    # ВИБІР МАФІЇ
    # -----------------------------------------------------

    alive_mafias_count = sum(

        1

        for p in game["players"].values()

        if (
            p["alive"]
            and p["role"] == "mafia"
        )
    )

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
                )
                + 1
            )

    if (
        alive_mafias_count > 0
        and skip_count == alive_mafias_count
    ):

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

    doctor_target = game[
        "doctor_target"
    ]

    sheriff_shot = game[
        "sheriff_shot"
    ]

    text = (
        "🌅 **РАНОК У МІСТІ!**\n\n"
    )


    # =====================================================
    # КУЛЯ МАФІЇ
    # =====================================================

    if victim and victim != "skip":

        victim_player = game["players"].get(
            victim
        )

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

                text += (
                    f"🍀 Мафія стріляла в "
                    f"**{victim_player['name']}**, "
                    "але Щасливчик дивом "
                    "вижив!\n"
                )

            # Звичайна смерть
            else:

                victim_player["alive"] = False

                role_name = ROLE_ICONS.get(
                    victim_player["role"],
                    victim_player["role"]
                )

                text += (
                    f"💀 Мафія вбила "
                    f"**{victim_player['name']}**!\n"
                    f"Його роль: **{role_name}** 🪦\n"
                )

    else:

        text += (
            "🌙 Цієї ночі мафія "
            "нікого не вбила.\n"
        )


    # =====================================================
    # ПОСТРІЛ ШЕРИФА
    # =====================================================

    if sheriff_shot:

        shot_player = game["players"].get(
            sheriff_shot
        )

        if (
            shot_player
            and shot_player["alive"]
        ):

            # Якщо Лікар лікував саме цю людину
            if sheriff_shot == doctor_target:

                text += (
                    f"🩺 Лікар також врятував "
                    f"**{shot_player['name']}** "
                    "від пострілу шерифа!\n"
                )

            else:

                shot_player["alive"] = False

                role_name = ROLE_ICONS.get(
                    shot_player["role"],
                    shot_player["role"]
                )

                text += (
                    f"🔫 Шериф застрелив "
                    f"**{shot_player['name']}**!\n"
                    f"Його роль: **{role_name}** 🪦\n"
                )


    text += (
        "\n"
        +
        get_alive_list_text()
    )


    # =====================================================
    # ДЕНЬ
    # =====================================================

    await mute_chat(
        game["chat_id"],
        False
    )

    await send_phase_photo(
        game["chat_id"],
        "day",
        text
    )


    if await check_win_condition():

        return


    game["status"] = "discussion"

    await bot.send_message(

        game["chat_id"],

        "🗣 **ЧАТ ВІДКРИТО!**\n\n"
        "Обговорення — **1 хвилина** ⏳"
    )

    cancel_timer()

    game["timer_task"] = asyncio.create_task(
        discussion_timer()
    )


# =========================================================
# 15. ТАЙМЕР ОБГОВОРЕННЯ
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
# 16. ГОЛОСУВАННЯ
# =========================================================

async def start_voting(
    candidate_ids=None
):

    if game["status"] not in [
        "discussion",
        "voting"
    ]:
        return

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

            "⏳ У вас **30 секунд** "
            "на вирішальне голосування."
        )

    else:

        msg_text = (

            "⚖️ **ЧАС ГОЛОСУВАННЯ!**\n\n"

            "Обирайте підозрюваного:\n\n"

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
# 17. ТАЙМЕР ГОЛОСУВАННЯ
# =========================================================

async def voting_timer():

    try:

        await asyncio.sleep(30)

        if game["status"] == "voting":

            await resolve_voting()

    except asyncio.CancelledError:

        pass


# =========================================================
# 18. ГОЛОС
# =========================================================

@dp.callback_query(
    F.data.startswith("vote_")
)
async def cb_vote(
    callback: CallbackQuery
):

    if game["status"] != "voting":

        await callback.answer(
            "❌ Голосування завершене!",
            show_alert=True
        )

        return

    voter_id = callback.from_user.id

    voter = game["players"].get(
        voter_id
    )

    if (
        not voter
        or not voter["alive"]
    ):

        await callback.answer(
            "❌ Мертві не голосують!",
            show_alert=True
        )

        return

    target_id = int(
        callback.data.split("_")[1]
    )

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

    game["votes"][
        voter_id
    ] = target_id

    target_name = target_player["name"]

    try:

        await callback.message.edit_text(

            f"🗳 Ваш голос за "
            f"**{target_name}** "
            "прийнято.\n\n"
            "Очікуємо інших...",

            reply_markup=None
        )

    except Exception:
        pass

    await callback.answer(
        "✅ Голос прийнято!"
    )

    alive_ids = {

        uid

        for uid, p
        in game["players"].items()

        if p["alive"]
    }

    voted_count = sum(

        1

        for voter_id
        in game["votes"]

        if voter_id in alive_ids
    )

    if voted_count >= len(alive_ids):

        cancel_timer()

        await resolve_voting()


# =========================================================
# 19. РЕЗУЛЬТАТИ ГОЛОСУВАННЯ
# =========================================================

async def resolve_voting():

    if game["status"] != "voting":
        return

    cancel_timer()

    alive_ids = {

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
            voter in alive_ids
            and target in alive_ids
        ):

            vote_counts[target] = (
                vote_counts.get(
                    target,
                    0
                )
                + 1
            )

    text = (
        "📊 **РЕЗУЛЬТАТИ "
        "ГОЛОСУВАННЯ:**\n\n"
    )

    # Ніхто не голосував
    if not vote_counts:

        text += (
            "Ніхто не проголосував."
        )

        game[
            "runoff_candidates"
        ].clear()

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
    # ПЕРЕСТРІЛКА
    # =====================================================

    if game["runoff_candidates"]:

        if len(candidates) > 1:

            text += (
                "⚖️ **ЗНОВУ НІЧИЯ!**\n\n"
                "Місто вирішило "
                "**нікого не виганяти**."
            )

            game[
                "runoff_candidates"
            ].clear()

            await finalize_voting_round(
                text
            )

            return


    # =====================================================
    # ПЕРША НІЧИЯ
    # =====================================================

    if len(candidates) > 1:

        game[
            "runoff_candidates"
        ] = candidates

        names = ", ".join(

            f"{game['players'][uid]['number']}. "
            f"{game['players'][uid]['name']}"

            for uid in candidates
        )

        text += (

            "⚖️ **НІЧИЯ!**\n\n"

            f"Однакова кількість голосів:\n"
            f"**{names}**\n\n"

            "Проводимо додаткове "
            "голосування між ними."
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
    # ВИГНАННЯ
    # =====================================================

    exiled = candidates[0]

    game["players"][
        exiled
    ]["alive"] = False

    role_key = game["players"][
        exiled
    ]["role"]

    role_name = ROLE_ICONS.get(
        role_key,
        role_key
    )

    text += (

        f"⚖️ Місто вигнало "
        f"**{game['players'][exiled]['name']}**.\n\n"

        f"Його роль: **{role_name}** 🪦"
    )

    game[
        "runoff_candidates"
    ].clear()

    await finalize_voting_round(
        text
    )


# =========================================================
# 20. ПЕРЕХІД ДО НОВОЇ НОЧІ
# =========================================================

async def finalize_voting_round(
    text: str
):

    text += (
        "\n\n"
        +
        get_alive_list_text()
    )

    await mute_chat(
        game["chat_id"],
        False
    )

    await bot.send_message(
        game["chat_id"],
        text
    )

    if await check_win_condition():

        return

    # НОВА НІЧ
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

        "🌙 **МІСТО ЗНОВУ ЗАСИНАЄ...**\n\n"
        "🔒 Чат заблоковано."
    )

    mafia_team_text = (
        get_mafia_team_str()
    )

    # -----------------------------------------------------
    # ДІЇ ДЛЯ ЖИВИХ ГРАВЦІВ
    # -----------------------------------------------------

    for uid, p in game[
        "players"
    ].items():

        if not p["alive"]:
            continue

        try:

            if p["role"] == "mafia":

                await bot.send_message(

                    uid,

                    "🔪 **МАФІЯ**\n\n"

                    f"👥 Ваша команда:\n"
                    f"{mafia_team_text}\n\n"

                    "🎯 Кого вбиваємо?\n\n"

                    +
                    get_alive_list_text(),

                    reply_markup=get_mafia_keyboard(
                        game["players"]
                    )
                )

            elif p["role"] == "doctor":

                await bot.send_message(

                    uid,

                    "🩺 **ЛІКАР**\n\n"

                    "Кого будеш лікувати?\n\n"

                    "⚠️ Себе можна лікувати "
                    "лише 1 раз за гру.\n\n"

                    +
                    get_alive_list_text(),

                    reply_markup=get_doctor_keyboard(
                        game["players"]
                    )
                )

            elif p["role"] == "sheriff":

                await bot.send_message(

                    uid,

                    "🕵️ **ШЕРИФ**\n\n"

                    "Цієї ночі ти можеш:\n\n"

                    "🔍 перевірити когось;\n"
                    "🔫 вистрілити;\n"
                    "💤 нічого не робити.\n\n"

                    "♻️ Наступної ночі "
                    "вибір знову буде доступний.",

                    reply_markup=get_sheriff_keyboard(
                        game["players"],
                        uid
                    )
                )

        except Exception as e:

            print(
                f"Помилка ЛС {uid}: {e}"
            )

    cancel_timer()

    game["timer_task"] = asyncio.create_task(
        night_timer()
    )


# =========================================================
# 21. ПЕРЕВІРКА ПЕРЕМОГИ
# =========================================================

async def check_win_condition():

    alive_mafia = sum(

        1

        for p in game["players"].values()

        if (
            p["alive"]
            and p["role"] == "mafia"
        )
    )

    alive_non_mafia = sum(

        1

        for p in game["players"].values()

        if (
            p["alive"]
            and p["role"] != "mafia"
        )
    )


    # -----------------------------------------------------
    # ПЕРЕМОГА МИРНИХ
    # -----------------------------------------------------

    if alive_mafia == 0:

        game["status"] = "finished"

        cancel_timer()

        summary_text = (

            "🎉 **ПЕРЕМОГА МИРНИХ!** 😇\n\n"

            "🔪 Всю мафію знищено!\n\n"

            +
            format_all_roles_summary()
        )

        await mute_chat(
            game["chat_id"],
            False
        )

        await bot.send_message(
            game["chat_id"],
            summary_text
        )

        return True


    # -----------------------------------------------------
    # ПЕРЕМОГА МАФІЇ
    # -----------------------------------------------------

    if alive_mafia >= alive_non_mafia:

        game["status"] = "finished"

        cancel_timer()

        summary_text = (

            "🔪 **ПЕРЕМОГА МАФІЇ!** 😈\n\n"

            "Мафія захопила місто!\n\n"

            +
            format_all_roles_summary()
        )

        await mute_chat(
            game["chat_id"],
            False
        )

        await bot.send_message(
            game["chat_id"],
            summary_text
        )

        return True

    return False


# =========================================================
# 22. ЗАПУСК
# =========================================================

async def main():

    print(
        "🎴 Бот Мафії запущено успішно!"
    )

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":
    asyncio.run(main())
