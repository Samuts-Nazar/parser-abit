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
from bot_texts import t, t_list

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
        return t("person_word.one")
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return t("person_word.few")
    return t("person_word.many")


def _explain_status(status: str) -> str:
    try:
        return t(f"status.{status}")
    except KeyError:
        return status


def _scenario_block(optimistic: int, expected: float, pessimistic: int, limit: int, limit_label: str) -> str:
    return "\n".join(
        [
            t("budget_result.scenario_worst", value=pessimistic),
            t("budget_result.scenario_expected", value=round(expected)),
            t("budget_result.scenario_best", value=optimistic),
            t("budget_result.scenario_limit", label=limit_label, value=limit),
        ]
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(t("help.text"), parse_mode=ParseMode.HTML)


# ------------------------------------------------------------------ доступ


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    context.user_data.clear()

    if ACCESS_CODE and chat_id not in _verified_chats:
        await update.message.reply_text(t("greeting.access_code_prompt"))
        return CHECK_CODE

    return await _ask_url(update, context)


async def check_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text.strip() == ACCESS_CODE:
        _verified_chats.add(update.effective_chat.id)
        return await _ask_url(update, context)
    await update.message.reply_text(t("greeting.access_code_wrong"))
    return CHECK_CODE


async def _ask_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(t("greeting.welcome"))
    return ASK_URL


# --------------------------------------------------------------- діалог вводу


async def ask_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()
    if "abit-poisk.org.ua" not in url:
        await update.message.reply_text(t("input.invalid_url"))
        return ASK_URL
    context.user_data["url"] = url
    await update.message.reply_text(t("input.ask_score"))
    return ASK_SCORE


async def ask_priority(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(",", ".")
    try:
        score = float(text)
        if score <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(t("input.invalid_score"))
        return ASK_SCORE
    context.user_data["score"] = score

    keyboard = [
        [InlineKeyboardButton(str(p), callback_data=f"pr:{p}") for p in range(1, 6)],
        [InlineKeyboardButton(str(p), callback_data=f"pr:{p}") for p in range(6, 10)],
    ]
    await update.message.reply_text(
        t("input.ask_priority"),
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
            InlineKeyboardButton(t("input.funding_budget_button"), callback_data="fn:Б"),
            InlineKeyboardButton(t("input.funding_contract_button"), callback_data="fn:К"),
        ]
    ]
    await query.edit_message_text(
        t("input.ask_funding", priority=priority),
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
        await query.edit_message_text(t("analysis.already_running"))
        return ConversationHandler.END

    url = context.user_data["url"]
    score = context.user_data["score"]
    priority = context.user_data["priority"]

    status_msg = await query.edit_message_text(t("analysis.starting"))
    _active_chats.add(chat_id)

    loop = asyncio.get_running_loop()
    last_edit = [0.0]

    def on_progress(done: int, total: int, name: str) -> None:
        now = time.monotonic()
        if now - last_edit[0] < 3.0 and done != total:
            return
        last_edit[0] = now
        text = t("analysis.progress", done=done, total=total, name=_e(name))
        _run_in_loop_and_wait(_safe_edit(status_msg, text), loop)

    try:
        result = await asyncio.to_thread(
            run_analysis, url, score, priority, funding, cross_check=True, on_progress=on_progress
        )
    except AnalysisError as e:
        await _safe_edit(status_msg, t("analysis.error", error=_e(e)))
        return ConversationHandler.END
    except Exception as e:
        logger.exception("run_analysis впав")
        await _safe_edit(status_msg, t("analysis.unexpected_error", error=_e(e)))
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
    await update.message.reply_text(t("input.cancelled"))
    return ConversationHandler.END


# ------------------------------------------------------------------ форматування


def _format_budget_message(result: AnalysisResult) -> "tuple[str, Optional[InlineKeyboardMarkup]]":
    lines = [t("budget_result.title", title=_e(result.stats.title)), ""]

    cc = result.cross_check
    if cc is not None:
        limit_label = t("budget_result.scenario_limit_label")
        lines.append(_scenario_block(cc.optimistic_bound, cc.expected_count, cc.pessimistic_bound, cc.m, limit_label))
        lines.append("")
        lines.append(t("budget_result.verdict", verdict=_e(cc.verdict)))
        lines.append(
            t(
                "budget_result.chance",
                pessimistic=f"{cc.pessimistic_chance * 100:.0f}",
                expected=f"{cc.chance * 100:.0f}",
                optimistic=f"{cc.optimistic_chance * 100:.0f}",
            )
        )
        if cc.unknown_count:
            lines.append("")
            lines.append(
                t("budget_result.unknown_note", count=cc.unknown_count, word=_person_word(cc.unknown_count))
            )
    else:
        v = result.verdict
        lines.append(
            t("budget_result.no_v2_place", optimistic=v.optimistic_bound, pessimistic=v.pessimistic_bound, limit=v.m)
        )
        lines.append(t("budget_result.verdict", verdict=_e(v.verdict)))

    for w in result.warnings:
        lines.append(t("budget_result.warning_line", warning=_e(w)))

    row1 = []
    if cc is not None and cc.assessments:
        row1.append(InlineKeyboardButton(t("budget_result.button_details"), callback_data="details"))
    if result.stats.k is not None:
        row1.append(InlineKeyboardButton(t("budget_result.button_contract"), callback_data="contract"))
    else:
        lines.append("")
        lines.append(t("budget_result.contract_unavailable_note"))

    rows = [row1] if row1 else []
    if cc is not None:
        rows.append([InlineKeyboardButton(t("budget_result.button_track"), callback_data="track")])

    keyboard = InlineKeyboardMarkup(rows) if rows else None
    return "\n".join(lines), keyboard


def _format_details_text(result: AnalysisResult) -> str:
    cc = result.cross_check
    if cc is None or not cc.assessments:
        return t("details.none")
    lines = [t("details.header"), ""]
    for a in cc.assessments:
        applicant = a.competitor.applicant
        target = ""
        if a.best_choice:
            bc = a.best_choice
            target = t(
                "details.target",
                university=_e(bc.university),
                specialty=_e(bc.specialty),
                position=bc.position,
            )
        lines.append(
            t(
                "details.row",
                name=_e(applicant.name),
                score=applicant.score,
                priority=applicant.priority,
                status=_e(_explain_status(a.status)),
                target=target,
            )
        )
    return "\n".join(lines)


def _format_contract_message(cr: ContractResult) -> "tuple[str, Optional[InlineKeyboardMarkup]]":
    limit_label = t("contract_result.scenario_limit_label")
    lines = [
        t("contract_result.title"),
        "",
        _scenario_block(cr.optimistic_bound, cr.expected_count, cr.pessimistic_bound, cr.k, limit_label),
        "",
        t("contract_result.verdict", verdict=_e(cr.verdict)),
        t(
            "contract_result.chance",
            pessimistic=f"{cr.pessimistic_chance * 100:.0f}",
            expected=f"{cr.chance * 100:.0f}",
            optimistic=f"{cr.optimistic_chance * 100:.0f}",
        ),
    ]
    if cr.unknown_count:
        lines.append("")
        lines.append(
            t("contract_result.unknown_note", count=cr.unknown_count, word=_person_word(cr.unknown_count))
        )

    keyboard = None
    if cr.assessments:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(t("contract_result.button_details"), callback_data="contract_details")]]
        )
    return "\n".join(lines), keyboard


def _format_contract_details_text(cr: ContractResult) -> str:
    if not cr.assessments:
        return t("contract_details.none")
    lines = [t("contract_details.header"), ""]
    for a in cr.assessments:
        applicant = a.member.applicant
        origin = (
            t("contract_details.origin_contract")
            if a.member.origin == "контракт"
            else t("contract_details.origin_budget_fail")
        )
        target = ""
        if a.best_choice:
            bc = a.best_choice
            target = t(
                "contract_details.target",
                university=_e(bc.university),
                specialty=_e(bc.specialty),
                position=bc.position,
            )
        lines.append(
            t(
                "contract_details.row",
                name=_e(applicant.name),
                score=applicant.score,
                origin=_e(origin),
                status=_e(_explain_status(a.status)),
                target=target,
            )
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
    for i, tt in enumerate(_track_times_for_chat(chat_id)):
        job_queue.run_daily(periodic_check, time=tt, chat_id=chat_id, name=f"track-{chat_id}-{i}", data=chat_id)


def _unschedule_tracking(job_queue, chat_id: int) -> None:
    for job in job_queue.get_jobs_by_name(f"track-{chat_id}-0") + job_queue.get_jobs_by_name(f"track-{chat_id}-1"):
        job.schedule_removal()


async def on_track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    result = _last_result.get(chat_id)
    if result is None or result.cross_check is None:
        await query.message.reply_text(t("track.no_result"))
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

    times = ", ".join(tt.strftime("%H:%M") for tt in _track_times_for_chat(chat_id))
    await query.message.reply_text(t("track.subscribed", times=times))


async def untrack_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if bot_storage.get_subscription(chat_id) is None:
        await update.message.reply_text(t("track.not_subscribed"))
        return
    bot_storage.remove_subscription(chat_id)
    _unschedule_tracking(context.job_queue, chat_id)
    await update.message.reply_text(t("track.unsubscribed"))


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
        text = random.choice(t_list("track.nothing_changed"))
    else:
        budget_text, _ = _format_budget_message(result)
        text = t("track.update_prefix") + budget_text

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
        await query.message.reply_text(t("errors.no_saved_analysis"))
        return
    for chunk in _chunk_text(_format_details_text(result)):
        await query.message.reply_text(chunk, parse_mode=ParseMode.HTML)


async def on_contract(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    result = _last_result.get(chat_id)
    if result is None:
        await query.message.reply_text(t("errors.no_saved_analysis"))
        return
    if chat_id in _active_chats:
        await query.message.reply_text(t("contract_result.already_running"))
        return

    status_msg = await query.message.reply_text(t("contract_result.starting"))
    _active_chats.add(chat_id)

    loop = asyncio.get_running_loop()
    last_edit = [0.0]

    def on_progress(done: int, total: int, name: str) -> None:
        now = time.monotonic()
        if now - last_edit[0] < 3.0 and done != total:
            return
        last_edit[0] = now
        text = t("contract_result.progress", done=done, total=total, name=_e(name))
        _run_in_loop_and_wait(_safe_edit(status_msg, text), loop)

    try:
        cr = await asyncio.to_thread(run_contract_chance, result, on_progress=on_progress)
    except AnalysisError as e:
        await _safe_edit(status_msg, t("analysis.error", error=_e(e)))
        return
    except Exception as e:
        logger.exception("run_contract_chance впав")
        await _safe_edit(status_msg, t("analysis.unexpected_error", error=_e(e)))
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
        await query.message.reply_text(t("contract_details.no_result"))
        return
    for chunk in _chunk_text(_format_contract_details_text(cr)):
        await query.message.reply_text(chunk, parse_mode=ParseMode.HTML)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Необроблена помилка", exc_info=context.error)


async def _post_init(application: Application) -> None:
    # Меню команд біля поля вводу в Telegram — щоб не вписувати /start руками.
    await application.bot.set_my_commands(
        [
            BotCommand("start", t("commands.start")),
            BotCommand("help", t("commands.help")),
            BotCommand("cancel", t("commands.cancel")),
            BotCommand("untrack", t("commands.untrack")),
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
        raise SystemExit(t("startup.missing_token"))

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
