"""
Data Analyst Telegram Bot
- Receives a data-analysis question on Telegram
- Asks an LLM (Gemini) to work out the answer
- Replies with EXACTLY one JSON object: {"answer": ..., "log_url": "..."}
- Logs every run as a line in a JSONL file, pushed to a public GitHub Gist
  so log_url is a public, wget-able link.
"""

import os
import json
import time
import logging
import requests
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from google import genai

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- CONFIG (read from environment variables, set these on your host) ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]          # from BotFather
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]          # from aistudio.google.com/apikey
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]              # GitHub Personal Access Token (repo/gist scope)
GIST_ID = os.environ.get("GIST_ID", "")                # will be created automatically on first run if empty

genai_client = genai.Client(api_key=GEMINI_API_KEY)

# in-memory conversation buffer per chat (to handle multi-turn tasks)
chat_history = {}

# ---------- Gist-based JSONL logging (gives us a public wget-able URL) ----------
GIST_FILENAME = "run.jsonl"
_gist_cache = {"id": GIST_ID, "content": ""}


def _gist_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def _ensure_gist():
    """Create the gist once if it doesn't exist yet, and return its raw URL."""
    if _gist_cache["id"]:
        return _gist_cache["id"]

    resp = requests.post(
        "https://api.github.com/gists",
        headers=_gist_headers(),
        json={
            "description": "Data Analyst Telegram Bot run log",
            "public": True,
            "files": {GIST_FILENAME: {"content": "{}\n"}},
        },
        timeout=15,
    )
    resp.raise_for_status()
    gist_id = resp.json()["id"]
    _gist_cache["id"] = gist_id
    logger.info("Created new gist: %s (set GIST_ID env var to this to persist across restarts)", gist_id)
    return gist_id


def append_log(entry: dict):
    """Append one JSON line to the gist file and return its public raw URL."""
    gist_id = _ensure_gist()

    # fetch current content, append, push back
    get_resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=_gist_headers(), timeout=15)
    get_resp.raise_for_status()
    current = get_resp.json()["files"].get(GIST_FILENAME, {}).get("content", "")

    new_content = current + json.dumps(entry, ensure_ascii=False) + "\n"

    patch_resp = requests.patch(
        f"https://api.github.com/gists/{gist_id}",
        headers=_gist_headers(),
        json={"files": {GIST_FILENAME: {"content": new_content}}},
        timeout=15,
    )
    patch_resp.raise_for_status()
    raw_url = patch_resp.json()["files"][GIST_FILENAME]["raw_url"]
    return raw_url


# ---------- LLM call ----------
SYSTEM_PROMPT = """You are a careful data analyst. You will receive a data-analysis
question, possibly with inline data or a reference to a public dataset (e.g. MOSPI).
Work out the correct answer using reasoning and any numbers given in the message.
The user message will tell you the exact JSON shape required for the "answer" field
(for example: {"state": "<state name>"}). Follow that shape exactly.

Respond with ONLY a raw JSON object with a single key "answer" whose value matches
the requested shape. Do not include any other text, explanation, or markdown
formatting — just the JSON object, e.g.:
{"answer": {"state": "Assam"}}
"""


def ask_llm(conversation: list[str]) -> dict:
    """conversation: list of past user messages in this chat (last one is the one to answer)."""
    prompt = SYSTEM_PROMPT + "\n\nConversation so far:\n" + "\n".join(
        f"- {m}" for m in conversation
    ) + "\n\nAnswer the LAST message above. Respond with only the JSON object."

    response = genai_client.models.generate_content(
        model="gemini-1.5-flash",
        contents=prompt,
    )
    text = response.text.strip()

    # strip accidental markdown fences if the model adds them
    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json\n", "", 1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("LLM did not return valid JSON, wrapping raw text: %s", text)
        parsed = {"answer": text}

    return parsed


# ---------- Telegram handlers ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text or ""

    chat_history.setdefault(chat_id, []).append(user_text)
    # keep only the last 10 messages of context to stay cheap
    history = chat_history[chat_id][-10:]

    start = time.time()
    try:
        llm_result = ask_llm(history)
        answer_value = llm_result.get("answer")
        error_note = None
    except Exception as e:
        logger.exception("LLM call failed")
        answer_value = None
        error_note = f"{type(e).__name__}: {str(e)[:300]}"

    log_entry = {
        "chat_id": chat_id,
        "timestamp": time.time(),
        "incoming_message": user_text,
        "conversation_context": history,
        "llm_output": answer_value,
        "error": error_note,
        "latency_sec": round(time.time() - start, 2),
    }
    try:
        log_url = append_log(log_entry)
    except Exception:
        logger.exception("Failed to write log to gist")
        log_url = "https://example.com/log-failed"

    if error_note:
        # Still send something back so we can see what went wrong, instead of silence.
        final_reply = {"answer": None, "error": error_note, "log_url": log_url}
    else:
        final_reply = {"answer": answer_value, "log_url": log_url}

    try:
        await update.message.reply_text(json.dumps(final_reply, ensure_ascii=False))
    except Exception:
        logger.exception("Failed to send reply to Telegram")


def _start_dummy_port_listener():
    """Render's free tier only offers Web Services, which must bind a port
    or Render will keep restarting the process. This starts a tiny HTTP
    server in a background thread just to satisfy that health check —
    it has nothing to do with the bot's actual logic."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    port = int(os.environ.get("PORT", "10000"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running.")

        def log_message(self, format, *args):
            pass  # silence noisy request logs

    server = HTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Dummy port listener started on port %s", port)


def main():
    import asyncio
    _start_dummy_port_listener()
    # Python 3.14 no longer auto-creates an event loop on the main thread;
    # python-telegram-bot 21.6 still relies on asyncio.get_event_loop() finding one.
    # Explicitly create and set one here so run_polling() works on any Python version.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot starting (polling)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
