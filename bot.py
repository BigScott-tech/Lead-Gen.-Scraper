"""
bot.py — Telegram bot for remote control of the Lead Scraping Engine.

Commands
────────
/start                           Initialise session
/help                            Full command list
/status                          Show current settings
/set_target <niche>              Pick a niche (must match config.yaml)
/set_platforms <p1,p2,…>        Choose platforms to scrape
/set_region <region>             Region filter for next scrape
/set_amount <n>                  Lead cap for next scrape
/scrape                          Run scrape with current settings  (async-safe)
/download                        Download latest CSV
/niches                          List available niches
/platforms                       List available platforms
"""

from __future__ import annotations

import asyncio
import logging
import os
from functools import partial
from typing import Dict

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()

# ── Validate token early ──────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN is not set. "
        "Copy .env.example → .env and fill it in."
    )

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Engine (import after logging is set up) ───────────────────────────────────
from main import LeadScrappingEngine  # noqa: E402

engine = LeadScrappingEngine("config.yaml")

ALL_PLATFORMS = [p.get("name") for p in engine.config.get("platforms", [])]


# ─────────────────────────────────────────────────────────────────────────────
# Session helpers
# ─────────────────────────────────────────────────────────────────────────────

def _session(context: ContextTypes.DEFAULT_TYPE) -> Dict:
    return context.user_data.setdefault("session", {
        "target_niche": None,
        "platforms": list(
            engine.config.get("bot", {}).get("default_platforms", ALL_PLATFORMS)
        ),
        "region": engine.config.get("bot", {}).get("default_region", ""),
        "amount": engine.config.get("bot", {}).get("default_amount", 50),
        "last_filepath": None,
    })


def _available_niches() -> list[str]:
    return [n.get("name", "") for n in engine.config.get("niches", [])]


# ─────────────────────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _session(context)
    await update.message.reply_text(
        "🤖 *Lead Generation Bot* is ready!\n\n"
        f"Platforms: `{', '.join(state['platforms'])}`\n"
        f"Region: `{state['region'] or 'none'}`\n"
        f"Lead cap: `{state['amount']}`\n\n"
        "Send /help to see all commands.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📋 *Available Commands*\n\n"
        "/start — initialise session\n"
        "/help — this message\n"
        "/status — current session settings\n"
        "/niches — list available niches\n"
        "/platforms — list available platforms\n"
        "/set\\_target `<niche>` — set target niche\n"
        "/set\\_platforms `<p1,p2,…>` — set platforms\n"
        "/set\\_region `<region>` — set region filter\n"
        "/set\\_amount `<n>` — set lead cap\n"
        "/scrape — run scraping job\n"
        "/download — download latest CSV\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _session(context)
    api_key_set = bool(os.getenv("RAPIDAPI_KEY", "").strip())
    await update.message.reply_text(
        "⚙️ *Current Session*\n\n"
        f"Target niche: `{state['target_niche'] or 'all niches'}`\n"
        f"Platforms: `{', '.join(state['platforms'])}`\n"
        f"Region: `{state['region'] or 'none'}`\n"
        f"Lead cap: `{state['amount']}`\n"
        f"RapidAPI key: {'✅ set' if api_key_set else '❌ not set (free fallback only)'}",
        parse_mode="Markdown",
    )


async def niches_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    names = _available_niches()
    await update.message.reply_text(
        "📌 *Available Niches*\n\n" + "\n".join(f"• {n}" for n in names),
        parse_mode="Markdown",
    )


async def platforms_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🌐 *Available Platforms*\n\n" + "\n".join(f"• {p}" for p in ALL_PLATFORMS),
        parse_mode="Markdown",
    )


async def set_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _session(context)
    if not context.args:
        await update.message.reply_text("Usage: /set\\_target `<niche>`\n\nSee /niches for options.",
                                         parse_mode="Markdown")
        return
    niche = " ".join(context.args).strip()
    available = _available_niches()
    if niche not in available:
        await update.message.reply_text(
            f"❌ Niche not found.\n\nAvailable:\n" + "\n".join(f"• {n}" for n in available)
        )
        return
    state["target_niche"] = niche
    await update.message.reply_text(f"✅ Target niche set to: *{niche}*", parse_mode="Markdown")


async def set_platforms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _session(context)
    if not context.args:
        await update.message.reply_text(
            "Usage: /set\\_platforms `web,tiktok,instagram,linkedin,twitter,facebook,youtube`",
            parse_mode="Markdown",
        )
        return
    requested = [p.strip().lower() for p in " ".join(context.args).split(",") if p.strip()]
    valid = [p for p in requested if p in ALL_PLATFORMS]
    invalid = [p for p in requested if p not in ALL_PLATFORMS]

    if not valid:
        await update.message.reply_text(f"❌ No valid platforms. Available: {', '.join(ALL_PLATFORMS)}")
        return

    state["platforms"] = valid
    msg = f"✅ Platforms set to: `{', '.join(valid)}`"
    if invalid:
        msg += f"\n⚠️ Ignored (unknown): `{', '.join(invalid)}`"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def set_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _session(context)
    if not context.args:
        available = engine.config.get("regions", [])
        await update.message.reply_text(
            "Usage: /set\\_region `<region>`\n\n"
            "Common options:\n" + "\n".join(f"• {r}" for r in available),
            parse_mode="Markdown",
        )
        return
    state["region"] = " ".join(context.args).strip()
    await update.message.reply_text(f"✅ Region set to: *{state['region']}*", parse_mode="Markdown")


async def set_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _session(context)
    if not context.args:
        await update.message.reply_text("Usage: /set\\_amount `<number>`", parse_mode="Markdown")
        return
    try:
        n = int(context.args[0])
        if n <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Please provide a positive integer.")
        return
    state["amount"] = n
    await update.message.reply_text(f"✅ Lead cap set to: *{n}*", parse_mode="Markdown")


async def scrape(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Run the scraping job in a thread-pool so it doesn't block the Telegram
    event loop (which would cause timeouts on long scrapes).
    """
    state = _session(context)
    await update.message.reply_text(
        "⏳ Scraping started…\n\n"
        f"Platforms: `{', '.join(state['platforms'])}`\n"
        f"Niche: `{state['target_niche'] or 'all'}`\n"
        f"Region: `{state['region'] or 'none'}`\n"
        f"Cap: `{state['amount']}`\n\n"
        "This may take a few minutes. I'll message you when done.",
        parse_mode="Markdown",
    )

    loop = asyncio.get_event_loop()
    run = partial(
        engine.run_scraping,
        platforms=state["platforms"],
        niche=state["target_niche"],
        regions=[state["region"]] if state["region"] else [],
        max_leads=state["amount"],
    )

    try:
        leads = await loop.run_in_executor(None, run)
    except Exception as exc:
        logger.error(f"Scrape error: {exc}", exc_info=True)
        await update.message.reply_text(f"❌ Scrape failed: {exc}")
        return

    if not leads:
        await update.message.reply_text(
            "⚠️ No leads found.\n\n"
            "Tips:\n"
            "• Set RAPIDAPI\\_KEY in your .env for better results\n"
            "• Try a different niche with /set\\_target\n"
            "• Broaden the region with /set\\_region",
            parse_mode="Markdown",
        )
        return

    filepath = engine.save_leads()
    state["last_filepath"] = filepath

    # Platform breakdown
    by_platform: Dict[str, int] = {}
    for lead in leads:
        p = lead.get("source_platform", "unknown")
        by_platform[p] = by_platform.get(p, 0) + 1

    breakdown = "\n".join(f"  • {k}: {v}" for k, v in sorted(by_platform.items()))
    await update.message.reply_text(
        f"✅ *Scrape complete!*\n\n"
        f"Total leads: *{len(leads)}*\n\n"
        f"By platform:\n{breakdown}\n\n"
        f"Send /download to get the CSV.",
        parse_mode="Markdown",
    )


async def download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _session(context)
    filepath = state.get("last_filepath")
    if not filepath:
        await update.message.reply_text("No results yet. Run /scrape first.")
        return
    try:
        with open(filepath, "rb") as f:
            await update.message.reply_document(document=f, filename=filepath.split("/")[-1])
    except FileNotFoundError:
        await update.message.reply_text("File not found. Run /scrape again.")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    handlers = [
        CommandHandler("start", start),
        CommandHandler("help", help_command),
        CommandHandler("status", status),
        CommandHandler("niches", niches_command),
        CommandHandler("platforms", platforms_command),
        CommandHandler("set_target", set_target),
        CommandHandler("set_platforms", set_platforms),
        CommandHandler("set_region", set_region),
        CommandHandler("set_amount", set_amount),
        CommandHandler("scrape", scrape),
        CommandHandler("download", download),
    ]
    for h in handlers:
        app.add_handler(h)

    logger.info("🤖 Telegram bot started — polling …")
    app.run_polling()


if __name__ == "__main__":
    main()
