from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import pandas as pd
import streamlit as st

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # On Streamlit Cloud env vars come from the app settings

from database import (
    ExpenseRecord,
    FitnessRecord,
    GoalsRecord,
    NutritionRecord,
    fetch_expenses_for_telegram_user,
    fetch_fitness_for_telegram_user,
    fetch_goals,
    fetch_nutrition_for_telegram_user,
    upsert_goals,
)

st.set_page_config(
    page_title="Omni‑Tracker",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    /* Main background */
    .stApp { background-color: #0f1117; color: #e0e0e0; }

    /* Card style for metric containers */
    div[data-testid="stMetric"] {
        background: linear-gradient(135deg, #1e2130, #252840);
        border: 1px solid #2e3250;
        border-radius: 12px;
        padding: 16px 20px;
    }
    div[data-testid="stMetric"] label { color: #9aa0b2 !important; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em; }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] { color: #ffffff !important; font-size: 2rem; font-weight: 700; }
    div[data-testid="stMetricDelta"] { color: #6ee7b7 !important; }

    /* Tab styling */
    button[data-baseweb="tab"] { color: #9aa0b2; font-weight: 600; }
    button[data-baseweb="tab"][aria-selected="true"] { color: #818cf8; border-bottom: 2px solid #818cf8; }

    /* Section headings */
    .section-heading {
        font-size: 1.1rem;
        font-weight: 700;
        color: #c7d2fe;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 4px;
        margin-top: 24px;
    }

    /* Progress bar override */
    .stProgress > div > div { background: linear-gradient(90deg, #6366f1, #8b5cf6); border-radius: 8px; }

    /* Dataframe */
    .stDataFrame { border-radius: 10px; overflow: hidden; }

    /* Divider */
    hr { border-color: #2e3250 !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─── Auth helpers ──────────────────────────────────────────────────────────────

def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _verify_and_get_tid(token: str) -> Optional[int]:
    secret = os.getenv("DASHBOARD_TOKEN_SECRET", "")
    if not secret:
        st.error("Server misconfigured: DASHBOARD_TOKEN_SECRET is not set.")
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError:
        return None
    expected_sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode().rstrip("=")
    if not hmac.compare_digest(expected_sig_b64, sig_b64):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode())
        tid = int(payload["tid"])
        exp = int(payload["exp"])
    except Exception:
        return None
    if exp < int(datetime.now(timezone.utc).timestamp()):
        return None
    return tid


# ─── Date filtering ─────────────────────────────────────────────────────────

def _filter_period(df: pd.DataFrame, period: str, date_col: str = "date") -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    # Normalize to timezone-naive date strings to avoid tz comparison issues
    df[date_col] = pd.to_datetime(df[date_col], utc=True).dt.tz_localize(None)
    today = pd.Timestamp(datetime.utcnow().date())
    if period == "Today":
        return df[df[date_col].dt.date == today.date()]
    elif period == "This Week":
        start = today - pd.Timedelta(days=today.dayofweek)
        return df[df[date_col] >= start]
    elif period == "This Month":
        start = today.replace(day=1)
        return df[df[date_col] >= start]
    return df  # "All Time"


def _period_selector(key: str) -> str:
    return st.radio(
        "Period",
        ["Today", "This Week", "This Month", "All Time"],
        horizontal=True,
        key=key,
        label_visibility="collapsed",
    )


# ─── Share computation ────────────────────────────────────────────────────────

def _compute_your_share(df: pd.DataFrame) -> pd.DataFrame:
    def share(row: pd.Series) -> float:
        split_with = row.get("split_with") or []
        try:
            participants = 1 + len(split_with)
        except TypeError:
            participants = 1
        return float(row["amount"]) / max(participants, 1)

    df["your_share"] = df.apply(share, axis=1)
    return df


# ─── Goals sidebar ────────────────────────────────────────────────────────────

def render_goals_sidebar(tid: int) -> GoalsRecord:
    with st.sidebar:
        st.markdown("## ⚙️ Daily Goals")
        goals = fetch_goals(tid)
        fit_goal = st.number_input(
            "Fitness goal (min/day)", min_value=1, max_value=1440,
            value=goals["fitness_minutes_goal"], key="sidebar_fit_goal"
        )
        cal_goal = st.number_input(
            "Calorie goal (kcal/day)", min_value=100, max_value=10000,
            value=goals["calories_goal"], key="sidebar_cal_goal"
        )
        if st.button("Save Goals", use_container_width=True):
            upsert_goals(tid, int(fit_goal), int(cal_goal))
            st.success("Goals saved!")
            st.rerun()
        st.markdown("---")
        st.caption("Send `/dashboard` in Telegram to get a fresh link.")
    return goals


# ─── Overview page ────────────────────────────────────────────────────────────

def render_overview(
    tid: int,
    expenses: List[ExpenseRecord],
    fitness: List[FitnessRecord],
    nutrition: List[NutritionRecord],
    goals: GoalsRecord,
) -> None:
    today_str = date.today().isoformat()

    exp_df = pd.DataFrame(expenses) if expenses else pd.DataFrame()
    fit_df = pd.DataFrame(fitness) if fitness else pd.DataFrame()
    nut_df = pd.DataFrame(nutrition) if nutrition else pd.DataFrame()

    today_utc = datetime.utcnow().date()

    def _today_rows(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        return df[pd.to_datetime(df["date"], utc=True).dt.tz_localize(None).dt.date == today_utc]

    today_exp = _today_rows(exp_df)
    today_fit = _today_rows(fit_df)
    today_nut = _today_rows(nut_df)

    st.markdown("### Today's Snapshot")
    c1, c2, c3, c4 = st.columns(4)

    # Expenses today
    today_spend = today_exp["amount"].sum() if not today_exp.empty else 0.0
    with c1:
        st.metric("💸 Spent Today", f"{today_spend:,.2f}")

    # Fitness today vs goal
    today_mins = int(today_fit["duration_minutes"].sum()) if not today_fit.empty else 0
    fit_goal = goals["fitness_minutes_goal"]
    with c2:
        st.metric("🏃 Active Minutes", f"{today_mins} min", delta=f"Goal: {fit_goal} min")

    # Calories today vs goal
    today_cals = int(today_nut["calories"].sum()) if not today_nut.empty else 0
    cal_goal = goals["calories_goal"]
    with c3:
        st.metric("🍽️ Calories Today", f"{today_cals} kcal", delta=f"Goal: {cal_goal} kcal")

    # Entries logged today
    total_entries = len(today_exp) + len(today_fit) + len(today_nut)
    with c4:
        st.metric("📝 Entries Logged", total_entries)

    st.markdown("---")
    st.markdown("### Progress vs Goals")
    g1, g2 = st.columns(2)

    with g1:
        fit_pct = min(today_mins / max(fit_goal, 1), 1.0)
        st.markdown(f"**🏃 Fitness: {today_mins} / {fit_goal} min**")
        st.progress(fit_pct)
        if today_mins >= fit_goal:
            st.success("Goal reached! 🎉")

    with g2:
        cal_pct = min(today_cals / max(cal_goal, 1), 1.0)
        st.markdown(f"**🍽️ Calories: {today_cals} / {cal_goal} kcal**")
        st.progress(cal_pct)
        if today_cals >= cal_goal:
            st.warning("Calorie goal reached.")

    # 7-day activity sparkline
    if not fit_df.empty:
        st.markdown("---")
        st.markdown("### Last 7 Days — Fitness Minutes")
        fit_df2 = fit_df.copy()
        fit_df2["date"] = pd.to_datetime(fit_df2["date"])
        last7 = date.today() - timedelta(days=6)
        week_df = fit_df2[fit_df2["date"].dt.date >= last7].groupby(fit_df2["date"].dt.date)["duration_minutes"].sum().reset_index()
        week_df.columns = ["date", "minutes"]
        week_df = week_df.sort_values("date")
        st.bar_chart(week_df.set_index("date")["minutes"], use_container_width=True)


# ─── Expenses tab ─────────────────────────────────────────────────────────────

def render_expenses_tab(tid: int, expenses: List[ExpenseRecord]) -> None:
    period = _period_selector("period_exp")
    df = pd.DataFrame(expenses) if expenses else pd.DataFrame()

    if df.empty:
        st.info("No expenses logged yet. Use /wizard in Telegram to add some.")
        return

    df = _filter_period(df, period)
    df = _compute_your_share(df)

    if df.empty:
        st.info(f"No expenses for {period.lower()}.")
        return

    # KPIs
    total = df["amount"].sum()
    your_total = df["your_share"].sum()
    top_cat = df.groupby("category")["your_share"].sum().idxmax() if not df.empty else "—"
    k1, k2, k3 = st.columns(3)
    with k1:
        st.metric("Total Spending", f"{total:,.2f}")
    with k2:
        st.metric("Your Share", f"{your_total:,.2f}")
    with k3:
        st.metric("Top Category", top_cat)

    st.markdown("---")
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.markdown('<div class="section-heading">By Category</div>', unsafe_allow_html=True)
        cat_df = df.groupby("category")["your_share"].sum().reset_index().sort_values("your_share", ascending=False)
        st.bar_chart(cat_df.set_index("category")["your_share"], use_container_width=True)

    with col_right:
        st.markdown('<div class="section-heading">By Currency</div>', unsafe_allow_html=True)
        cur_df = df.groupby("currency")["amount"].sum().reset_index().sort_values("amount", ascending=False)
        st.bar_chart(cur_df.set_index("currency")["amount"], use_container_width=True)

    st.markdown("---")
    st.markdown('<div class="section-heading">All Entries</div>', unsafe_allow_html=True)
    display_cols = ["date", "description", "category", "amount", "currency", "your_share", "split_with"]
    available = [c for c in display_cols if c in df.columns]
    st.dataframe(
        df[available].rename(columns={"your_share": "your_share (split)"}),
        use_container_width=True,
        hide_index=True,
    )


# ─── Fitness tab ──────────────────────────────────────────────────────────────

def render_fitness_tab(tid: int, fitness: List[FitnessRecord], goals: GoalsRecord) -> None:
    period = _period_selector("period_fit")
    df = pd.DataFrame(fitness) if fitness else pd.DataFrame()

    if df.empty:
        st.info("No fitness activities logged yet.")
        return

    df = _filter_period(df, period)

    if df.empty:
        st.info(f"No fitness entries for {period.lower()}.")
        return

    total_mins = int(df["duration_minutes"].sum())
    sessions = len(df)
    avg_mins = round(total_mins / max(sessions, 1))
    fit_goal = goals["fitness_minutes_goal"]

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("Total Minutes", total_mins)
    with k2:
        st.metric("Sessions", sessions)
    with k3:
        st.metric("Avg per Session", f"{avg_mins} min")
    with k4:
        st.metric("Daily Goal", f"{fit_goal} min")

    st.markdown("---")
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.markdown('<div class="section-heading">Minutes Over Time</div>', unsafe_allow_html=True)
        df2 = df.copy()
        df2["date"] = pd.to_datetime(df2["date"])
        trend = df2.groupby(df2["date"].dt.date)["duration_minutes"].sum().reset_index()
        trend.columns = ["date", "minutes"]
        # Draw goal line as annotation
        st.line_chart(trend.set_index("date")["minutes"], use_container_width=True)

    with col_right:
        st.markdown('<div class="section-heading">By Activity Type</div>', unsafe_allow_html=True)
        act_df = df.groupby("activity_type")["duration_minutes"].sum().reset_index().sort_values("duration_minutes", ascending=False)
        st.bar_chart(act_df.set_index("activity_type")["duration_minutes"], use_container_width=True)

    st.markdown("---")
    st.markdown('<div class="section-heading">All Entries</div>', unsafe_allow_html=True)
    display_cols = ["date", "activity_type", "duration_minutes"]
    available = [c for c in display_cols if c in df.columns]
    st.dataframe(df[available], use_container_width=True, hide_index=True)


# ─── Nutrition tab ────────────────────────────────────────────────────────────

def render_nutrition_tab(tid: int, nutrition: List[NutritionRecord], goals: GoalsRecord) -> None:
    period = _period_selector("period_nut")
    df = pd.DataFrame(nutrition) if nutrition else pd.DataFrame()

    if df.empty:
        st.info("No nutrition entries logged yet.")
        return

    df = _filter_period(df, period)

    if df.empty:
        st.info(f"No nutrition entries for {period.lower()}.")
        return

    total_cals = int(df["calories"].sum())
    items = len(df)
    cal_goal = goals["calories_goal"]
    avg_per_item = round(total_cals / max(items, 1))

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("Total Calories", f"{total_cals:,} kcal")
    with k2:
        st.metric("Items Logged", items)
    with k3:
        st.metric("Avg per Item", f"{avg_per_item} kcal")
    with k4:
        st.metric("Daily Goal", f"{cal_goal} kcal")

    # Goal progress (only relevant for Today)
    if period == "Today":
        st.markdown("---")
        pct = min(total_cals / max(cal_goal, 1), 1.0)
        remaining = max(cal_goal - total_cals, 0)
        st.markdown(f"**Calorie Progress: {total_cals} / {cal_goal} kcal  — {remaining} kcal remaining**")
        st.progress(pct)

    st.markdown("---")
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.markdown('<div class="section-heading">Calories Over Time</div>', unsafe_allow_html=True)
        df2 = df.copy()
        df2["date"] = pd.to_datetime(df2["date"])
        trend = df2.groupby(df2["date"].dt.date)["calories"].sum().reset_index()
        trend.columns = ["date", "calories"]
        st.line_chart(trend.set_index("date")["calories"], use_container_width=True)

    with col_right:
        st.markdown('<div class="section-heading">Top Foods by Calories</div>', unsafe_allow_html=True)
        top_foods = df.groupby("food_item")["calories"].sum().reset_index().sort_values("calories", ascending=False).head(8)
        st.bar_chart(top_foods.set_index("food_item")["calories"], use_container_width=True)

    st.markdown("---")
    st.markdown('<div class="section-heading">All Entries</div>', unsafe_allow_html=True)
    display_cols = ["date", "food_item", "calories"]
    available = [c for c in display_cols if c in df.columns]
    st.dataframe(df[available], use_container_width=True, hide_index=True)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    # Auth via token in URL — re-verify on every load so a fresh link always works
    token = st.query_params.get("token")
    if token and isinstance(token, str):
        tid_from_token = _verify_and_get_tid(token)
        if tid_from_token is None:
            st.error("This dashboard link is invalid or expired. Get a fresh one from Telegram with /dashboard.")
            st.stop()
        else:
            st.session_state["telegram_user_id"] = tid_from_token

    tid = st.session_state.get("telegram_user_id")
    if not isinstance(tid, int):
        st.title("📊 Omni‑Tracker")
        st.info("Open this dashboard via Telegram using /dashboard to get your private link.")
        st.stop()

    goals = render_goals_sidebar(tid)

    # Header
    col_title, col_refresh = st.columns([5, 1])
    with col_title:
        st.markdown(
            "<h1 style='margin-bottom:0'>📊 Omni‑Tracker</h1>"
            "<p style='color:#9aa0b2;margin-top:4px'>Your personal life dashboard</p>",
            unsafe_allow_html=True,
        )
    with col_refresh:
        st.markdown("<div style='padding-top:20px'>", unsafe_allow_html=True)
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")

    # Fetch fresh data on every render (including after refresh button)
    try:
        expenses = fetch_expenses_for_telegram_user(tid)
        fitness = fetch_fitness_for_telegram_user(tid)
        nutrition = fetch_nutrition_for_telegram_user(tid)
    except Exception as exc:
        st.error(f"Failed to load data: {exc}")
        st.stop()


    overview_tab, expenses_tab, fitness_tab, nutrition_tab = st.tabs(
        ["🏠 Overview", "💸 Expenses", "🏃 Fitness", "🍽️ Nutrition"]
    )

    with overview_tab:
        render_overview(tid, expenses, fitness, nutrition, goals)
    with expenses_tab:
        render_expenses_tab(tid, expenses)
    with fitness_tab:
        render_fitness_tab(tid, fitness, goals)
    with nutrition_tab:
        render_nutrition_tab(tid, nutrition, goals)


if __name__ == "__main__":
    main()
