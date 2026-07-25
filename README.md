# Data Analyst Telegram Bot

A Telegram bot that answers data-analysis questions using an LLM and replies
with a single JSON object: `{"answer": ..., "log_url": "..."}`.

## Environment variables required

| Variable | Where to get it |
|---|---|
| `TELEGRAM_TOKEN` | From @BotFather on Telegram |
| `GEMINI_API_KEY` | From https://aistudio.google.com/apikey |
| `GITHUB_TOKEN` | GitHub → Settings → Developer settings → Personal access tokens (needs `gist` scope) |
| `GIST_ID` | Optional — leave blank on first run, a gist will be auto-created; then copy its ID here so logs persist across restarts |

## Run locally

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN="your-token"
export GEMINI_API_KEY="your-key"
export GITHUB_TOKEN="your-github-pat"
python bot.py
```

## Deploy

See deployment steps in the assignment guide (Render.com background worker).
