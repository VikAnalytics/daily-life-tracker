from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from database import (
    ExpenseRecord,
    FitnessRecord,
    NutritionRecord,
    fetch_expenses_for_telegram_user,
    fetch_fitness_for_telegram_user,
    fetch_nutrition_for_telegram_user,
)


load_dotenv()

st.set_page_config(page_title="Omni‑Tracker Dashboard", page_icon="📊", layout="wide")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _verify_and_get_tid(token: str) -> Optional[int]:
    """
    Verify token from /dashboard magic link and return telegram_user_id.
    Token format: <payload_b64url>.<sig_b64url>
    payload: {"tid": <int>, "exp": <unix_ts>}
    """
    secret = os.getenv("DASHBOARD_TOKEN_SECRET", "")
    if not secret:
        st.error("Server misconfigured: DASHBOARD_TOKEN_SECRET is not set.")
        return None

    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError:
        return None

    expected_sig = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode("utf-8").rstrip("=")
    if not hmac.compare_digest(expected_sig_b64, sig_b64):
        return None

    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        tid = int(payload["tid"])
        exp = int(payload["exp"])
    except Exception:  # noqa: BLE001
        return None

    now_ts = int(datetime.now(timezone.utc).timestamp())
    if exp < now_ts:
        return None

    return tid


def _compute_your_share(expenses: List[ExpenseRecord]) -> pd.DataFrame:
    df = pd.DataFrame(expenses)
    if df.empty:
        return df

    def your_share(row: pd.Series) -> float:
        split_with = row.get("split_with") or []
        try:
            count_others = len(split_with)
        except TypeError:
            # In case split_with comes back as a string by mistake.
            count_others = 0
        participants = 1 + count_others
        if participants <= 0:
            return float(row["amount"])
        return float(row["amount"]) / participants

    df["your_share"] = df.apply(your_share, axis=1)
    return df


def render_expenses_tab() -> None:
    st.subheader("All Expenses")
    tid = st.session_state.get("telegram_user_id")
    if not isinstance(tid, int):
        st.info("Open this dashboard via Telegram using /dashboard.")
        return
    try:
        expenses = fetch_expenses_for_telegram_user(tid)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to load expenses: {exc}")
        return

    df = _compute_your_share(expenses)
    if df.empty:
        st.info("No expenses logged yet.")
        return

    # Reorder for display
    cols = [
        "date",
        "amount",
        "currency",
        "category",
        "description",
        "split_with",
        "your_share",
    ]
    df = df[cols]
    st.dataframe(df, use_container_width=True)


def render_fitness_tab() -> None:
    st.subheader("Fitness Summary")
    tid = st.session_state.get("telegram_user_id")
    if not isinstance(tid, int):
        st.info("Open this dashboard via Telegram using /dashboard.")
        return
    try:
        fitness_records: List[FitnessRecord] = fetch_fitness_for_telegram_user(tid)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to load fitness activities: {exc}")
        return

    if not fitness_records:
        st.info("No fitness activities logged yet.")
        return

    df = pd.DataFrame(fitness_records)
    total_minutes = int(df["duration_minutes"].sum())
    st.metric(label="Total Minutes Tracked", value=total_minutes)
    st.dataframe(df, use_container_width=True)


def render_nutrition_tab() -> None:
    st.subheader("Nutrition Summary")
    tid = st.session_state.get("telegram_user_id")
    if not isinstance(tid, int):
        st.info("Open this dashboard via Telegram using /dashboard.")
        return
    try:
        nutrition_records: List[NutritionRecord] = fetch_nutrition_for_telegram_user(tid)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to load nutrition items: {exc}")
        return

    if not nutrition_records:
        st.info("No nutrition entries logged yet.")
        return

    df = pd.DataFrame(nutrition_records)
    total_calories = int(df["calories"].sum())

    kpi_cols = st.columns(1)
    with kpi_cols[0]:
        st.metric(label="Total Daily Calories (all time view)", value=total_calories)

    st.dataframe(df, use_container_width=True)


def main() -> None:
    st.title("Omni‑Tracker")
    st.caption("Unified view of your expenses, fitness, and nutrition logs.")

    token = st.query_params.get("token")
    if token and isinstance(token, str):
        tid = _verify_and_get_tid(token)
        if tid is None:
            st.error("This dashboard link is invalid or expired. Get a fresh one from Telegram with /dashboard.")
        else:
            st.session_state["telegram_user_id"] = tid
    elif "telegram_user_id" not in st.session_state:
        st.info("Open this dashboard via Telegram using /dashboard to get your private link.")

    expenses_tab, fitness_tab, nutrition_tab = st.tabs(
        ["💸 Expenses", "🏃 Fitness", "🍽️ Nutrition"]
    )

    with expenses_tab:
        render_expenses_tab()
    with fitness_tab:
        render_fitness_tab()
    with nutrition_tab:
        render_nutrition_tab()


if __name__ == "__main__":
    main()

