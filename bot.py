"""
Telegram-бот (приватний, домашній хостинг) — той самий рушій, що й CLI/GUI,
у чаті. Long-polling, без webhook. Нуль нової аналітики — лише ввід/вивід
навколо abit_parser.engine.

Запуск:
    python bot.py

Налаштування — .env у корені проєкту (див. .env.example):
    TELEGRAM_ABIT_BOT_TOKEN=...
    TELEGRAM_ABIT_ACCESS_CODE=...   (опційно, за замовчуванням доступ відкритий)
"""

import asyncio
import html
import logging
import os
import random
import sys
import time
from datetime import time as dtime
from typing import Dict, List, Optional, Set

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot_storage
from abit_parser.contract_analysis import ContractResult
from abit_parser.engine import AnalysisError, AnalysisResult, run_analysis, run_contract_chance

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_ABIT_BOT_TOKEN")
ACCESS_CODE = os.environ.get("TELEGRAM_ABIT_ACCESS_CODE") or None

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

CHECK_CODE, ASK_URL, ASK_SCORE, ASK_PRIORITY, ASK_FUNDING = range(5)

MESSAGE_LIMIT = 3500  # запас під ліміт Telegram у 4096 символів

# Стеження: фіксовані моменти доби (по системному годиннику машини), а не
# "раз на 12 год від старту процесу" — інакше графік "поплив" би після
# кожного рестарту служби (а вона перезапускається сама при краші).
# Час — свій для кожного chat_id (стабільний, але розкиданий по добі), щоб
# кілька підписників не били сайт одним залпом запитів о 9:00 й 21:00 разом.
TRACK_WINDOW_MINUTES = 12 * 60  # дві перевірки на добу, рівно через 12 год одна від одної


def _track_times_for_chat(chat_id: int) -> "list[dtime]":
    offset = abs(chat_id) % TRACK_WINDOW_MINUTES
    first = dtime(hour=offset // 60, minute=offset % 60)
    second_offset = (offset + TRACK_WINDOW_MINUTES) % (24 * 60)
    second = dtime(hour=second_offset // 60, minute=second_offset % 60)
    return [first, second]


JOKES_NOTHING_CHANGED = [
    "Можеш відпустити кнопку F5, списки не рушили з місця. Нічого нового.",
    "Якби за кожну перевірку без змін тобі додавали бал до НМТ, ти б уже був на бюджеті. Все стабільно.",
    "Твої конкуренти сплять (або плачуть). Тобі теж варто відпочити. Змін нуль.",
    "Ситуація під контролем. Ніхто твоє місце не вкрав (принаймні за останні 5 хвилин).",
    "ЄДЕБО лежить, конкуренти лежать, списки лежать. Все стабільно, змін немає.",
    "Я перевірив базу швидше, ніж у тебе вчергове сіпнулося око. Видихай, без змін.",
    "Твоє місце в рейтингу зараз таке ж непохитне, як черга за шаурмою. Нічого не змінилося.",
    "Спойлер: у новій серії вступної драми нічого не відбулося. Чекаємо далі.",
    "Я перевірив. Потім перевірив ще раз. Потім згадав, що я бот і не помиляюсь. Змін катма.",
    "Списки не оновилися. А навіть якби й оновилися, я б тобі сказав. Іди поїж.",
    "Тиша... Тільки чутно, як десь нервово клікають мишкою тисячі абітурієнтів. У нас без змін."
]
# Стан на рівні процесу (приватний бот для кількох людей — простих set/dict досить)
_verified_chats: Set[int] = set()
_active_chats: Set[int] = set()
_last_result: Dict[int, AnalysisResult] = {}
_last_contract: Dict[int, ContractResult] = {}


def _chunk_text(text: str, limit: int = MESSAGE_LIMIT) -> List[str]:
    lines = text.split("\n")
    chunks: List[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]


def _e(text: object) -> str:
    # quote=False: лапки/апострофи не треба ескейпити для HTML-режиму Telegram
    # (це не HTML-атрибут), лишаємо їх як є, щоб не показувались як &#x27;.
    return html.escape(str(text), quote=False)


def _person_word(n: int) -> str:
    """Відмінок іменника "особа" за числівником: 1 особа, 2-4 особи, 5+/0/11-14 осіб."""
    if n % 10 == 1 and n % 100 != 11:
        return "особа"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "особи"
    return "осіб"


STATUS_EXPLAIN = {
    "точно": "точно піде на вищий пріоритет — звільнить місце тут",
    "ймовірно": "ймовірно піде на вищий пріоритет",
    "лишається": "найімовірніше лишиться тут (реальний конкурент)",
    "невизначено": "не вдалось перевірити напевно",
}


def _explain_status(status: str) -> str:
    return STATUS_EXPLAIN.get(status, status)


def _scenario_block(optimistic: int, expected: float, pessimistic: int, limit: int, limit_label: str) -> str:
    return (
        f"🔴 Найгірший сценарій: {pessimistic}-те місце\n"
        f"🟡 Очікувано: {round(expected)}-те місце\n"
        f"🟢 Найкращий сценарій: {optimistic}-те місце\n"
        f"{limit_label}: {limit}"
    )


HELP_TEXT = (
    "🎓 <b>Що робить Калькулятор потужності</b>\n"
    "Показує не просто місце в рейтинговому списку, а реальний шанс пройти — з урахуванням того, що "
    "частина людей вище за балом можуть піти на свій вищий пріоритет і звільнити місце тут. Без ворожіння "
    "на кавовій гущі — тільки цифри з реальних даних сайту.\n\n"
    "<b>Як користуватись</b>\n"
    "1. /start — почати\n"
    "2. Надіслати посилання на напрям (abit-poisk.org.ua)\n"
    "3. Вказати свій бал\n"
    "4. Обрати пріоритет заяви на цю спеціальність (кнопками)\n"
    "5. Обрати бюджет чи контракт (кнопками)\n"
    "6. Дочекатись аналізу (до ~2 хв — бот перевіряє кожного конкурента вище окремо, тому не миттєво)\n\n"
    "<b>Що означають три сценарії</b>\n"
    "🔴 Найгірший — якщо ніхто з конкурентів вище нікуди не піде\n"
    "🟡 Очікувано — зважена оцінка, скільки з них реально підуть на вищий пріоритет\n"
    "🟢 Найкращий — якщо всі, хто може піти, підуть\n\n"
    "<b>Що таке «невизначено»</b>\n"
    "Частину людей не вдалось перевірити напевно — у когось іншого знайшлось таке саме ім'я та прізвище "
    "(і незрозуміло, хто з них саме той) або сайт не відповів. Це чесно показується окремо, а не ховається "
    "у «підуть» чи «лишаються».\n\n"
    "<b>Кнопки після результату</b>\n"
    "«Хто саме вище мене» — повний список конкурентів вище твого балу і що з кожним\n"
    "«Шанс на контракт» — той самий розрахунок, але для контрактних місць\n"
    "«Стежити» — раз на 12 год сам перерахую і напишу, якщо щось зміниться (а якщо ні — просто щось "
    "зажартую). /untrack — зупинити стеження\n\n"
    "Усе — детермінований розрахунок з реальних даних сайту, без ШІ.\n"
    "/cancel — скасувати поточний діалог."
)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


# ------------------------------------------------------------------ доступ


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    context.user_data.clear()

    if ACCESS_CODE and chat_id not in _verified_chats:
        await update.message.reply_text("Привіт! Цей бот приватний. Введи код доступу:")
        return CHECK_CODE

    return await _ask_url(update, context)


async def check_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text.strip() == ACCESS_CODE:
        _verified_chats.add(update.effective_chat.id)
        return await _ask_url(update, context)
    await update.message.reply_text("Невірний код. Спробуй ще раз:")
    return CHECK_CODE


async def _ask_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "👋 Привіт, тут Калькулятор потужності. Порахую реальний шанс пройти на бюджет (і на контракт) "
        "на обрану спеціальність — без істерики, самі цифри.\n\n"
        "Що саме і як рахується — /help.\n\n"
        "Надішли посилання на напрям з abit-poisk.org.ua, напр.:\n"
        "https://abit-poisk.org.ua/rate2026/direction/1613482"
    )
    return ASK_URL


# --------------------------------------------------------------- діалог вводу


async def ask_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    if "abit-poisk.org.ua" not in url:
        await update.message.reply_text("Це не схоже на посилання abit-poisk.org.ua. Спробуй ще раз:")
        return ASK_URL
    context.user_data["url"] = url
    await update.message.reply_text("Який у тебе конкурсний бал на цю спеціальність?")
    return ASK_SCORE


async def ask_priority(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(",", ".")
    try:
        score = float(text)
        if score <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Бал має бути числом, напр. 185.077. Спробуй ще раз:")
        return ASK_SCORE
    context.user_data["score"] = score

    keyboard = [
        [InlineKeyboardButton(str(p), callback_data=f"pr:{p}") for p in range(1, 6)],
        [InlineKeyboardButton(str(p), callback_data=f"pr:{p}") for p in range(6, 10)],
    ]
    await update.message.reply_text(
        "Яким пріоритетом у заяві вказана саме ця спеціальність? (число від 1 до 9)",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ASK_PRIORITY


async def ask_funding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    priority = int(query.data.split(":")[1])
    context.user_data["priority"] = priority

    keyboard = [
        [
            InlineKeyboardButton("Бюджет (Б)", callback_data="fn:Б"),
            InlineKeyboardButton("Контракт (К)", callback_data="fn:К"),
        ]
    ]
    await query.edit_message_text(
        f"Пріоритет: {priority}. Ця заява — на бюджет чи на контракт?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ASK_FUNDING


# ------------------------------------------------------------------- аналіз


async def run_analysis_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    funding = query.data.split(":")[1]
    context.user_data["funding"] = funding

    chat_id = update.effective_chat.id
    if chat_id in _active_chats:
        await query.edit_message_text("Вже рахую попередній аналіз для тебе — зачекай на результат.")
        return ConversationHandler.END

    url = context.user_data["url"]
    score = context.user_data["score"]
    priority = context.user_data["priority"]

    status_msg = await query.edit_message_text("Аналізую… (до ~2 хв)")
    _active_chats.add(chat_id)

    loop = asyncio.get_running_loop()
    last_edit = [0.0]

    def on_progress(done: int, total: int, name: str) -> None:
        now = time.monotonic()
        if now - last_edit[0] < 3.0 and done != total:
            return
        last_edit[0] = now
        text = f"Аналізую… {done}/{total}: {_e(name)}"
        _run_in_loop_and_wait(_safe_edit(status_msg, text), loop)

    try:
        result = await asyncio.to_thread(
            run_analysis, url, score, priority, funding, cross_check=True, on_progress=on_progress
        )
    except AnalysisError as e:
        await _safe_edit(status_msg, f"Помилка: {_e(e)}")
        return ConversationHandler.END
    except Exception as e:
        logger.exception("run_analysis впав")
        await _safe_edit(status_msg, f"Неочікувана помилка: {_e(e)}")
        return ConversationHandler.END
    finally:
        _active_chats.discard(chat_id)

    _last_result[chat_id] = result
    _last_contract.pop(chat_id, None)

    text, keyboard = _format_budget_message(result)
    await _safe_edit(status_msg, text, reply_markup=keyboard)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Скасовано. /start (з меню або текстом) — щоб почати заново.")
    return ConversationHandler.END


# ------------------------------------------------------------------ форматування


def _format_budget_message(result: AnalysisResult) -> "tuple[str, Optional[InlineKeyboardMarkup]]":
    lines = [f"📊 <b>{_e(result.stats.title)}</b>", ""]

    cc = result.cross_check
    if cc is not None:
        lines.append(_scenario_block(cc.optimistic_bound, cc.expected_count, cc.pessimistic_bound, cc.m, "Бюджетних місць"))
        lines.append("")
        lines.append(f"<b>Висновок: {_e(cc.verdict)}</b>")
        lines.append(
            f"Орієнтовний шанс: {cc.pessimistic_chance * 100:.0f}–{cc.chance * 100:.0f}–"
            f"{cc.optimistic_chance * 100:.0f}% (від найгіршого до найкращого сценарію, не гарантія)"
        )
        if cc.unknown_count:
            lines.append("")
            lines.append(
                f"❓ {cc.unknown_count} {_person_word(cc.unknown_count)} не вдалось перевірити напевно "
                "(в іншої людини знайшлось таке саме ім'я, або сайт не відповів) — чесний невідомий "
                "залишок, не врахований у жодну сторону."
            )
    else:
        v = result.verdict
        lines.append(f"Місце в списку: {v.optimistic_bound}–{v.pessimistic_bound}-те — бюджетних місць {v.m}")
        lines.append(f"<b>Висновок: {_e(v.verdict)}</b>")

    for w in result.warnings:
        lines.append(f"⚠ {_e(w)}")

    row1 = []
    if cc is not None and cc.assessments:
        row1.append(InlineKeyboardButton("📋 Хто саме вище мене", callback_data="details"))
    if result.stats.k is not None:
        row1.append(InlineKeyboardButton("💳 Шанс на контракт", callback_data="contract"))
    else:
        lines.append("")
        lines.append("(шанс на контракт недоступний — сайт не показує кількість контрактних місць для цього напряму)")

    rows = [row1] if row1 else []
    if cc is not None:
        rows.append([InlineKeyboardButton("🔔 Стежити (раз на 12 год)", callback_data="track")])

    keyboard = InlineKeyboardMarkup(rows) if rows else None
    return "\n".join(lines), keyboard


def _format_details_text(result: AnalysisResult) -> str:
    cc = result.cross_check
    if cc is None or not cc.assessments:
        return "Деталей немає."
    lines = ["<b>Хто з абітурієнтів вище твого балу і що з ними ймовірно буде:</b>", ""]
    for a in cc.assessments:
        applicant = a.competitor.applicant
        target = ""
        if a.best_choice:
            bc = a.best_choice
            target = f"\n   → на пріоритетнішому виборі: {_e(bc.university)} / {_e(bc.specialty)} (там {bc.position}-те місце)"
        lines.append(
            f"• <b>{_e(applicant.name)}</b> — бал {applicant.score}, тут пріоритет {applicant.priority}\n"
            f"   {_e(_explain_status(a.status))}{target}"
        )
    return "\n".join(lines)


def _format_contract_message(cr: ContractResult) -> "tuple[str, Optional[InlineKeyboardMarkup]]":
    lines = [
        "💳 <b>Шанс на контракт</b>",
        "",
        _scenario_block(cr.optimistic_bound, cr.expected_count, cr.pessimistic_bound, cr.k, "Контрактних місць"),
        "",
        f"<b>Висновок: {_e(cr.verdict)}</b>",
        f"Орієнтовний шанс: {cr.pessimistic_chance * 100:.0f}–{cr.chance * 100:.0f}–"
        f"{cr.optimistic_chance * 100:.0f}% (від найгіршого до найкращого сценарію, не гарантія)",
    ]
    if cr.unknown_count:
        lines.append("")
        lines.append(
            f"❓ {cr.unknown_count} {_person_word(cr.unknown_count)} не вдалось перевірити напевно "
            "(в іншої людини знайшлось таке саме ім'я, або сайт не відповів) — чесний невідомий "
            "залишок, не врахований у жодну сторону."
        )

    keyboard = None
    if cr.assessments:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📋 Хто саме вище мене (контракт)", callback_data="contract_details")]]
        )
    return "\n".join(lines), keyboard


def _format_contract_details_text(cr: ContractResult) -> str:
    if not cr.assessments:
        return "Деталей немає."
    lines = ["<b>Хто в контрактному пулі вище твого балу:</b>", ""]
    for a in cr.assessments:
        applicant = a.member.applicant
        origin = (
            "має тут окрему заявку на контракт"
            if a.member.origin == "контракт"
            else "заявка на бюджет тут не пройшла, автоматично в контракті"
        )
        target = ""
        if a.best_choice:
            bc = a.best_choice
            target = f"\n   → на пріоритетнішому виборі: {_e(bc.university)} / {_e(bc.specialty)} (там {bc.position}-те місце)"
        lines.append(
            f"• <b>{_e(applicant.name)}</b> — бал {applicant.score} ({_e(origin)})\n"
            f"   {_e(_explain_status(a.status))}{target}"
        )
    return "\n".join(lines)


def _snapshot_from_cc(cc) -> dict:
    return {
        "verdict": cc.verdict,
        "optimistic_bound": cc.optimistic_bound,
        "pessimistic_bound": cc.pessimistic_bound,
        "expected_count": round(cc.expected_count, 1),  # округлення — щоб шум 45.81 vs 45.83 не тригерив "зміну"
        "m": cc.m,
    }


def _schedule_tracking(job_queue, chat_id: int) -> None:
    _unschedule_tracking(job_queue, chat_id)
    for i, t in enumerate(_track_times_for_chat(chat_id)):
        job_queue.run_daily(periodic_check, time=t, chat_id=chat_id, name=f"track-{chat_id}-{i}", data=chat_id)


def _unschedule_tracking(job_queue, chat_id: int) -> None:
    for job in job_queue.get_jobs_by_name(f"track-{chat_id}-0") + job_queue.get_jobs_by_name(f"track-{chat_id}-1"):
        job.schedule_removal()


async def on_track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    result = _last_result.get(chat_id)
    if result is None or result.cross_check is None:
        await query.message.reply_text("Спочатку зроби аналіз — нема за чим стежити.")
        return

    sub = {
        "url": result.url,
        "score": result.score,
        "priority": result.priority,
        "funding": result.funding,
        "snapshot": _snapshot_from_cc(result.cross_check),
    }
    bot_storage.set_subscription(chat_id, sub)
    _schedule_tracking(context.job_queue, chat_id)

    times = ", ".join(t.strftime("%H:%M") for t in _track_times_for_chat(chat_id))
    await query.message.reply_text(
        f"🔔 Стежу за цим напрямом. Перевірятиму щодня о {times} і напишу, якщо щось зміниться "
        "(а якщо ні — просто щось зажартую, щоб ти знав, що я живий).\n/untrack — зупинити."
    )


async def untrack_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if bot_storage.get_subscription(chat_id) is None:
        await update.message.reply_text("Я й так за тобою не стежу.")
        return
    bot_storage.remove_subscription(chat_id)
    _unschedule_tracking(context.job_queue, chat_id)
    await update.message.reply_text("Гаразд, більше не стежу. /start — почати новий аналіз.")


async def periodic_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data
    sub = bot_storage.get_subscription(chat_id)
    if sub is None:
        return  # відписались між плануванням і спрацюванням джоби

    try:
        result = await asyncio.to_thread(
            run_analysis, sub["url"], sub["score"], sub["priority"], sub["funding"], cross_check=True
        )
    except Exception as e:
        logger.warning("periodic_check: аналіз впав для %s: %s", chat_id, e)
        return

    cc = result.cross_check
    if cc is None:
        return  # без v2-даних нема з чим порівнювати — тихо пропускаємо цей цикл

    _last_result[chat_id] = result
    new_snapshot = _snapshot_from_cc(cc)
    old_snapshot = sub.get("snapshot")

    if old_snapshot is not None and old_snapshot == new_snapshot:
        text = random.choice(JOKES_NOTHING_CHANGED)
    else:
        budget_text, _ = _format_budget_message(result)
        text = "🔔 <b>Дещо змінилось!</b>\n\n" + budget_text

    sub["snapshot"] = new_snapshot
    bot_storage.set_subscription(chat_id, sub)

    try:
        await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning("periodic_check: не вдалось надіслати повідомлення %s: %s", chat_id, e)


async def _safe_edit(message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.warning("edit_text BadRequest: %s", e)
    except Exception as e:
        logger.warning("edit_text failed: %s", e)


def _run_in_loop_and_wait(coro, loop: asyncio.AbstractEventLoop, timeout: float = 10.0) -> None:
    """Планує корутину на event loop і ЧЕКАЄ на завершення з робочого потоку.

    Без цього очікування 'дочекайся' — фінальний edit (результат аналізу) і
    останній проміжний прогрес-edit можуть виконатись у зворотному порядку
    (обидва async_coroutine_threadsafe без .result() — fire-and-forget, без
    гарантії, хто відпрацює першим), і фінальне повідомлення тихо
    перезаписується застряглим "42/42" — саме такий баг і був спіймано."""
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        future.result(timeout=timeout)
    except Exception as e:
        logger.warning("progress edit failed: %s", e)


# --------------------------------------------------------------- кнопки після діалогу


async def on_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    result = _last_result.get(chat_id)
    if result is None:
        await query.message.reply_text("Немає збереженого аналізу — напиши /start.")
        return
    for chunk in _chunk_text(_format_details_text(result)):
        await query.message.reply_text(chunk, parse_mode=ParseMode.HTML)


async def on_contract(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    result = _last_result.get(chat_id)
    if result is None:
        await query.message.reply_text("Немає збереженого аналізу — напиши /start.")
        return
    if chat_id in _active_chats:
        await query.message.reply_text("Вже рахую попередній запит для тебе — зачекай.")
        return

    status_msg = await query.message.reply_text("Рахую шанс на контракт… (до ~1 хв)")
    _active_chats.add(chat_id)

    loop = asyncio.get_running_loop()
    last_edit = [0.0]

    def on_progress(done: int, total: int, name: str) -> None:
        now = time.monotonic()
        if now - last_edit[0] < 3.0 and done != total:
            return
        last_edit[0] = now
        _run_in_loop_and_wait(_safe_edit(status_msg, f"Рахую контракт… {done}/{total}: {_e(name)}"), loop)

    try:
        cr = await asyncio.to_thread(run_contract_chance, result, on_progress=on_progress)
    except AnalysisError as e:
        await _safe_edit(status_msg, f"Помилка: {_e(e)}")
        return
    except Exception as e:
        logger.exception("run_contract_chance впав")
        await _safe_edit(status_msg, f"Неочікувана помилка: {_e(e)}")
        return
    finally:
        _active_chats.discard(chat_id)

    _last_contract[chat_id] = cr
    text, keyboard = _format_contract_message(cr)
    await _safe_edit(status_msg, text, reply_markup=keyboard)


async def on_contract_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    cr = _last_contract.get(chat_id)
    if cr is None:
        await query.message.reply_text("Немає збереженого розрахунку контракту.")
        return
    for chunk in _chunk_text(_format_contract_details_text(cr)):
        await query.message.reply_text(chunk, parse_mode=ParseMode.HTML)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Необроблена помилка", exc_info=context.error)


async def _post_init(application: Application) -> None:
    # Меню команд біля поля вводу в Telegram — щоб не вписувати /start руками.
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Почати новий аналіз"),
            BotCommand("help", "Що це і як користуватись"),
            BotCommand("cancel", "Скасувати поточний діалог"),
            BotCommand("untrack", "Зупинити стеження"),
        ]
    )

    # Джоби стеження живуть тільки в пам'яті — після рестарту служби (а вона
    # рестартує сама при краші) їх треба перереєструвати з того, що збережено
    # на диску, інакше підписки мовчки перестають працювати.
    restored = 0
    for chat_id_str in bot_storage.load_all():
        _schedule_tracking(application.job_queue, int(chat_id_str))
        restored += 1
    if restored:
        logger.info("Відновлено %d підписок на стеження", restored)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_ABIT_BOT_TOKEN не встановлено. Створи .env (див. .env.example) з токеном від @BotFather."
        )

    # concurrent_updates=True — без цього PTB обробляє оновлення послідовно по
    # всьому боту: довгий аналіз одного юзера заблокував би повідомлення інших.
    application = (
        Application.builder().token(BOT_TOKEN).concurrent_updates(True).post_init(_post_init).build()
    )

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHECK_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_code)],
            ASK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_score)],
            ASK_SCORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_priority)],
            ASK_PRIORITY: [CallbackQueryHandler(ask_funding, pattern=r"^pr:\d$")],
            ASK_FUNDING: [CallbackQueryHandler(run_analysis_step, pattern=r"^fn:[БК]$")],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("untrack", untrack_command))
    application.add_handler(CallbackQueryHandler(on_details, pattern="^details$"))
    application.add_handler(CallbackQueryHandler(on_contract, pattern="^contract$"))
    application.add_handler(CallbackQueryHandler(on_contract_details, pattern="^contract_details$"))
    application.add_handler(CallbackQueryHandler(on_track, pattern="^track$"))
    application.add_error_handler(on_error)

    logger.info("Бот запускається (long-polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
