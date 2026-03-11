# Omni‑Tracker Bot & Dashboard

Omni‑Tracker is a Telegram bot plus Streamlit dashboard that lets you track **multi‑currency expenses**, **fitness activities**, and **daily nutrition** using a strict text template that is easy for an LLM to parse.

## 1. Setup

1. Create a Python 3.11+ virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create a Supabase project and run `db_setup.sql` against your Supabase database (via the SQL editor or psql).

3. Configure environment variables:

```bash
export SUPABASE_URL="https://<your-project>.supabase.co"
export SUPABASE_KEY="<your-supabase-service-role-key>"
export TELEGRAM_BOT_TOKEN="<your-telegram-bot-token>"
export GOOGLE_API_KEY="<your-gemini-api-key>"
export DASHBOARD_BASE_URL="http://localhost:8501"
export DASHBOARD_TOKEN_SECRET="<random-long-secret>"
```

## 2. Telegram Bot

Run the bot with:

```bash
python bot.py
```

Use `/start` or `/template` in Telegram to get the strict daily‑log template:

```text
💰 EXPENSES:
- [Amount] [Currency] | [Category] | [Description] | Split with: [Name1, Name2, or None]
🏋️ FITNESS:

[Activity] | [Duration in minutes]

🍎 NUTRITION:

[Food Item] | [Estimated Calories]
```

Fill this out (you can add multiple lines per section) and send it as a single message. The bot uses Gemini 2.5 Flash via LangChain to convert the text into structured records and stores them in Supabase.

### Phone-friendly dashboard access

To view your stats on your phone without creating a separate account, use Telegram:

- Send `/dashboard` to the bot
- Tap the private link it sends you

That link contains a short-lived signed token and shows only your own data (keyed by your Telegram user id).

## 3. Streamlit Dashboard

Run the dashboard with:

```bash
streamlit run app.py
```

You will see three tabs:

- **💸 Expenses**: Table of all expenses and a calculated **Your Share** column (amount divided by you plus all split‑with names).
- **🏃 Fitness**: Total minutes tracked and table of all activities.
- **🍽️ Nutrition**: Total calories across all entries and table of foods eaten.

## 4. Error Handling

- If environment variables or the Supabase client cannot be initialized, the code raises a clear `RuntimeError`.
- Database operations and LLM extraction are wrapped in try/except blocks with logging for easier troubleshooting.

