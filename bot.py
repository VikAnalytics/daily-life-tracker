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
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from database import (
    insert_expenses,
    insert_fitness_activities,
    insert_nutrition_items,
    store_daily_log_structured,
)
from llm_extractor import DailyLog, extract_daily_log


load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


TEMPLATE_TEXT: Final[str] = (
    "💰 EXPENSES:\n"
    "- [Amount] [Currency] | [Category] | [Description] | Split with: [Name1, Name2, or None]\n"
    "🏋️ FITNESS:\n"
    "\n"
    "[Activity] | [Duration in minutes]\n"
    "\n"
    "🍎 NUTRITION:\n"
    "\n"
    "[Food Item] | [Estimated Calories]\n"
)


DEFAULT_CURRENCY: Final[str] = os.getenv("DEFAULT_CURRENCY", "USD").upper()
DASHBOARD_BASE_URL: Final[str] = os.getenv("DASHBOARD_BASE_URL", "").rstrip("/")
DASHBOARD_TOKEN_SECRET: Final[str] = os.getenv("DASHBOARD_TOKEN_SECRET", "")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _sign_dashboard_token(*, telegram_user_id: int, ttl_minutes: int = 60 * 24 * 7) -> str:
    """
    Create a signed token for the Streamlit dashboard.
    Format: <payload_b64url>.<sig_b64url>
    """
    if not DASHBOARD_TOKEN_SECRET:
        raise RuntimeError("DASHBOARD_TOKEN_SECRET must be set.")
    exp = int((datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).timestamp())
    payload = {"tid": int(telegram_user_id), "exp": exp}
    payload_b = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64url_encode(payload_b)
    sig = hmac.new(DASHBOARD_TOKEN_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = _b64url_encode(sig)
    return f"{payload_b64}.{sig_b64}"


def _looks_like_daily_template(text: str) -> bool:
    """Loosely validate that the message matches the expected multi‑section template."""
    normalized = text.strip()
    if "💰 EXPENSES:" not in normalized:
        return False
    if "🏋️ FITNESS:" not in normalized:
        return False
    if "🍎 NUTRITION:" not in normalized:
        return False

    # At least one expense style line beginning with a dash and containing a currency‑like token.
    expense_line_pattern = re.compile(r"^-.*\b\d+(?:\.\d+)?\s+[A-Z]{2,10}\b.*$", re.MULTILINE)
    if not expense_line_pattern.search(normalized):
        # Allow days without expenses, but the sections should still exist.
        logger.debug("No expense lines detected; continuing because sections exist.")

    return True


async def start_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start an interactive, step‑by‑step logging wizard."""
    if update.message is None:
        return

    context.user_data["wizard"] = {
        "phase": "expenses",
        "step": "ask_expense_amount",
        "expenses": [],
        "fitness": [],
        "nutrition": [],
    }

    await update.message.reply_text(
        "Let's get your expenses for today.\n"
        "Send each expense one by one.\n\n"
        "First expense:\n"
        "How much did you spend? (amount only, numeric)"
    )


async def _handle_wizard_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route free‑text messages through the interactive wizard if it is active."""
    if update.message is None or not update.message.text:
        return

    wizard: Dict[str, Any] | None = context.user_data.get("wizard")  # type: ignore[assignment]
    if not wizard:
        return

    text = update.message.text.strip()
    phase = wizard.get("phase")
    step = wizard.get("step")

    # Helper to parse yes/no
    def is_yes(value: str) -> bool:
        return value.lower() in {"y", "yes", "yeah", "yep"}

    def is_no(value: str) -> bool:
        return value.lower() in {"n", "no", "nope"}

    # EXPENSES FLOW
    if phase == "expenses":
        if step == "ask_expense_amount":
            try:
                amount = float(text)
            except ValueError:
                await update.message.reply_text("Please enter just the amount as a number, e.g. 120 or 120.50.")
                return

            wizard["current_expense"] = {"amount": amount}
            wizard["step"] = "ask_expense_description"
            await update.message.reply_text("What did you spend it on? (short description)")
            return

        if step == "ask_expense_description":
            current = wizard.get("current_expense", {})
            current["description"] = text
            # Simple default category
            current["category"] = "General"
            wizard["current_expense"] = current
            wizard["step"] = "ask_expense_split"
            await update.message.reply_text(
                "Did you split it with anyone? "
                "Reply with comma‑separated names (e.g. Alice,Bob) or 'none'."
            )
            return

        if step == "ask_expense_split":
            current = wizard.get("current_expense", {})
            if text.lower() == "none":
                split_with: List[str] = []
            else:
                split_with = [name.strip() for name in text.split(",") if name.strip()]

            expense_payload = {
                "date": _date.today().isoformat(),
                "amount": float(current.get("amount", 0.0)),
                "currency": DEFAULT_CURRENCY,
                "category": current.get("category", "General"),
                "description": current.get("description", ""),
                "split_with": split_with,
            }
            expenses: List[Dict[str, Any]] = wizard.get("expenses", [])
            expenses.append(expense_payload)
            wizard["expenses"] = expenses
            wizard.pop("current_expense", None)

            wizard["step"] = "ask_more_expenses"
            await update.message.reply_text(
                "Do you have more expenses to enter? (yes/no)"
            )
            return

        if step == "ask_more_expenses":
            if is_yes(text):
                wizard["step"] = "ask_expense_amount"
                await update.message.reply_text(
                    "Next expense:\nHow much did you spend? (amount only, numeric)"
                )
                return
            if not is_no(text):
                await update.message.reply_text("Please answer 'yes' or 'no'.")
                return

            # Move to fitness phase
            wizard["phase"] = "fitness"
            wizard["step"] = "ask_activity"
            await update.message.reply_text(
                "Great. Let's move on to fitness.\n"
                "What activity did you do? (e.g. Running, Walking)"
            )
            return

    # FITNESS FLOW
    if phase == "fitness":
        if step == "ask_activity":
            wizard["current_activity"] = {"activity_type": text}
            wizard["step"] = "ask_duration"
            await update.message.reply_text(
                "How long did you do it for? (duration in minutes, numeric)"
            )
            return

        if step == "ask_duration":
            try:
                minutes = int(text)
            except ValueError:
                await update.message.reply_text(
                    "Please enter the duration as whole minutes, e.g. 30."
                )
                return

            current = wizard.get("current_activity", {})
            fitness_payload = {
                "date": _date.today().isoformat(),
                "activity_type": current.get("activity_type", ""),
                "duration_minutes": minutes,
            }
            fitness_list: List[Dict[str, Any]] = wizard.get("fitness", [])
            fitness_list.append(fitness_payload)
            wizard["fitness"] = fitness_list
            wizard.pop("current_activity", None)

            wizard["step"] = "ask_more_fitness"
            await update.message.reply_text(
                "Any more fitness activities to add? (yes/no)"
            )
            return

        if step == "ask_more_fitness":
            if is_yes(text):
                wizard["step"] = "ask_activity"
                await update.message.reply_text(
                    "Next activity:\nWhat did you do?"
                )
                return
            if not is_no(text):
                await update.message.reply_text("Please answer 'yes' or 'no'.")
                return

            # Move to nutrition phase
            wizard["phase"] = "nutrition"
            wizard["step"] = "ask_food"
            await update.message.reply_text(
                "Now let's track your nutrition.\n"
                "What did you eat? (food item)"
            )
            return

    # NUTRITION FLOW
    if phase == "nutrition":
        if step == "ask_food":
            wizard["current_food"] = {"food_item": text}
            wizard["step"] = "ask_calories"
            await update.message.reply_text(
                "Roughly how many calories was that? (numeric)"
            )
            return

        if step == "ask_calories":
            try:
                calories = int(text)
            except ValueError:
                await update.message.reply_text(
                    "Please enter calories as a whole number, e.g. 250."
                )
                return

            current = wizard.get("current_food", {})
            nutrition_payload = {
                "date": _date.today().isoformat(),
                "food_item": current.get("food_item", ""),
                "calories": calories,
            }
            nutrition_list: List[Dict[str, Any]] = wizard.get("nutrition", [])
            nutrition_list.append(nutrition_payload)
            wizard["nutrition"] = nutrition_list
            wizard.pop("current_food", None)

            wizard["step"] = "ask_more_nutrition"
            await update.message.reply_text(
                "Any more food items to add? (yes/no)"
            )
            return

        if step == "ask_more_nutrition":
            if is_yes(text):
                wizard["step"] = "ask_food"
                await update.message.reply_text(
                    "Next item:\nWhat did you eat?"
                )
                return
            if not is_no(text):
                await update.message.reply_text("Please answer 'yes' or 'no'.")
                return

            # Finish: write everything to the database
            expenses: List[Dict[str, Any]] = wizard.get("expenses", [])
            fitness_list: List[Dict[str, Any]] = wizard.get("fitness", [])
            nutrition_list: List[Dict[str, Any]] = wizard.get("nutrition", [])

            if update.effective_user is None:
                await update.message.reply_text("Couldn't identify your Telegram user. Please try again.")
                context.user_data.pop("wizard", None)
                return

            telegram_user_id = int(update.effective_user.id)
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
            except Exception as exc:  # noqa: BLE001
                logging.exception("Failed to store wizard data: %s", exc)
                await update.message.reply_text(
                    "I collected your entries but hit a database error while saving them."
                )
            else:
                await update.message.reply_text(
                    f"All set! Logged {len(expenses)} expenses, "
                    f"{len(fitness_list)} fitness activities, and "
                    f"{len(nutrition_list)} nutrition items for today. ✅"
                )

            # Clear wizard state
            context.user_data.pop("wizard", None)
            return


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
    if update.message is None:
        return
    await update.message.reply_text(
        "Welcome to Omni‑Tracker!\n\n"
        "Copy this template, fill in your day, and send it back as a single message:\n\n"
        f"{TEMPLATE_TEXT}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def template(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /template command."""
    if update.message is None:
        return
    await update.message.reply_text(TEMPLATE_TEXT, disable_web_page_preview=True)

async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a signed dashboard link suitable for phone viewing."""
    if update.effective_user is None or update.message is None:
        return
    if not DASHBOARD_BASE_URL:
        await update.message.reply_text(
            "Dashboard URL not configured. Set DASHBOARD_BASE_URL in the bot environment."
        )
        return
    try:
        token = _sign_dashboard_token(telegram_user_id=int(update.effective_user.id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to sign dashboard token: %s", exc)
        await update.message.reply_text("Failed to generate a dashboard link. Try again later.")
        return

    url = f"{DASHBOARD_BASE_URL}/?token={token}"
    await update.message.reply_text(
        "Here’s your private dashboard link (works on phone). Keep it secret:\n\n"
        f"{url}"
    )


async def handle_daily_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle arbitrary text messages.

    If the interactive wizard is active for this user, route messages there.
    Otherwise, try to interpret the message as a structured daily log template.
    """
    if update.message is None or not update.message.text:
        return

    # If the user is in the wizard workflow, handle that first.
    if context.user_data.get("wizard"):
        await _handle_wizard_message(update, context)
        return

    text = update.message.text
    if not _looks_like_daily_template(text):
        await update.message.reply_text(
            "I couldn't recognize this as a daily log.\n"
            "Please use /template, fill it in, and send it back as a single message."
        )
        return

    try:
        daily_log: DailyLog = extract_daily_log(text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM extraction failed: %s", exc)
        await update.message.reply_text(
            "Sorry, I couldn't parse your log. Please double‑check the template format and try again."
        )
        return

    if update.effective_user is None:
        await update.message.reply_text("Couldn't identify your Telegram user. Please try again.")
        return

    try:
        store_daily_log_structured(daily_log, telegram_user_id=int(update.effective_user.id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to store DailyLog: %s", exc)
        await update.message.reply_text(
            "I parsed your log but encountered a database error while saving it."
        )
        return

    num_expenses = len(daily_log.expenses)
    num_fitness = len(daily_log.fitness_activities)
    num_nutrition = len(daily_log.nutrition_items)

    await update.message.reply_text(
        f"Got it! Logged {num_expenses} expenses, {num_fitness} fitness activities, "
        f"and {num_nutrition} nutrition items for today. ✅"
    )


def main() -> None:
    """Entry point for running the Telegram bot with polling."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable must be set.")

    application = ApplicationBuilder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("template", template))
    application.add_handler(CommandHandler("wizard", start_wizard))
    application.add_handler(CommandHandler("dashboard", dashboard))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_daily_log))

    logger.info("Starting Omni‑Tracker bot polling...")
    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()

