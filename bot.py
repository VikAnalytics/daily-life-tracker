from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Final, List

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import (
    GoalsRecord,
    fetch_goals,
    insert_expenses,
    insert_fitness_activities,
    insert_nutrition_items,
    store_daily_log_structured,
    upsert_goals,
)
from llm_extractor import DailyLog, extract_daily_log


load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


DEFAULT_CURRENCY: Final[str] = os.getenv("DEFAULT_CURRENCY", "USD").upper()
DASHBOARD_BASE_URL: Final[str] = os.getenv("DASHBOARD_BASE_URL", "").rstrip("/")
DASHBOARD_TOKEN_SECRET: Final[str] = os.getenv("DASHBOARD_TOKEN_SECRET", "")

# ─── Inline keyboards ─────────────────────────────────────────────────────────

def _yes_no_keyboard(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes", callback_data=yes_cb),
        InlineKeyboardButton("➡️ No, move on", callback_data=no_cb),
    ]])


def _skip_keyboard(skip_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏭️ Skip", callback_data=skip_cb),
    ]])


# ─── Auth helpers ─────────────────────────────────────────────────────────────

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _sign_dashboard_token(*, telegram_user_id: int, ttl_minutes: int = 60 * 24 * 7) -> str:
    if not DASHBOARD_TOKEN_SECRET:
        raise RuntimeError("DASHBOARD_TOKEN_SECRET must be set.")
    exp = int((datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).timestamp())
    payload = {"tid": int(telegram_user_id), "exp": exp}
    payload_b = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64url_encode(payload_b)
    sig = hmac.new(DASHBOARD_TOKEN_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = _b64url_encode(sig)
    return f"{payload_b64}.{sig_b64}"


# ─── Template detection ───────────────────────────────────────────────────────



# ─── /start ───────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    name = update.effective_user.first_name if update.effective_user else "there"
    await update.message.reply_text(
        f"👋 Hey <b>{name}</b>, welcome to <b>Omni‑Tracker</b>!\n\n"
        "I help you log your daily <b>expenses</b>, <b>fitness</b>, and <b>nutrition</b> "
        "and show them in a beautiful dashboard.\n\n"
        "━━━━━━━━━━━━━━━\n"
        "🧭 <b>What would you like to do?</b>\n\n"
        "📝 <b>/wizard</b> — Step-by-step guided logging\n"
        "💬 <b>Just type</b> — Tell me your day in plain English and I'll extract everything\n"
        "📊 <b>/dashboard</b> — Open your personal stats dashboard\n"
        "🎯 <b>/goals</b> — Set your daily fitness & calorie goals\n"
        "❓ <b>/help</b> — See all commands & tips\n"
        "━━━━━━━━━━━━━━━\n\n"
        "💡 <i>Tip: Just tell me your day naturally!\n"
        'e.g. "Spent 50 USD on lunch, ran for 30 mins, had a salad ~400 cal"</i>',
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ─── /help ────────────────────────────────────────────────────────────────────

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "📖 <b>Omni‑Tracker — Help Guide</b>\n\n"
        "━━━━━━━━━━━━━━━\n"
        "📝 <b>Logging your day</b>\n\n"
        "  • <b>Just type naturally</b>\n"
        "    Send any message describing your day — I'll use AI to extract\n"
        "    expenses, fitness, and nutrition automatically.\n\n"
        '    <i>e.g. "Had coffee for 4 USD, went to the gym for 45 mins, ate pasta ~600 cal"</i>\n\n'
        "  • <b>/wizard</b>\n"
        "    Prefer to be guided? I'll ask one question at a time.\n\n"
        "━━━━━━━━━━━━━━━\n"
        "📊 <b>Viewing your stats</b>\n\n"
        "  • <b>/dashboard</b>\n"
        "    Sends you a private link to your personal dashboard.\n"
        "    Works on phone — charts, KPIs, daily/weekly/monthly views.\n\n"
        "━━━━━━━━━━━━━━━\n"
        "🎯 <b>Setting goals</b>\n\n"
        "  • <b>/goals</b>\n"
        "    Set daily targets for fitness minutes and calories.\n"
        "    Your dashboard will show progress bars vs these goals.\n\n"
        "━━━━━━━━━━━━━━━\n"
        "💡 <b>Tips</b>\n"
        "  • All your data is private — only you can see your dashboard.\n"
        f"  • Currency defaults to <code>{DEFAULT_CURRENCY}</code> if you don't mention one.\n"
        "  • You can log just one thing — e.g. just mention a workout.\n"
        "  • If the AI misses something, just send a follow-up message.\n",
        parse_mode=ParseMode.HTML,
    )


# ─── /template ────────────────────────────────────────────────────────────────


# ─── /dashboard ───────────────────────────────────────────────────────────────

async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    if not DASHBOARD_BASE_URL:
        await update.message.reply_text(
            "⚠️ Dashboard URL not configured. Ask the bot admin to set <code>DASHBOARD_BASE_URL</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        token = _sign_dashboard_token(telegram_user_id=int(update.effective_user.id))
    except Exception as exc:
        logger.exception("Failed to sign dashboard token: %s", exc)
        await update.message.reply_text("❌ Failed to generate a dashboard link. Try again later.")
        return

    url = f"{DASHBOARD_BASE_URL}/?token={token}"
    await update.message.reply_text(
        "📊 <b>Your Private Dashboard</b>\n\n"
        "Tap the link below to open your personal stats on your phone:\n\n"
        f"🔗 {url}\n\n"
        "<i>This link is valid for 7 days. Get a new one anytime with /dashboard.\n"
        "Keep it secret — it's private to you!</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ─── /goals ───────────────────────────────────────────────────────────────────

async def goals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current goals and prompt the user to update them."""
    if update.message is None or update.effective_user is None:
        return
    tid = int(update.effective_user.id)
    goals: GoalsRecord = fetch_goals(tid)
    context.user_data["goals_step"] = "ask_fitness_goal"
    await update.message.reply_text(
        "🎯 <b>Daily Goals</b>\n\n"
        f"Current goals:\n"
        f"  🏃 Fitness: <b>{goals['fitness_minutes_goal']} min/day</b>\n"
        f"  🍽️ Calories: <b>{goals['calories_goal']} kcal/day</b>\n\n"
        "Let's update them. What's your daily <b>fitness goal</b> in minutes?\n"
        "<i>(e.g. 30 for 30 minutes per day)</i>",
        parse_mode=ParseMode.HTML,
    )


# ─── Wizard ───────────────────────────────────────────────────────────────────

async def start_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    context.user_data.pop("goals_step", None)
    context.user_data["wizard"] = {
        "phase": "expenses",
        "step": "ask_expense_amount",
        "expenses": [],
        "fitness": [],
        "nutrition": [],
    }
    await update.message.reply_text(
        "✨ <b>Daily Log Wizard</b>\n\n"
        "I'll ask you a few short questions to log your day.\n"
        "Let's start with your <b>expenses</b>.\n\n"
        "💰 <b>How much did you spend?</b>\n"
        "<i>(Enter just the number, e.g. <code>12.50</code>. Type <code>skip</code> to skip expenses.)</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=_skip_keyboard("wizard_skip_expenses"),
    )


async def _wizard_finish(update_or_query: Any, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Persist wizard data and clean up state.

    Accepts either an Update (from message handlers) or a CallbackQuery (from button handlers).
    """
    wizard: Dict[str, Any] = context.user_data.get("wizard", {})
    expenses: List[Dict[str, Any]] = wizard.get("expenses", [])
    fitness_list: List[Dict[str, Any]] = wizard.get("fitness", [])
    nutrition_list: List[Dict[str, Any]] = wizard.get("nutrition", [])

    # Support both Update objects and CallbackQuery objects
    if isinstance(update_or_query, Update):
        user = update_or_query.effective_user
        msg = update_or_query.message or (update_or_query.callback_query.message if update_or_query.callback_query else None)
    else:
        # It's a CallbackQuery
        user = update_or_query.from_user
        msg = update_or_query.message

    if user is None or msg is None:
        return

    telegram_user_id = int(user.id)
    for e in expenses:
        e["telegram_user_id"] = telegram_user_id
    for f in fitness_list:
        f["telegram_user_id"] = telegram_user_id
    for n in nutrition_list:
        n["telegram_user_id"] = telegram_user_id

    try:
        insert_expenses(expenses)
        insert_fitness_activities(fitness_list)
        insert_nutrition_items(nutrition_list)
    except Exception as exc:
        logger.exception("Failed to store wizard data: %s", exc)
        await msg.reply_text("⚠️ I collected your entries but hit a database error while saving them.")
    else:
        summary_parts = []
        if expenses:
            summary_parts.append(f"💰 {len(expenses)} expense(s)")
        if fitness_list:
            summary_parts.append(f"🏃 {len(fitness_list)} fitness activity/activities")
        if nutrition_list:
            summary_parts.append(f"🍽️ {len(nutrition_list)} food item(s)")

        if summary_parts:
            summary = "\n".join(f"  • {p}" for p in summary_parts)
            await msg.reply_text(
                f"✅ <b>All done! Here's what I logged for today:</b>\n\n{summary}\n\n"
                "View it all on your dashboard with /dashboard 📊",
                parse_mode=ParseMode.HTML,
            )
        else:
            await msg.reply_text("👍 Nothing to log — wizard complete.")

    context.user_data.pop("wizard", None)


async def _handle_wizard_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or not update.message.text:
        return

    wizard: Dict[str, Any] | None = context.user_data.get("wizard")
    if not wizard:
        return

    text = update.message.text.strip()
    phase = wizard.get("phase")
    step = wizard.get("step")

    # ── EXPENSES ──────────────────────────────────────────────────
    if phase == "expenses":
        if step == "ask_expense_amount":
            if text.lower() == "skip":
                wizard["phase"] = "fitness"
                wizard["step"] = "ask_activity"
                await update.message.reply_text(
                    "Skipping expenses. 🏋️ <b>Fitness time!</b>\n\n"
                    "What activity did you do today?\n"
                    "<i>(e.g. Running, Yoga, Cycling — or type <code>skip</code>)</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=_skip_keyboard("wizard_skip_fitness"),
                )
                return
            try:
                amount = float(text)
            except ValueError:
                await update.message.reply_text("Please enter just the amount as a number, e.g. <code>12.50</code>.", parse_mode=ParseMode.HTML)
                return
            wizard["current_expense"] = {"amount": amount}
            wizard["step"] = "ask_expense_currency"
            await update.message.reply_text(
                f"💱 <b>What currency?</b>\n"
                f"<i>(e.g. USD, EUR, INR — or press Enter to use <b>{DEFAULT_CURRENCY}</b>)</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        if step == "ask_expense_currency":
            current = wizard.get("current_expense", {})
            if text.upper() in ["", "-", "DEFAULT", DEFAULT_CURRENCY] or len(text) < 2:
                current["currency"] = DEFAULT_CURRENCY
            else:
                current["currency"] = text.upper()[:10]
            wizard["current_expense"] = current
            wizard["step"] = "ask_expense_description"
            await update.message.reply_text(
                "📝 <b>What did you spend it on?</b>\n<i>(Short description, e.g. Lunch, Uber, Groceries)</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        if step == "ask_expense_description":
            current = wizard.get("current_expense", {})
            current["description"] = text
            current["category"] = _guess_category(text)
            wizard["current_expense"] = current
            wizard["step"] = "ask_expense_split"
            await update.message.reply_text(
                "👥 <b>Did you split this with anyone?</b>\n"
                "<i>Enter comma-separated names (e.g. <code>Alice, Bob</code>) or tap None.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🙋 Just me", callback_data="wizard_split_none"),
                ]]),
            )
            return

        if step == "ask_expense_split":
            split_with: List[str] = [] if text.lower() in {"none", "no", "-", ""} else [n.strip() for n in text.split(",") if n.strip()]
            _commit_expense(wizard, split_with)
            wizard["step"] = "ask_more_expenses"
            await update.message.reply_text(
                "➕ <b>Any more expenses to add?</b>",
                reply_markup=_yes_no_keyboard("wizard_more_expenses", "wizard_done_expenses"),
            )
            return

        if step == "ask_more_expenses":
            if text.lower() in {"y", "yes"}:
                wizard["step"] = "ask_expense_amount"
                await update.message.reply_text(
                    "💰 <b>Next expense — how much?</b>",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await _transition_to_fitness(update, wizard)
            return

    # ── FITNESS ──────────────────────────────────────────────────
    if phase == "fitness":
        if step == "ask_activity":
            if text.lower() == "skip":
                wizard["phase"] = "nutrition"
                wizard["step"] = "ask_food"
                await update.message.reply_text(
                    "Skipping fitness. 🍽️ <b>Nutrition time!</b>\n\n"
                    "What did you eat today?\n<i>(Or type <code>skip</code> to skip)</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=_skip_keyboard("wizard_skip_nutrition"),
                )
                return
            wizard["current_activity"] = {"activity_type": text.title()}
            wizard["step"] = "ask_duration"
            await update.message.reply_text(
                "⏱️ <b>How long?</b> (in minutes)\n<i>(e.g. <code>30</code>)</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        if step == "ask_duration":
            try:
                minutes = int(text)
            except ValueError:
                await update.message.reply_text("Please enter whole minutes, e.g. <code>30</code>.", parse_mode=ParseMode.HTML)
                return
            current = wizard.get("current_activity", {})
            wizard["fitness"].append({
                "date": _date.today().isoformat(),
                "activity_type": current.get("activity_type", ""),
                "duration_minutes": minutes,
            })
            wizard.pop("current_activity", None)
            wizard["step"] = "ask_more_fitness"
            await update.message.reply_text(
                "➕ <b>Any more fitness activities?</b>",
                reply_markup=_yes_no_keyboard("wizard_more_fitness", "wizard_done_fitness"),
            )
            return

        if step == "ask_more_fitness":
            if text.lower() in {"y", "yes"}:
                wizard["step"] = "ask_activity"
                await update.message.reply_text("🏋️ <b>Next activity — what did you do?</b>", parse_mode=ParseMode.HTML)
            else:
                await _transition_to_nutrition(update, wizard)
            return

    # ── NUTRITION ─────────────────────────────────────────────────
    if phase == "nutrition":
        if step == "ask_food":
            if text.lower() == "skip":
                await _wizard_finish(update, context)
                return
            wizard["current_food"] = {"food_item": text.title()}
            wizard["step"] = "ask_calories"
            await update.message.reply_text(
                "🔢 <b>Roughly how many calories?</b>\n<i>(e.g. <code>250</code>)</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        if step == "ask_calories":
            try:
                calories = int(text)
            except ValueError:
                await update.message.reply_text("Please enter calories as a whole number, e.g. <code>250</code>.", parse_mode=ParseMode.HTML)
                return
            current = wizard.get("current_food", {})
            wizard["nutrition"].append({
                "date": _date.today().isoformat(),
                "food_item": current.get("food_item", ""),
                "calories": calories,
            })
            wizard.pop("current_food", None)
            wizard["step"] = "ask_more_nutrition"
            await update.message.reply_text(
                "➕ <b>Anything else to add to nutrition?</b>",
                reply_markup=_yes_no_keyboard("wizard_more_nutrition", "wizard_done_nutrition"),
            )
            return

        if step == "ask_more_nutrition":
            if text.lower() in {"y", "yes"}:
                wizard["step"] = "ask_food"
                await update.message.reply_text("🍽️ <b>Next item — what did you eat?</b>", parse_mode=ParseMode.HTML)
            else:
                await _wizard_finish(update, context)
            return


def _commit_expense(wizard: Dict[str, Any], split_with: List[str]) -> None:
    current = wizard.get("current_expense", {})
    wizard["expenses"].append({
        "date": _date.today().isoformat(),
        "amount": float(current.get("amount", 0.0)),
        "currency": current.get("currency", DEFAULT_CURRENCY),
        "category": current.get("category", "General"),
        "description": current.get("description", ""),
        "split_with": split_with,
    })
    wizard.pop("current_expense", None)


def _guess_category(description: str) -> str:
    desc = description.lower()
    mapping = {
        "Food": ["lunch", "dinner", "breakfast", "coffee", "cafe", "restaurant", "food", "eat", "snack", "pizza", "burger"],
        "Transport": ["uber", "taxi", "bus", "metro", "train", "petrol", "fuel", "parking", "flight", "travel"],
        "Shopping": ["amazon", "shop", "store", "mall", "clothes", "shoes", "buy", "purchase"],
        "Health": ["pharmacy", "doctor", "medicine", "gym", "health", "hospital"],
        "Entertainment": ["movie", "netflix", "spotify", "game", "concert", "bar", "club"],
        "Utilities": ["electricity", "water", "internet", "phone", "rent", "wifi"],
    }
    for cat, keywords in mapping.items():
        if any(kw in desc for kw in keywords):
            return cat
    return "General"


async def _transition_to_fitness(msg_source: Any, wizard: Dict[str, Any]) -> None:
    """msg_source can be an Update (has .message) or a Message directly."""
    wizard["phase"] = "fitness"
    wizard["step"] = "ask_activity"
    msg = msg_source.message if isinstance(msg_source, Update) else msg_source
    await msg.reply_text(
        "🏋️ <b>Fitness time!</b>\n\n"
        "What physical activity did you do today?\n"
        "<i>(e.g. Running, Yoga, Cycling — or type <code>skip</code>)</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=_skip_keyboard("wizard_skip_fitness"),
    )


async def _transition_to_nutrition(msg_source: Any, wizard: Dict[str, Any]) -> None:
    """msg_source can be an Update (has .message) or a Message directly."""
    wizard["phase"] = "nutrition"
    wizard["step"] = "ask_food"
    msg = msg_source.message if isinstance(msg_source, Update) else msg_source
    await msg.reply_text(
        "🍽️ <b>Nutrition time!</b>\n\n"
        "What did you eat today? Log one item at a time.\n"
        "<i>(Or type <code>skip</code> to skip nutrition)</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=_skip_keyboard("wizard_skip_nutrition"),
    )


# ─── Callback query handler (inline keyboard buttons) ─────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    data = query.data
    wizard: Dict[str, Any] | None = context.user_data.get("wizard")

    # ── Wizard yes/no ──────────────────────────────────────────────
    if data == "wizard_more_expenses":
        if wizard:
            wizard["step"] = "ask_expense_amount"
        await query.message.reply_text("💰 <b>Next expense — how much?</b>", parse_mode=ParseMode.HTML)

    elif data == "wizard_done_expenses":
        if wizard:
            await _transition_to_fitness(query.message, wizard)

    elif data == "wizard_more_fitness":
        if wizard:
            wizard["step"] = "ask_activity"
        await query.message.reply_text("🏋️ <b>Next activity — what did you do?</b>", parse_mode=ParseMode.HTML)

    elif data == "wizard_done_fitness":
        if wizard:
            await _transition_to_nutrition(query.message, wizard)

    elif data == "wizard_more_nutrition":
        if wizard:
            wizard["step"] = "ask_food"
        await query.message.reply_text("🍽️ <b>What did you eat?</b>", parse_mode=ParseMode.HTML)

    elif data == "wizard_done_nutrition":
        await _wizard_finish(query, context)

    elif data == "wizard_split_none":
        if wizard:
            _commit_expense(wizard, [])
            wizard["step"] = "ask_more_expenses"
        await query.message.reply_text(
            "➕ <b>Any more expenses to add?</b>",
            reply_markup=_yes_no_keyboard("wizard_more_expenses", "wizard_done_expenses"),
        )

    elif data == "wizard_skip_expenses":
        if wizard:
            wizard["phase"] = "fitness"
            wizard["step"] = "ask_activity"
        await query.message.reply_text(
            "🏋️ <b>Fitness time!</b>\n\nWhat activity did you do today?\n<i>(Or type <code>skip</code>)</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=_skip_keyboard("wizard_skip_fitness"),
        )

    elif data == "wizard_skip_fitness":
        if wizard:
            wizard["phase"] = "nutrition"
            wizard["step"] = "ask_food"
        await query.message.reply_text(
            "🍽️ <b>Nutrition time!</b>\n\nWhat did you eat?\n<i>(Or type <code>skip</code>)</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=_skip_keyboard("wizard_skip_nutrition"),
        )

    elif data == "wizard_skip_nutrition":
        await _wizard_finish(query, context)


# ─── Goals conversation ───────────────────────────────────────────────────────

async def _handle_goals_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or not update.message.text:
        return
    if update.effective_user is None:
        return

    step = context.user_data.get("goals_step")
    text = update.message.text.strip()
    tid = int(update.effective_user.id)

    if step == "ask_fitness_goal":
        try:
            fit_goal = int(text)
            if fit_goal < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a positive whole number, e.g. <code>30</code>.", parse_mode=ParseMode.HTML)
            return
        context.user_data["goals_fit"] = fit_goal
        context.user_data["goals_step"] = "ask_calorie_goal"
        await update.message.reply_text(
            f"✅ Fitness goal set to <b>{fit_goal} min/day</b>.\n\n"
            "🍽️ <b>What's your daily calorie goal?</b>\n<i>(e.g. <code>2000</code> kcal)</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    if step == "ask_calorie_goal":
        try:
            cal_goal = int(text)
            if cal_goal < 100:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a reasonable number, e.g. <code>2000</code>.", parse_mode=ParseMode.HTML)
            return
        fit_goal = context.user_data.pop("goals_fit", 30)
        context.user_data.pop("goals_step", None)
        try:
            upsert_goals(tid, fit_goal, cal_goal)
        except Exception as exc:
            logger.exception("Failed to save goals: %s", exc)
            await update.message.reply_text("⚠️ Couldn't save goals right now. Try again later.")
            return
        await update.message.reply_text(
            f"🎯 <b>Goals saved!</b>\n\n"
            f"  🏃 Fitness: <b>{fit_goal} min/day</b>\n"
            f"  🍽️ Calories: <b>{cal_goal} kcal/day</b>\n\n"
            "Your dashboard will now show progress bars against these targets. 📊\n"
            "You can also update them anytime in the sidebar of your dashboard.",
            parse_mode=ParseMode.HTML,
        )


# ─── Main message router ──────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or not update.message.text:
        return

    # Goals conversation
    if context.user_data.get("goals_step"):
        await _handle_goals_message(update, context)
        return

    # Wizard conversation
    if context.user_data.get("wizard"):
        await _handle_wizard_message(update, context)
        return

    # Free-form natural language — pass everything to the LLM
    text = update.message.text
    if len(text.strip()) < 5:
        await update.message.reply_text(
            "🤔 That's a bit short for me to work with!\n\n"
            "Just describe your day naturally, e.g.:\n"
            "<i>\"Had coffee for 4 USD, ran 30 mins, ate pasta ~600 cal\"</i>\n\n"
            "Or use /wizard for guided step-by-step logging.",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_text("🧠 <i>Extracting your entries...</i>", parse_mode=ParseMode.HTML)

    try:
        daily_log: DailyLog = extract_daily_log(text)
    except Exception as exc:
        logger.exception("LLM extraction failed: %s", exc)
        await update.message.reply_text(
            "❌ I couldn't extract anything from that. Try rephrasing, or use /wizard for guided logging."
        )
        return

    if update.effective_user is None:
        await update.message.reply_text("❌ Couldn't identify your Telegram user. Please try again.")
        return

    num_expenses = len(daily_log.expenses)
    num_fitness = len(daily_log.fitness_activities)
    num_nutrition = len(daily_log.nutrition_items)

    if num_expenses == 0 and num_fitness == 0 and num_nutrition == 0:
        await update.message.reply_text(
            "🤷 I couldn't find any expenses, fitness, or nutrition in that message.\n\n"
            "Try being a bit more specific, e.g.:\n"
            "<i>\"Lunch 12 USD, walked 20 mins, had a burger ~700 cal\"</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        store_daily_log_structured(daily_log, telegram_user_id=int(update.effective_user.id))
    except Exception as exc:
        logger.exception("Failed to store DailyLog: %s", exc)
        await update.message.reply_text("⚠️ Parsed your log but encountered a database error while saving it.")
        return

    # Build a human-readable confirmation of what was extracted
    lines = ["✅ <b>Got it! Here's what I logged:</b>\n"]
    if num_expenses:
        lines.append("💰 <b>Expenses:</b>")
        for e in daily_log.expenses:
            split_note = f" (split with {', '.join(e.split_with)})" if e.split_with else ""
            lines.append(f"  • {e.amount} {e.currency} — {e.description}{split_note}")
    if num_fitness:
        lines.append("\n🏃 <b>Fitness:</b>")
        for f in daily_log.fitness_activities:
            lines.append(f"  • {f.activity_type} — {f.duration_minutes} min")
    if num_nutrition:
        lines.append("\n🍽️ <b>Nutrition:</b>")
        for n in daily_log.nutrition_items:
            lines.append(f"  • {n.food_item} — {n.calories} kcal")
    lines.append("\n<i>Something wrong? Just send a correction and I'll log it.</i>")
    lines.append("View it all with /dashboard 📊")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable must be set.")

    application = ApplicationBuilder().token(bot_token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("wizard", start_wizard))
    application.add_handler(CommandHandler("dashboard", dashboard))
    application.add_handler(CommandHandler("goals", goals_cmd))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Starting Omni‑Tracker bot polling...")
    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
