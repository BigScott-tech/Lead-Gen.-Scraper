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
| `scrapers/social_scrapers.py` | Discovers public posts/profiles/communities with `site:` searches for X/Twitter, LinkedIn, Facebook, Instagram, TikTok, and YouTube |
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
python main.py --query "website developer needed since 14-05-2026" --platforms x --amount 25 --format json
python main.py --query "#hvacontario" --platforms instagram --amount 30 --format json
python main.py --query "HVAC contractor Ontario" --platforms facebook --amount 30
python main.py --query "HVAC Ontario" --platforms instagram --regions Ontario --format json
python main.py --browser-login --browser-profile default
python main.py --browser-login --platforms x --browser-profile default
python main.py --browser --platforms tiktok --query "HVAC contractor" --amount 30 --browser-profile default
python main.py --browser --platforms tiktok --query "HVAC" --amount 200 --browser-profile default
python main.py --browser --platforms x --query "website developer needed since:2026-05-14" --amount 200 --browser-profile default --headful
python main.py --query "HVAC contractor Ontario" --platforms linkedin,facebook --browser-fallback --firefox-profile default
python main.py --query "HVAC contractor Ontario" --platforms linkedin --browser-fallback --custom-searches linkedin_posts,google --browser-app firefox --links-only
python main.py --query "HVAC contractor Ontario" --platforms linkedin --print-search-links --custom-searches all
python main.py --url-file urls.txt --amount 50
python main.py --url "https://www.google.com/url?sa=t&source=web&rct=j&url=https://www.linkedin.com/jobs/web-developer-jobs-denver-co"
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
/browser_login x default
/browser_search -q "HVAC contractor" -n 30 --profile default
/browser_search -p x -q "website developer needed since:2026-05-14" -n 200 --profile default --headful
/browser_links -q "HVAC contractor" -p linkedin --custom all
/custom_searches
/export --format json
/scrape
/download
```

`/browser_login` opens a browser window on the machine running the bot, not inside Telegram. Telegram's in-app browser is useful for links, but its cookies do not become Playwright cookies. Log in to X or TikTok in the local Playwright window, then run `/browser_search`.

You can also extract contacts directly from a single page URL with `/extract_url <url>` in the bot or `python main.py --url "<page_url>"` from the CLI.

## Platforms

| Platform | Method |
|---|---|
| `web` | DuckDuckGo HTML search + direct page scraping |
| `twitter` | Public `site:x.com` discovery, optional logged-in browser search |
| `linkedin` | Public `site:linkedin.com/posts` discovery |
| `facebook` | Public `site:facebook.com/groups` and post discovery |
| `instagram` | Instaloader when available, public search fallback |
| `tiktok` | Public `site:tiktok.com` discovery |
| `youtube` | Public `site:youtube.com/watch` and Shorts discovery |

## Browser Mode

X/Twitter and TikTok have optional logged-in browser scrapers. They use persistent
local Playwright profiles under `profiles/browser/<profile>/<platform>`.

For X lead searches:

```bash
python main.py --browser-login --platforms x --browser-profile default
python main.py --browser --platforms x --query "website developer needed since:2026-05-14" --amount 200 --browser-profile default --headful
```

The X browser scraper opens X live search, scrolls results, captures each post URL,
user profile URL, handle, post text, visible timestamp, emails/phones when present,
and filters out posts outside the requested date window. If you provide `since:`,
it adds `until:` for tomorrow so "through today" is included by X's exclusive
`until` operator.

TikTok browser mode:

- Uses a persistent local Playwright profile under `profiles/browser/<profile>/tiktok`
- Opens TikTok search, scrolls results, collects video/profile URLs, visits creator profiles, and extracts visible bio/contact text
- Caps browser runs to 20-250 leads by default; keep larger runs slower and review-heavy
- Gives next-step choices in Telegram: continue a small batch, switch account profile, or return to default public search

This is not anonymity. It is a logged-in local browser session. Smaller batches, human review, and separate profiles can reduce repeated session pressure, but they do not make automation invisible.

## Manual Browser Fallback

Use the new `--browser-fallback` option to open platform search pages in your local Firefox browser while the CLI run continues. This is useful when public search discovery is being blocked or when you want to inspect signed-in search results manually.

```bash
python main.py --query "HVAC contractor Ontario" --platforms linkedin,facebook --browser-fallback --firefox-profile default
```

If Firefox is already open, the fallback opens new tabs in that existing signed-in session. You can also choose another local browser:

```bash
python main.py --query "website developer needed" --platforms linkedin --browser-fallback --browser-app brave
python main.py --query "website developer needed" --platforms linkedin --print-search-links --links-only
```

## Custom Search Links

Edit `custom_searches.links` in `config.yaml` to add your own signed-in search pages. Link templates support placeholders such as `{query_plus}`, `{raw_query_plus}`, `{region_plus}`, and `{niche_plus}`.

```yaml
custom_searches:
  links:
    - name: "my_linkedin_sales_search"
      enabled: true
      platforms: ["linkedin"]
      url: "https://www.linkedin.com/search/results/people/?keywords={query_plus}"
```

Use them from the CLI:

```bash
python main.py -q "HVAC contractor Ontario" -p linkedin --browser-fallback --custom-searches my_linkedin_sales_search
python main.py -q "HVAC contractor Ontario" --custom-link "https://www.google.com/search?q={query_plus}+email" --browser-fallback --links-only
python main.py --list-custom-searches
```

## Output

CSV files are written to `data/` with these columns:

```text
email, phone, company_name, social_handle, region, source_url,
source_platform, post_link, profile_url, bio_link, title, snippet,
search_query, lead_type, confidence, lead_score, lead_reason, extracted_at
```

For social searches, `post_link` points to the discovered post/reel/video when available,
`profile_url` points to the creator/company/group profile, and `bio_link` captures a
visible external profile/bio link when a public result exposes one.

## Notes

- No paid scraping API is required.
- Results depend on what public search engines can see.
- X/Twitter, LinkedIn, Facebook, TikTok, and Instagram heavily limit direct unauthenticated scraping, so public search discovery is more reliable and safer than trying to bypass login walls.
- Use conservative rates and only process publicly available data.
