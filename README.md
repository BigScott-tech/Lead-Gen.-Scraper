# Lead Generation Engine v2.0

A Python-based lead scraping system for **NorthOrbis / AIMA** outreach — extracts emails, phone numbers, company names, and social handles from **7 platforms** using a single RapidAPI key.

---

## What's new in v2.0

| Area | Change |
|---|---|
| **TikTok** | Full hashtag + keyword scraping — new platform |
| **YouTube** | Channel/video description scraping — new platform |
| **RapidAPI layer** | All social scrapers now hit real API endpoints instead of placeholder stubs |
| **Free fallback** | Instagram falls back to instaloader; web uses DuckDuckGo — works without a key |
| **Async bot fix** | `/scrape` no longer blocks the Telegram event loop (was causing timeouts) |
| **`/niches` command** | List available niches directly in Telegram |
| **`/platforms` command** | List all available platforms in Telegram |
| **Better dedup** | Deduplication now also checks social handles |
| **phonenumbers lib** | More accurate international phone validation |
| **Leaner deps** | Removed unused Scrapy/Selenium from requirements |

---

## Architecture

```
Lead Gen Engine/
├── scrapers/
│   ├── web_scraper.py          ← DuckDuckGo → BeautifulSoup (free)
│   ├── social_scrapers.py      ← Platform scrapers (RapidAPI + fallbacks)
│   └── rapidapi_scrapers.py    ← Raw RapidAPI HTTP clients (NEW)
├── utils/
│   ├── lead_extractor.py       ← Regex email/phone/company extraction
│   ├── human_behavior.py       ← User-agent rotation, rate limiting
│   ├── validators.py           ← Validation & deduplication
│   └── proxy_manager.py        ← Proxy pool (future)
├── tests/
│   └── test_extraction.py
├── data/                       ← Output CSVs land here
├── config.yaml                 ← Main config (niches, platforms, RapidAPI hosts)
├── main.py                     ← CLI entry point
├── bot.py                      ← Telegram bot
├── scheduler.py                ← APScheduler wrapper
├── requirements.txt
├── .env.example                ← Copy to .env and fill in
└── render.yaml                 ← Render.com deploy config
```

---

## Quick start

### 1 — Clone & create venv

```bash
git clone <your-repo>
cd "Lead Gen Engine"
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
```

### 2 — Install dependencies

```bash
pip install -r requirements.txt
```

### 3 — Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_from_@BotFather
RAPIDAPI_KEY=your_rapidapi_key          # get free at rapidapi.com
```

### 4 — Run

```bash
# One-shot scrape (all platforms, all niches)
python main.py

# Telegram bot (remote control from your phone)
python bot.py
```

---

## Getting your RapidAPI key

1. Go to [rapidapi.com](https://rapidapi.com) and create a free account.
2. Search for any of the APIs below and subscribe to their **free tier**.
3. Copy the key from **Apps → My Apps → your app → Security**.

One key works across all platforms. Free tiers are generous enough for daily lead gen.

| Platform | RapidAPI host (default) | Notes |
|---|---|---|
| Instagram | `instagram-scraper-api2.p.rapidapi.com` | |
| Twitter/X | `twitter241.p.rapidapi.com` | |
| LinkedIn | `linkedin-data-api.p.rapidapi.com` | |
| TikTok | `tiktok-api23.p.rapidapi.com` | |
| YouTube | `youtube-v31.p.rapidapi.com` | |

> **Tip:** If a host goes stale (APIs change), swap it in `config.yaml → rapidapi.hosts` without touching code.

---

## Platforms

| Platform | Free fallback | Notes |
|---|---|---|
| `web` | ✅ DuckDuckGo + BeautifulSoup | Always works |
| `instagram` | ✅ instaloader | API preferred; instaloader blocked intermittently |
| `tiktok` | ❌ | Needs RAPIDAPI_KEY |
| `youtube` | ❌ | Needs RAPIDAPI_KEY |
| `twitter` | ❌ | Needs RAPIDAPI_KEY |
| `linkedin` | ❌ | Needs RAPIDAPI_KEY |
| `facebook` | ❌ | Requires Graph API (not implemented) |

---

## Telegram bot commands

```
/start                  — Initialise session + show settings
/help                   — Full command reference
/status                 — Current session settings + API key status
/niches                 — List available niches from config.yaml
/platforms              — List available platforms
/set_target <niche>     — Set niche (must match config.yaml)
/set_platforms p1,p2,…  — Select platforms (comma-separated)
/set_region <region>    — Region filter (e.g. Nigeria, U.S.)
/set_amount <n>         — Lead cap for next scrape
/scrape                 — Start scrape (runs in background, won't timeout)
/download               — Download latest CSV
```

---

## Output CSV columns

| Column | Description |
|---|---|
| `email` | Extracted email address |
| `phone` | Extracted phone number (normalised) |
| `company_name` | Extracted company name |
| `social_handle` | Instagram/TikTok/Twitter handle |
| `region` | Region filter applied during scrape |
| `source_url` | URL the lead was found on |
| `source_platform` | Platform name |
| `post_link` | Direct link to source post |
| `extracted_at` | ISO 8601 timestamp |

---

## Adding new niches

Edit `config.yaml`:

```yaml
niches:
  - name: "Real Estate"
    keywords:
      - "real estate agent"
      - "property manager"
      - "need property website"
      - "real estate marketing"
```

Then in Telegram: `/set_target Real Estate`

---

## Deploy to Render (free tier)

The `render.yaml` is already configured. Push to GitHub, connect your repo on [render.com](https://render.com), and add the two environment variables in the Render dashboard.

---

## Compliance

- Only scrapes **publicly available** data
- Respects rate limits and adds random delays
- Does not bypass authentication
- Log all activity in `scraper.log`
- Check robots.txt before scraping custom domains
- Verify compliance with GDPR / local laws for your target regions

---

## Troubleshooting

| Problem | Fix |
|---|---|
| All platforms return 0 leads | Set `RAPIDAPI_KEY` in `.env` |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |
| Instagram blocked | Use RapidAPI (set key), instaloader gets blocked intermittently |
| Bot timeout on `/scrape` | Fixed in v2 — scrape now runs off the event loop |
| RapidAPI 401 error | Invalid key or not subscribed to that specific API's plan |
| RapidAPI 429 error | Rate limit hit — the engine retries automatically with backoff |

---

**Version:** 2.0.0 | **Updated:** May 2026
