from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, TypedDict

from supabase import Client, create_client


logger = logging.getLogger(__name__)


class ExpenseRecord(TypedDict):
    id: int
    telegram_user_id: int
    date: date
    amount: float
    currency: str
    category: str
    description: Optional[str]
    split_with: List[str]


class FitnessRecord(TypedDict):
    id: int
    telegram_user_id: int
    date: date
    activity_type: str
    duration_minutes: int


class NutritionRecord(TypedDict):
    id: int
    telegram_user_id: int
    date: date
    food_item: str
    calories: int


@dataclass
class DatabaseConfig:
    supabase_url: str
    supabase_key: str

    @staticmethod
    def from_env() -> "DatabaseConfig":
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in environment variables.")
        return DatabaseConfig(supabase_url=url, supabase_key=key)


_supabase_client: Optional[Client] = None


def get_client() -> Client:
    """Return a singleton Supabase client instance."""
    global _supabase_client
    if _supabase_client is None:
        cfg = DatabaseConfig.from_env()
        try:
            _supabase_client = create_client(cfg.supabase_url, cfg.supabase_key)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to create Supabase client.")
            raise RuntimeError("Could not initialize Supabase client.") from exc
    return _supabase_client


def insert_expenses(expenses: Sequence[Dict[str, Any]]) -> None:
    """Insert multiple expense records into the database."""
    if not expenses:
        return
    client = get_client()
    try:
        client.table("expenses").insert(list(expenses)).execute()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to insert expenses: %s", exc)
        raise RuntimeError("Database error while inserting expenses.") from exc


def insert_fitness_activities(activities: Sequence[Dict[str, Any]]) -> None:
    """Insert multiple fitness records into the database."""
    if not activities:
        return
    client = get_client()
    try:
        client.table("fitness").insert(list(activities)).execute()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to insert fitness activities: %s", exc)
        raise RuntimeError("Database error while inserting fitness activities.") from exc


def insert_nutrition_items(items: Sequence[Dict[str, Any]]) -> None:
    """Insert multiple nutrition records into the database."""
    if not items:
        return
    client = get_client()
    try:
        client.table("nutrition").insert(list(items)).execute()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to insert nutrition items: %s", exc)
        raise RuntimeError("Database error while inserting nutrition items.") from exc


def fetch_expenses_for_telegram_user(telegram_user_id: int) -> List[ExpenseRecord]:
    """Fetch expense records for a Telegram user."""
    client = get_client()
    try:
        response = (
            client.table("expenses")
            .select("*")
            .eq("telegram_user_id", telegram_user_id)
            .order("date", desc=True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch expenses: %s", exc)
        raise RuntimeError("Database error while fetching expenses.") from exc

    data = response.data or []
    return [ExpenseRecord(**row) for row in data]  # type: ignore[arg-type]


def fetch_fitness_for_telegram_user(telegram_user_id: int) -> List[FitnessRecord]:
    """Fetch fitness records for a Telegram user."""
    client = get_client()
    try:
        response = (
            client.table("fitness")
            .select("*")
            .eq("telegram_user_id", telegram_user_id)
            .order("date", desc=True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch fitness activities: %s", exc)
        raise RuntimeError("Database error while fetching fitness activities.") from exc

    data = response.data or []
    return [FitnessRecord(**row) for row in data]  # type: ignore[arg-type]


def fetch_nutrition_for_telegram_user(telegram_user_id: int) -> List[NutritionRecord]:
    """Fetch nutrition records for a Telegram user."""
    client = get_client()
    try:
        response = (
            client.table("nutrition")
            .select("*")
            .eq("telegram_user_id", telegram_user_id)
            .order("date", desc=True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch nutrition items: %s", exc)
        raise RuntimeError("Database error while fetching nutrition items.") from exc

    data = response.data or []
    return [NutritionRecord(**row) for row in data]  # type: ignore[arg-type]


def store_daily_log_structured(daily_log: "DailyLogLike", *, telegram_user_id: int) -> None:
    """
    Persist a structured DailyLog-like object into the three tables.

    The object must expose .expenses, .fitness_activities, and .nutrition_items sequences
    with attributes that map 1:1 to the database columns (minus ids and timestamps).
    """
    from datetime import date as _date  # local import to avoid circulars

    def _serialize_date(value: Any) -> str:
        """Convert a date-like value to an ISO date string for Supabase."""
        if isinstance(value, _date):
            return value.isoformat()
        # Fallback: if it's already a string, pass through; otherwise, use today's date.
        if isinstance(value, str):
            return value
        return _date.today().isoformat()

    expense_payload: List[Dict[str, Any]] = []
    for e in getattr(daily_log, "expenses", []) or []:
        expense_payload.append(
            {
                "telegram_user_id": telegram_user_id,
                "date": _serialize_date(getattr(e, "date", _date.today())),
                "amount": float(getattr(e, "amount")),
                "currency": getattr(e, "currency"),
                "category": getattr(e, "category"),
                "description": getattr(e, "description", None),
                "split_with": list(getattr(e, "split_with", []) or []),
            }
        )

    fitness_payload: List[Dict[str, Any]] = []
    for a in getattr(daily_log, "fitness_activities", []) or []:
        fitness_payload.append(
            {
                "telegram_user_id": telegram_user_id,
                "date": _serialize_date(getattr(a, "date", _date.today())),
                "activity_type": getattr(a, "activity_type"),
                "duration_minutes": int(getattr(a, "duration_minutes")),
            }
        )

    nutrition_payload: List[Dict[str, Any]] = []
    for n in getattr(daily_log, "nutrition_items", []) or []:
        nutrition_payload.append(
            {
                "telegram_user_id": telegram_user_id,
                "date": _serialize_date(getattr(n, "date", _date.today())),
                "food_item": getattr(n, "food_item"),
                "calories": int(getattr(n, "calories")),
            }
        )

    # Insert in three independent steps so a failure in one category
    # does not prevent others from being stored.
    insert_expenses(expense_payload)
    insert_fitness_activities(fitness_payload)
    insert_nutrition_items(nutrition_payload)


class DailyLogLike(TypedDict, total=False):
    """Structural type used for type hints in store_daily_log_structured."""

    expenses: Sequence[Any]
    fitness_activities: Sequence[Any]
    nutrition_items: Sequence[Any]


class GoalsRecord(TypedDict):
    telegram_user_id: int
    fitness_minutes_goal: int
    calories_goal: int


def fetch_goals(telegram_user_id: int) -> GoalsRecord:
    """Return the user's goals, or sensible defaults if none have been set."""
    client = get_client()
    try:
        resp = (
            client.table("goals")
            .select("*")
            .eq("telegram_user_id", telegram_user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch goals: %s", exc)
        return GoalsRecord(telegram_user_id=telegram_user_id, fitness_minutes_goal=30, calories_goal=2000)

    rows = resp.data or []
    if not rows:
        return GoalsRecord(telegram_user_id=telegram_user_id, fitness_minutes_goal=30, calories_goal=2000)
    return GoalsRecord(**rows[0])  # type: ignore[arg-type]


def upsert_goals(telegram_user_id: int, fitness_minutes_goal: int, calories_goal: int) -> None:
    """Save or update daily goals for a user."""
    client = get_client()
    payload = {
        "telegram_user_id": telegram_user_id,
        "fitness_minutes_goal": fitness_minutes_goal,
        "calories_goal": calories_goal,
        "updated_at": date.today().isoformat(),
    }
    try:
        client.table("goals").upsert(payload).execute()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to upsert goals: %s", exc)
        raise RuntimeError("Database error while saving goals.") from exc

