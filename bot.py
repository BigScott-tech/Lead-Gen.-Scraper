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
/set_query <query>               Set free-form search intent
/search <query>                  Set query and run scrape immediately
/export --format csv|json        Download latest results
/config <key> <value>            Set session defaults
/monitor <query>                 Run recurring searches
/jobs                            List active monitors
/deep                            Enrich latest leads from reachable URLs
/browser_login                   Open local TikTok login browser
/browser_search                  Run logged-in TikTok browser search
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
from typing import Dict, List

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes

load_dotenv()

# ── Validate token early ──────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ADMIN_TOKEN = os.getenv("TELEGRAM_ADMIN_TOKEN", "").strip()
TELEGRAM_ADMIN_IDS = {
    int(item.strip())
    for item in os.getenv("TELEGRAM_ADMIN_IDS", "").replace(" ", "").split(",")
    if item.strip().isdigit()
}
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
from utils.command_parser import parse_search_command  # noqa: E402

engine = LeadScrappingEngine("config.yaml")

ALL_PLATFORMS = [p.get("name") for p in engine.config.get("platforms", [])]
MONITOR_TASKS: Dict[int, List[asyncio.Task]] = {}


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
        "search_query": "",
        "last_filepath": None,
        "last_format": "csv",
        "browser_profile": "default",
        "authorized": False,
    })


def _available_niches() -> list[str]:
    return [n.get("name", "") for n in engine.config.get("niches", [])]


def _is_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id if update.effective_user else None
    if not TELEGRAM_ADMIN_IDS and not TELEGRAM_ADMIN_TOKEN:
        return True
    if user_id in TELEGRAM_ADMIN_IDS:
        return True
    return bool(_session(context).get("authorized"))


async def _require_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if _is_authorized(update, context):
        return True
    await update.message.reply_text("Access locked. Send /auth <admin_token> first.")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _session(context)
    await update.message.reply_text(
        "🤖 *Lead Generation Bot* is ready!\n\n"
        f"Platforms: `{', '.join(state['platforms'])}`\n"
        f"Region: `{state['region'] or 'none'}`\n"
        f"Query: `{state['search_query'] or 'auto from niche'}`\n"
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
        "/set\\_query `<query>` — set smart search text\n"
        "/search `-p x,ig -q \"web dev\" -n 20` — scrape now\n"
        "/config `default_region NY` — set session default\n"
        "/monitor `-p x -q \"website needed\" --every 6h` — recurring search\n"
        "/jobs — list active monitor jobs\n"
        "/deep — enrich latest batch from reachable URLs\n"
        "/browser\\_login `tiktok default` — open local login browser\n"
        "/browser\\_search `-q \"HVAC\" -n 30 --profile default` — logged-in TikTok run\n"
        "/export `--format csv|json` — download latest results\n"
        "/auth `<admin_token>` — unlock bot if configured\n"
        "/scrape — run scraping job\n"
        "/download — download latest CSV\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _session(context)
    await update.message.reply_text(
        "⚙️ *Current Session*\n\n"
        f"Target niche: `{state['target_niche'] or 'all niches'}`\n"
        f"Platforms: `{', '.join(state['platforms'])}`\n"
        f"Region: `{state['region'] or 'none'}`\n"
        f"Query: `{state['search_query'] or 'auto from niche'}`\n"
        f"Lead cap: `{state['amount']}`\n"
        f"Last format: `{state['last_format']}`\n"
        f"Browser profile: `{state['browser_profile']}`\n"
        f"Search mode: `free/open sources only`",
        parse_mode="Markdown",
    )


async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _session(context)
    if not TELEGRAM_ADMIN_TOKEN and not TELEGRAM_ADMIN_IDS:
        await update.message.reply_text("Auth is not required for this bot.")
        return
    if update.effective_user and update.effective_user.id in TELEGRAM_ADMIN_IDS:
        state["authorized"] = True
        await update.message.reply_text("Authorized by admin chat ID.")
        return
    supplied = " ".join(context.args).strip()
    if TELEGRAM_ADMIN_TOKEN and supplied == TELEGRAM_ADMIN_TOKEN:
        state["authorized"] = True
        await update.message.reply_text("Authorized.")
        return
    await update.message.reply_text("Invalid admin token.")


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
    if not await _require_authorized(update, context):
        return
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
    if not await _require_authorized(update, context):
        return
    state = _session(context)
    if not context.args:
        await update.message.reply_text(
            "Usage: /set\\_platforms `web,tiktok,instagram,linkedin,twitter,facebook,youtube`",
            parse_mode="Markdown",
        )
        return
    parsed = parse_search_command(["-p", " ".join(context.args)])
    requested = parsed.platforms
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
    if not await _require_authorized(update, context):
        return
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
    if not await _require_authorized(update, context):
        return
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


async def set_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update, context):
        return
    state = _session(context)
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text(
            "Usage: /set\\_query `website developer needed since 10-05-2026`",
            parse_mode="Markdown",
        )
        return
    state["search_query"] = query
    await update.message.reply_text(f"✅ Search query set to: `{query}`", parse_mode="Markdown")


async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update, context):
        return
    state = _session(context)
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /config `<default_region|amount|platforms|query>` `<value>`",
            parse_mode="Markdown",
        )
        return
    key = context.args[0].strip().lower()
    value = " ".join(context.args[1:]).strip()
    if key in {"default_region", "region"}:
        state["region"] = value
    elif key in {"amount", "number", "leads"}:
        state["amount"] = max(1, int(value))
    elif key in {"platform", "platforms"}:
        parsed = parse_search_command(["-p", value])
        state["platforms"] = parsed.platforms or list(ALL_PLATFORMS)
    elif key in {"query", "search"}:
        state["search_query"] = value
    else:
        await update.message.reply_text("Unknown config key.")
        return
    await update.message.reply_text("✅ Session config updated.")


async def scrape(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Run the scraping job in a thread-pool so it doesn't block the Telegram
    event loop (which would cause timeouts on long scrapes).
    """
    if not await _require_authorized(update, context):
        return
    state = _session(context)
    await _run_scrape(update, context, state)


async def _run_scrape(update: Update, context: ContextTypes.DEFAULT_TYPE, state: Dict,
                      output_format: str = "csv", deep: bool = False) -> None:
    await update.message.reply_text(
        "⏳ Scraping started…\n\n"
        f"Platforms: `{', '.join(state['platforms'])}`\n"
        f"Niche: `{state['target_niche'] or 'all'}`\n"
        f"Region: `{state['region'] or 'none'}`\n"
        f"Query: `{state['search_query'] or 'auto from niche'}`\n"
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
        search_text=state["search_query"],
    )

    try:
        leads = await loop.run_in_executor(None, run)
        if deep and leads:
            leads = await loop.run_in_executor(None, partial(engine.deep_enrich_leads, leads))
    except Exception as exc:
        logger.error(f"Scrape error: {exc}", exc_info=True)
        await update.message.reply_text(f"❌ Scrape failed: {exc}")
        return

    if not leads:
        await update.message.reply_text(
            "⚠️ No leads found.\n\n"
            "Tips:\n"
            "• Try /search `website developer needed since 10-05-2026`\n"
            "• Try a different niche with /set\\_target\n"
            "• Broaden the region with /set\\_region",
            parse_mode="Markdown",
        )
        return

    filepath = engine.save_leads_json() if output_format == "json" else engine.save_leads()
    state["last_filepath"] = filepath
    state["last_format"] = output_format

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
        f"Send /download or /export to get the file.",
        parse_mode="Markdown",
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update, context):
        return
    try:
        parsed = parse_search_command(context.args)
    except ValueError as exc:
        await update.message.reply_text(f"Bad search command: {exc}")
        return
    if not parsed.query:
        await update.message.reply_text(
            "Usage: /search `-p x,ig -q \"website developer needed since 12-05-2026\" -n 20`",
            parse_mode="Markdown",
        )
        return
    state = _session(context)
    state["search_query"] = parsed.query
    if parsed.platforms:
        state["platforms"] = [p for p in parsed.platforms if p in ALL_PLATFORMS]
    if parsed.regions:
        state["region"] = ", ".join(parsed.regions)
    if parsed.amount:
        state["amount"] = parsed.amount
    await _run_scrape(update, context, state, output_format=parsed.output_format, deep=parsed.deep)


async def deep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update, context):
        return
    if not engine.all_leads:
        await update.message.reply_text("No latest batch to enrich. Run /search or /scrape first.")
        return
    await update.message.reply_text("Deep enrichment started for the latest batch.")
    loop = asyncio.get_event_loop()
    leads = await loop.run_in_executor(None, engine.deep_enrich_leads)
    state = _session(context)
    state["last_filepath"] = engine.save_leads()
    await update.message.reply_text(f"✅ Deep enrichment complete. Leads: {len(leads)}")


async def browser_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update, context):
        return
    platform = context.args[0] if context.args else "tiktok"
    profile = context.args[1] if len(context.args) > 1 else _session(context).get("browser_profile", "default")
    _session(context)["browser_profile"] = profile
    await update.message.reply_text(
        "Opening a local browser login window on the machine running this bot.\n\n"
        "Log in there, then return here and run /browser_search. "
        "Telegram's in-app browser cannot transfer its cookies into Playwright.",
    )
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, partial(engine.browser_login, platform=platform,
                                                 profile=profile, hold_seconds=180))
    except Exception as exc:
        await update.message.reply_text(f"Browser login failed: {exc}")
        return
    await update.message.reply_text(f"Login window closed for `{platform}` profile `{profile}`.",
                                    parse_mode="Markdown")


async def browser_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update, context):
        return
    try:
        parsed = parse_search_command(context.args)
    except ValueError as exc:
        await update.message.reply_text(f"Bad browser search command: {exc}")
        return
    state = _session(context)
    query = parsed.query or state.get("search_query")
    if not query:
        await update.message.reply_text(
            "Usage: /browser\\_search `-q \"HVAC\" -n 30 --profile default`",
            parse_mode="Markdown",
        )
        return
    amount = parsed.amount or state.get("amount", 30)
    profile = parsed.profile or state.get("browser_profile", "default")
    state["search_query"] = query
    state["browser_profile"] = profile
    await update.message.reply_text(
        f"Browser TikTok search started with profile `{profile}`. "
        "This mode intentionally caps runs to 20-50 leads.",
        parse_mode="Markdown",
    )
    loop = asyncio.get_event_loop()
    try:
        leads = await loop.run_in_executor(
            None,
            partial(engine.scrape_tiktok_browser, search_text=query,
                    max_leads=amount, profile=profile, headful=parsed.headful),
        )
    except Exception as exc:
        await update.message.reply_text(f"Browser TikTok search failed: {exc}")
        return

    filepath = engine.save_leads()
    state["last_filepath"] = filepath
    report = engine.last_browser_report
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Continue small batch", callback_data="browser_continue"),
            InlineKeyboardButton("Default search", callback_data="browser_default"),
        ],
        [InlineKeyboardButton("Switch account", callback_data="browser_switch_account")],
    ])
    await update.message.reply_text(
        "✅ Browser TikTok run complete.\n\n"
        f"Collected: {len(leads)}\n"
        f"Visited profiles: {report.visited_profiles if report else 'unknown'}\n"
        f"Profile: `{profile}`\n\n"
        "Browser mode is not anonymity; it is a slower logged-in mode with smaller batches.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def browser_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _is_authorized(update, context):
        await query.message.reply_text("Access locked. Send /auth <admin_token> first.")
        return
    state = _session(context)
    if query.data == "browser_continue":
        await query.message.reply_text(
            "Run `/browser_search` again to continue with the same query/profile.",
            parse_mode="Markdown",
        )
    elif query.data == "browser_default":
        state["platforms"] = ["tiktok"]
        await query.message.reply_text(
            "Switched back to default public search mode for TikTok. Run `/search -p tiktok -q \"...\"`.",
            parse_mode="Markdown",
        )
    elif query.data == "browser_switch_account":
        await query.message.reply_text(
            "Use `/browser_login tiktok another_profile_name` to open a separate browser profile.",
            parse_mode="Markdown",
        )


async def export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update, context):
        return
    fmt = "csv"
    args = list(context.args)
    if "--format" in args:
        idx = args.index("--format")
        if idx + 1 < len(args):
            fmt = args[idx + 1].lower()
    if fmt not in {"csv", "json"}:
        await update.message.reply_text("Format must be csv or json.")
        return
    filepath = engine.save_leads_json() if fmt == "json" else engine.save_leads()
    if not filepath:
        await update.message.reply_text("No results available to export.")
        return
    _session(context)["last_filepath"] = filepath
    with open(filepath, "rb") as f:
        await update.message.reply_document(document=f, filename=filepath.split("/")[-1])


def _parse_every(args: List[str]) -> tuple[List[str], int]:
    tokens = list(args)
    hours = 6
    if "--every" in tokens:
        idx = tokens.index("--every")
        if idx + 1 < len(tokens):
            raw = tokens[idx + 1].lower()
            if raw.endswith("h"):
                hours = int(raw[:-1])
            elif raw.endswith("m"):
                hours = max(1, int(raw[:-1]) // 60)
            else:
                hours = int(raw)
            del tokens[idx:idx + 2]
    return tokens, max(1, hours)


async def monitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update, context):
        return
    tokens, hours = _parse_every(context.args)
    parsed = parse_search_command(tokens)
    if not parsed.query:
        await update.message.reply_text("Usage: /monitor `-p x -q \"website needed\" --every 6h`",
                                        parse_mode="Markdown")
        return
    chat_id = update.effective_chat.id
    state = dict(_session(context))
    state["search_query"] = parsed.query
    if parsed.platforms:
        state["platforms"] = [p for p in parsed.platforms if p in ALL_PLATFORMS]
    if parsed.amount:
        state["amount"] = parsed.amount

    async def _job():
        while True:
            await asyncio.sleep(hours * 3600)
            run = partial(
                engine.run_scraping,
                platforms=state["platforms"],
                niche=state["target_niche"],
                regions=[state["region"]] if state["region"] else [],
                max_leads=state["amount"],
                search_text=state["search_query"],
            )
            leads = await asyncio.get_event_loop().run_in_executor(None, run)
            filepath = engine.save_leads()
            await context.bot.send_message(chat_id=chat_id, text=f"Monitor found {len(leads)} new leads.")
            if filepath:
                with open(filepath, "rb") as f:
                    await context.bot.send_document(chat_id=chat_id, document=f, filename=filepath.split("/")[-1])

    task = asyncio.create_task(_job())
    MONITOR_TASKS.setdefault(chat_id, []).append(task)
    await update.message.reply_text(f"✅ Monitor created: every {hours}h for `{parsed.query}`",
                                    parse_mode="Markdown")


async def jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update, context):
        return
    chat_id = update.effective_chat.id
    active = [task for task in MONITOR_TASKS.get(chat_id, []) if not task.done()]
    await update.message.reply_text(f"Active monitor jobs: {len(active)}")


async def download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update, context):
        return
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
        CommandHandler("auth", auth),
        CommandHandler("status", status),
        CommandHandler("niches", niches_command),
        CommandHandler("platforms", platforms_command),
        CommandHandler("set_target", set_target),
        CommandHandler("set_platforms", set_platforms),
        CommandHandler("set_region", set_region),
        CommandHandler("set_amount", set_amount),
        CommandHandler("set_query", set_query),
        CommandHandler("config", config_command),
        CommandHandler("search", search),
        CommandHandler("monitor", monitor),
        CommandHandler("jobs", jobs),
        CommandHandler("deep", deep),
        CommandHandler("browser_login", browser_login),
        CommandHandler("browser_search", browser_search),
        CommandHandler("export", export),
        CommandHandler("scrape", scrape),
        CommandHandler("download", download),
        CallbackQueryHandler(browser_callback, pattern="^browser_"),
    ]
    for h in handlers:
        app.add_handler(h)

    logger.info("🤖 Telegram bot started — polling …")
    app.run_polling()


if __name__ == "__main__":
    main()
