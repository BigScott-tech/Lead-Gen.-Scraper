# Lead Generation Engine

A free/open-source lead discovery system for finding public buying-intent posts and contact details without paid scraping APIs.

The engine is built around local search planning plus public web discovery. For a query like:

```text
website developer needed since 10-05-2026
```

it generates platform-specific searches such as:

```text
"website developer needed" since:2026-05-10 -filter:retweets site:x.com
"website developer needed" after:2026-05-10 site:linkedin.com/posts
#hvacontario site:instagram.com
```

Then it extracts emails, phones, company names, social handles, post/profile links, snippets, score reasons, and source URLs from public result pages and reachable HTML.

## How It Works

| Layer | Purpose |
|---|---|
| `utils/search_planner.py` | Turns niches, regions, dates, and free-form intent into smart search queries |
| `utils/lead_scoring.py` | Offline keyword scoring for urgency, buying intent, and contact quality |
| `utils/lead_store.py` | SQLite persistence so the same lead is not sent again tomorrow |
| `scrapers/web_scraper.py` | Uses DuckDuckGo HTML search and BeautifulSoup, no API key |
| `scrapers/social_scrapers.py` | Discovers public posts/profiles with `site:` searches for X/Twitter, LinkedIn, Facebook, Instagram, TikTok, and YouTube |
| `main.py` | Runs platform scrapers, validates, scores, deduplicates, and saves CSV/JSON output |
| `bot.py` | Telegram workflow for setting platforms, niches, regions, search text, and downloading results |

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_from_BotFather
TELEGRAM_ADMIN_IDS=123456789
TELEGRAM_ADMIN_TOKEN=optional_shared_auth_token
```

`TELEGRAM_ADMIN_IDS` is recommended. `TELEGRAM_ADMIN_TOKEN` enables `/auth <token>` if you want a passphrase-style unlock.

## Run

```bash
python main.py --query "website developer needed since 12-05-2026" --platforms twitter,linkedin --amount 25
python main.py --query "HVAC Ontario" --platforms instagram --regions Ontario --format json
python main.py --browser-login --browser-profile default
python main.py --browser --platforms tiktok --query "HVAC contractor" --amount 30 --browser-profile default
python bot.py
```

Browser mode uses Playwright. After installing dependencies, install Chromium once:

```bash
python -m playwright install chromium
```

## Telegram Commands

```text
/start
/help
/status
/auth <admin_token>
/niches
/platforms
/set_target <niche>
/set_platforms web,twitter,linkedin
/set_region United States
/set_amount 50
/set_query website developer needed since 10-05-2026
/search -p x,ig -q "website developer needed since 12-05-2026" -n 20
/search -p instagram -q "HVAC" -r Ontario -n 30
/config default_region Ontario
/monitor -p x -q "website developer needed" --every 6h
/jobs
/deep
/browser_login tiktok default
/browser_search -q "HVAC contractor" -n 30 --profile default
/export --format json
/scrape
/download
```

`/browser_login` opens a browser window on the machine running the bot, not inside Telegram. Telegram's in-app browser is useful for links, but its cookies do not become Playwright cookies. Log in to TikTok in the local Playwright window, then run `/browser_search`.

## Platforms

| Platform | Method |
|---|---|
| `web` | DuckDuckGo HTML search + direct page scraping |
| `twitter` | Public `site:x.com` and `site:twitter.com` discovery |
| `linkedin` | Public `site:linkedin.com/posts` discovery |
| `facebook` | Public `site:facebook.com/groups` and post discovery |
| `instagram` | Instaloader when available, public search fallback |
| `tiktok` | Public `site:tiktok.com` discovery |
| `youtube` | Public `site:youtube.com/watch` and Shorts discovery |

## Browser Mode

TikTok has an optional logged-in browser scraper:

- Uses a persistent local Playwright profile under `profiles/browser/<profile>/tiktok`
- Opens TikTok search, scrolls results, collects video/profile URLs, visits creator profiles, and extracts visible bio/contact text
- Caps browser runs to 20-50 leads to reduce account pressure
- Gives next-step choices in Telegram: continue a small batch, switch account profile, or return to default public search

This is not anonymity. It is a logged-in local browser session. Smaller batches, human review, and separate profiles can reduce repeated session pressure, but they do not make automation invisible.

## Output

CSV files are written to `data/` with these columns:

```text
email, phone, company_name, social_handle, region, source_url,
source_platform, post_link, profile_url, title, snippet, search_query,
lead_score, lead_reason, extracted_at
```

## Notes

- No paid scraping API is required.
- Results depend on what public search engines can see.
- X/Twitter, LinkedIn, Facebook, TikTok, and Instagram heavily limit direct unauthenticated scraping, so public search discovery is more reliable and safer than trying to bypass login walls.
- Use conservative rates and only process publicly available data.
