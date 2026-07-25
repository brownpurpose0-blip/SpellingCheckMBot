import logging
import os

import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("spellingcheckmbot")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
LANGUAGETOOL_URL = os.environ.get("LANGUAGETOOL_URL", "https://api.languagetool.org/v2/check")
MAX_TEXT_LENGTH = 4000  # keep well under LanguageTool's public API limits

KNOWN_LANGUAGES = {
    "auto": "Auto-detect",
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt-BR": "Portuguese (Brazil)",
    "pt-PT": "Portuguese (Portugal)",
    "it": "Italian",
    "nl": "Dutch",
    "pl": "Polish",
    "ru": "Russian",
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *SpellingCheckMBot*\n\n"
        "Send me any text and I'll check its spelling and grammar.\n\n"
        "Use /help to see all commands.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Just send me a message and I'll check it automatically.\n\n"
        "*Commands*\n"
        "/check <text> — explicitly check a piece of text\n"
        "/fix <text> — return only the corrected text\n"
        "/setlang <code> — set your checking language (e.g. `en-US`, `fr`, `de`)\n"
        "/lang — show your current language and the list of supported codes\n"
        "/stats — how many checks you've run and issues found\n\n"
        f"Max text length per check: {MAX_TEXT_LENGTH} characters."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def setlang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /setlang <code> — e.g. /setlang en-US\nSee /lang for supported codes.")
        return
    code = context.args[0]
    await db.set_language(update.effective_user.id, code)
    label = KNOWN_LANGUAGES.get(code, code)
    await update.message.reply_text(f"Language set to *{label}* (`{code}`).", parse_mode=ParseMode.MARKDOWN)


async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = await db.get_language(update.effective_user.id)
    label = KNOWN_LANGUAGES.get(current, current)
    supported = "\n".join(f"  `{code}` — {name}" for code, name in KNOWN_LANGUAGES.items())
    await update.message.reply_text(
        f"Current language: *{label}* (`{current}`)\n\nSupported shortcuts:\n{supported}\n\n"
        "LanguageTool supports many more codes — /setlang accepts any valid one.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = await db.get_stats(update.effective_user.id)
    await update.message.reply_text(
        f"Checks run: {stats['checks_count']}\nIssues found (total): {stats['issues_found']}"
    )


async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text or ""
    if not text:
        await update.message.reply_text("Usage: /check <text> (or reply to a message with /check)")
        return
    await handle_text_check(update, context, text)


async def fix_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text or ""
    if not text:
        await update.message.reply_text("Usage: /fix <text> (or reply to a message with /fix)")
        return
    await handle_text_check(update, context, text, fix_only=True)


async def plain_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if not text.strip():
        return
    await handle_text_check(update, context, text)


async def handle_text_check(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, fix_only: bool = False):
    if len(text) > MAX_TEXT_LENGTH:
        await update.message.reply_text(
            f"That's {len(text)} characters — please keep it under {MAX_TEXT_LENGTH}."
        )
        return

    user_id = update.effective_user.id
    language = await db.get_language(user_id)

    try:
        matches = await check_text(text, language)
    except httpx.HTTPStatusError as exc:
        await update.message.reply_text(f"Grammar service error: HTTP {exc.response.status_code}. Try again shortly.")
        return
    except Exception:
        logger.exception("check failed")
        await update.message.reply_text("Something went wrong reaching the grammar checker. Try again shortly.")
        return

    await db.record_check(user_id, len(matches))

    if fix_only:
        corrected = apply_fixes(text, matches)
        await update.message.reply_text(corrected if corrected.strip() else "(no text)")
        return

    await update.message.reply_text(format_matches(text, matches))


async def check_text(text: str, language: str):
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            LANGUAGETOOL_URL,
            data={"text": text, "language": language},
        )
        response.raise_for_status()
        data = response.json()
        return data.get("matches", [])


def apply_fixes(text: str, matches: list) -> str:
    fixable = [m for m in matches if m.get("replacements")]
    fixable.sort(key=lambda m: m["offset"], reverse=True)

    result = text
    for m in fixable:
        offset = m["offset"]
        length = m["length"]
        replacement = m["replacements"][0]["value"]
        result = result[:offset] + replacement + result[offset + length:]
    return result


def format_matches(text: str, matches: list) -> str:
    if not matches:
        return "✅ No issues found — looks good!"

    lines = [f"🔍 Found {len(matches)} issue(s):\n"]
    for i, m in enumerate(matches[:15], start=1):
        offset, length = m["offset"], m["length"]
        snippet_start = max(0, offset - 20)
        snippet_end = min(len(text), offset + length + 20)
        before = text[snippet_start:offset]
        bad = text[offset:offset + length]
        after = text[offset + length:snippet_end]
        snippet = f"{before}«{bad}»{after}".replace("\n", " ")

        category = (m.get("rule", {}).get("category", {}) or {}).get("name", "Grammar")
        message = m.get("message", "")
        replacements = [r["value"] for r in m.get("replacements", [])[:3]]
        suggestion = f"Suggestions: {', '.join(replacements)}" if replacements else "No suggestion available"

        lines.append(f"{i}. [{category}] {message}\n   ...{snippet}...\n   {suggestion}\n")

    if len(matches) > 15:
        lines.append(f"…and {len(matches) - 15} more issue(s).")

    lines.append("\nSend /fix with the same text to get a corrected version.")
    return "\n".join(lines)


async def post_init(application: Application):
    await db.init_db()


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set.")

    application = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("setlang", setlang_cmd))
    application.add_handler(CommandHandler("lang", lang_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("check", check_cmd))
    application.add_handler(CommandHandler("fix", fix_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_message))

    logger.info("Starting SpellingCheckMBot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
