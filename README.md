# 📊 Omni-Tracker

A personal life tracker built on Telegram + Streamlit. Log your daily expenses, fitness, and nutrition through a friendly Telegram bot and view beautiful stats on a private dashboard.

---

## Features

- **Telegram bot** — guided wizard to log expenses, fitness, and nutrition one question at a time
- **Multi-currency expenses** — tracks any currency, calculates your split share automatically
- **Fitness tracking** — logs activities and duration, tracks progress vs your daily goal
- **Nutrition logging** — tracks food items and calories, shows daily calorie budget
- **Daily goals** — set personal targets for fitness minutes and calories
- **Private dashboard** — magic-link access from Telegram, no login required
- **Charts & filters** — bar/line charts with Today / This Week / This Month / All Time views
- **Always-on bot** — deployed on Railway, runs 24/7 without your computer

---

## Tech Stack

| Layer | Technology |
|---|---|
| Bot | Python + python-telegram-bot |
| LLM extraction | LangChain + Gemini 2.5 Flash |
| Database | Supabase (PostgreSQL) |
| Dashboard | Streamlit |
| Bot hosting | Railway |
| Dashboard hosting | Streamlit Community Cloud |

---

## Project Structure

```
.
├── bot.py                 # Telegram bot (commands, wizard, goals)
├── app.py                 # Streamlit dashboard
├── llm_extractor.py       # LangChain + Gemini structured extraction
├── database.py            # Supabase client and data functions
├── db_setup.sql           # PostgreSQL schema (run once in Supabase)
├── requirements.txt       # Dashboard dependencies (Streamlit Cloud)
├── requirements-bot.txt   # Bot dependencies (Railway)
├── railway.json           # Railway deployment config
├── Procfile               # Railway process definition
├── runtime.txt            # Python 3.11 pin for Streamlit Cloud
└── .env                   # Local secrets (never committed)
```

---

## Setup

### 1. Prerequisites

- Python 3.11+
- A [Telegram bot token](https://t.me/BotFather)
- A [Supabase](https://supabase.com) project
- A [Google AI Studio](https://aistudio.google.com) API key (for Gemini)

### 2. Clone and install

```bash
git clone https://github.com/VikAnalytics/daily-life-tracker.git
cd daily-life-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-bot.txt
```

### 3. Environment variables

Create a `.env` file in the project root:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_service_role_key
GOOGLE_API_KEY=your_google_ai_api_key
DASHBOARD_BASE_URL=https://your-app.streamlit.app
DASHBOARD_TOKEN_SECRET=your_random_secret_string
DEFAULT_CURRENCY=USD
```

> Generate a secure token secret with: `python3 -c "import secrets; print(secrets.token_hex(32))"`

### 4. Database setup

Run `db_setup.sql` in your Supabase SQL editor:

```sql
-- Copy and paste the full contents of db_setup.sql
```

This creates four tables: `expenses`, `fitness`, `nutrition`, `goals`.

### 5. Run locally

```bash
source .venv/bin/activate
python bot.py
```

---

## Deployment

### Bot → Railway (24/7)

1. Go to [railway.app](https://railway.app) → **New Project → Deploy from GitHub**
2. Select this repo — Railway picks up `railway.json` automatically
3. Add all environment variables under the **Variables** tab
4. Deploy — check logs for `Starting Omni-Tracker bot polling...`

> Railway auto-restarts the bot on crashes. Never run the bot locally at the same time as Railway — two polling instances will conflict.

### Dashboard → Streamlit Community Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**
2. Select this repo, set **Main file** to `app.py`, select **Python 3.11**
3. Add the same environment variables under **Advanced settings → Secrets**
4. Deploy — copy the app URL and set it as `DASHBOARD_BASE_URL` in Railway

---

## Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and overview of all commands |
| `/wizard` | Step-by-step guided logging (recommended) |
| `/template` | Copy-and-fill quick text format |
| `/goals` | Set daily fitness and calorie targets |
| `/dashboard` | Get your private dashboard link (valid 7 days) |
| `/help` | Full command reference |

---

## How it works

### Logging via /wizard

The wizard walks you through three sections one question at a time:

1. **Expenses** — amount → currency → description (auto-categorised) → split with
2. **Fitness** — activity → duration in minutes
3. **Nutrition** — food item → calories

Inline buttons handle Yes / No / Skip so you never have to type those.

### Logging via /template

Copy the template, fill it in, and send it back as one message. Gemini 2.5 Flash extracts the structured data via LangChain and saves it to Supabase.

### Dashboard access

Sending `/dashboard` generates a signed HMAC token containing your Telegram user ID. The Streamlit app verifies the token and loads only your data — no accounts or passwords needed.

---

## Privacy

- All data is keyed by `telegram_user_id` — users can only see their own records
- Dashboard links are signed with HMAC-SHA256 and expire after 7 days
- No Supabase Auth or Row Level Security required for end users
- The `.env` file is excluded from version control via `.gitignore`

---

## Local development tips

```bash
# Watch bot logs
python bot.py

# Run dashboard locally
streamlit run app.py

# Test with ngrok (for mobile testing)
ngrok http 8501
```
