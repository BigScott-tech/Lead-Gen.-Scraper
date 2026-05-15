"""
main.py — Lead scraping orchestrator.

Run once:      python main.py
Scheduled:     set scheduler.enabled = true in config.yaml
Telegram bot:  python bot.py
"""

from __future__ import annotations

import csv
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from scrapers.web_scraper import WebScraper
from scrapers.social_scrapers import (
    LinkedInScraper,
    FacebookScraper,
    TwitterScraper,
    InstagramScraper,
    TikTokScraper,
    YouTubeScraper,
)
from scrapers.browser_tiktok import TikTokBrowserScraper, BrowserRunReport
from utils.validators import DataValidator, DeduplicateManager
from utils.search_planner import SearchPlanner
from utils.lead_scoring import LeadScorer
from utils.lead_store import LeadStore
from scheduler import ScrapingScheduler

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("scraper.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────

class LeadScrappingEngine:
    """Main orchestrator — initialise once, call run_scraping() as needed."""

    CSV_FIELDS = [
        "email", "phone", "company_name", "social_handle",
        "region", "source_url", "source_platform", "post_link", "profile_url",
        "title", "snippet", "search_query", "lead_score", "lead_reason",
        "extracted_at",
    ]

    def __init__(self, config_file: str = "config.yaml"):
        self.config = self._load_config(config_file)
        self.dedup = DeduplicateManager()
        self.scheduler = ScrapingScheduler()
        self.all_leads: List[Dict] = []
        self.search_planner = SearchPlanner(self.config)
        self.scorer = LeadScorer(self.config)
        store_path = self.config.get("deduplication", {}).get("sqlite_path", "data/leads.sqlite3")
        self.lead_store = LeadStore(store_path)
        self.last_browser_report: Optional[BrowserRunReport] = None

    # ── config ────────────────────────────────────────────────────────────────

    def _load_config(self, path: str) -> dict:
        try:
            with open(path) as f:
                cfg = yaml.safe_load(f)
            logger.info(f"Config loaded from {path}")
            return cfg
        except FileNotFoundError:
            logger.error(f"Config not found: {path}")
            sys.exit(1)
        except Exception as exc:
            logger.error(f"Config load error: {exc}")
            sys.exit(1)

    def _keywords_for_niche(self, niche_name: Optional[str]) -> List[str]:
        if not niche_name:
            return []
        for n in self.config.get("niches", []):
            if n.get("name") == niche_name:
                return n.get("keywords", [])
        return []

    def _region_terms(self, regions: Optional[List[str]]) -> List[str]:
        if regions is not None:
            return [r.strip() for r in regions if r and r.strip()]
        return [r.strip() for r in self.config.get("region_filter", []) if r]

    def _default_platforms(self) -> List[str]:
        return [
            p.strip().lower()
            for p in self.config.get("bot", {}).get(
                "default_platforms",
                ["web", "instagram", "tiktok", "twitter", "linkedin", "facebook", "youtube"],
            )
        ]

    # ── per-platform scrapers ─────────────────────────────────────────────────

    def scrape_web(self, niche: str = None, regions: List[str] = None,
                   max_urls: int = 10, search_text: str = "") -> List[Dict]:
        logger.info("── Web scraping ──")
        scraper = WebScraper(rate_limit=1.0)
        try:
            keywords = self._keywords_for_niche(niche)
            plan = self.search_planner.plan(
                query=search_text,
                niche_keywords=keywords,
                regions=self._region_terms(regions),
            )
            queries = self.search_planner.queries_for_platform("web", plan, max_queries=max_urls)
            return scraper.scrape_search_queries(
                plan.terms, plan.regions, max_results=max_urls, queries=queries
            )
        finally:
            scraper.close()

    def scrape_instagram(self, niche: str = None, regions: List[str] = None,
                          max_posts: int = 30, search_text: str = "") -> List[Dict]:
        logger.info("── Instagram scraping ──")
        platform_cfg = next(
            (p for p in self.config.get("platforms", []) if p.get("name") == "instagram"), {}
        )
        hashtags = list(platform_cfg.get("hashtags", []))
        if niche:
            hashtags.extend(self._keywords_for_niche(niche))
        if search_text:
            hashtags.insert(0, search_text)
        hashtags = list(dict.fromkeys(t.strip().lstrip("#") for t in hashtags if t))
        scraper = InstagramScraper(config=self.config)
        return scraper.search_hashtags(hashtags, regions=self._region_terms(regions),
                                       max_posts=max_posts, raw_query=search_text)

    def scrape_tiktok(self, niche: str = None, regions: List[str] = None,
                      max_videos: int = 30, search_text: str = "") -> List[Dict]:
        logger.info("── TikTok scraping ──")
        platform_cfg = next(
            (p for p in self.config.get("platforms", []) if p.get("name") == "tiktok"), {}
        )
        hashtags = list(platform_cfg.get("hashtags", []))
        keywords = self._keywords_for_niche(niche)
        if search_text:
            keywords = [search_text] + keywords
        scraper = TikTokScraper(config=self.config)
        return scraper.search_hashtags(hashtags, keywords=keywords,
                                       regions=self._region_terms(regions),
                                       max_videos=max_videos,
                                       raw_query=search_text)

    def scrape_youtube(self, niche: str = None, regions: List[str] = None,
                       max_results: int = 20, search_text: str = "") -> List[Dict]:
        logger.info("── YouTube scraping ──")
        platform_cfg = next(
            (p for p in self.config.get("platforms", []) if p.get("name") == "youtube"), {}
        )
        keywords = platform_cfg.get("search_keywords", [])
        if niche:
            keywords = self._keywords_for_niche(niche) + keywords
        if search_text:
            keywords = [search_text] + keywords
        scraper = YouTubeScraper(config=self.config)
        return scraper.search(keywords, regions=self._region_terms(regions),
                               max_results=max_results,
                               raw_query=search_text)

    def scrape_linkedin(self, niche: str = None, regions: List[str] = None,
                        search_text: str = "", max_results: int = 20) -> List[Dict]:
        logger.info("── LinkedIn scraping ──")
        scraper = LinkedInScraper(config=self.config)
        platform_cfg = next(
            (p for p in self.config.get("platforms", []) if p.get("name") == "linkedin"), {}
        )
        days = self.config.get("time_filters", {}).get("max_age_days", 7)
        leads: List[Dict] = []
        all_keywords = (
            self._keywords_for_niche(niche)
            + platform_cfg.get("search_keywords", [])
        )
        if search_text:
            all_keywords = [search_text] + all_keywords
        posts = scraper.search_public_posts(
            all_keywords or ["web developer"],
            days=days,
            regions=self._region_terms(regions),
            raw_query=search_text,
            max_results=max_results,
        )
        for post in posts:
            leads.extend(scraper.extract_from_post(post.get("text", ""), post.get("url", "")))
        # search_public_posts already returns normalized lead dicts.
        leads.extend(posts)
        return leads

    def scrape_facebook(self, niche: str = None, regions: List[str] = None,
                        search_text: str = "", max_results: int = 20) -> List[Dict]:
        logger.info("── Facebook scraping ──")
        scraper = FacebookScraper(config=self.config)
        days = self.config.get("time_filters", {}).get("max_age_days", 7)
        leads: List[Dict] = []
        keywords = self._keywords_for_niche(niche)
        if search_text:
            keywords = [search_text] + keywords
        if not keywords:
            for configured_niche in self.config.get("niches", []):
                keywords.extend(configured_niche.get("keywords", []))
        leads.extend(scraper.search_public_posts(
            keywords,
            days=days,
            regions=self._region_terms(regions),
            raw_query=search_text,
            max_results=max_results,
        ))
        return leads

    def scrape_twitter(self, niche: str = None, regions: List[str] = None,
                       search_text: str = "", max_results: int = 30) -> List[Dict]:
        logger.info("── Twitter scraping ──")
        scraper = TwitterScraper(config=self.config)
        days = self.config.get("time_filters", {}).get("max_age_days", 7)
        platform_cfg = next(
            (p for p in self.config.get("platforms", []) if p.get("name") == "twitter"), {}
        )
        keywords = (
            self._keywords_for_niche(niche)
            + platform_cfg.get("search_keywords", [])
        )
        if search_text:
            keywords = [search_text] + keywords
        return scraper.search_tweets(
            keywords or [],
            days=days,
            raw_query=search_text,
            max_results=max_results,
        )

    def _finalize_leads(self, leads: List[Dict], limit: int = None, persist: bool = True) -> List[Dict]:
        filtered = DataValidator.filter_leads(leads, require_email=False)
        unique: List[Dict] = []
        self.dedup.clear()
        for lead in filtered:
            if not self.dedup.is_duplicate(
                email=lead.get("email", ""),
                phone=lead.get("phone", ""),
                company=lead.get("company_name", ""),
                handle=lead.get("social_handle", ""),
            ):
                unique.append(lead)

        unique = self.scorer.score_many(unique)
        minimum_score = int(self.config.get("scoring", {}).get("minimum_score", 0) or 0)
        if minimum_score:
            unique = [lead for lead in unique if int(lead.get("lead_score") or 0) >= minimum_score]
        if limit:
            unique = unique[:limit]
        if persist and self.config.get("deduplication", {}).get("persistent", True):
            unique = self.lead_store.filter_new(unique)
        return unique

    def scrape_custom_url(self, url: str, max_leads: int = None, persist: bool = True) -> List[Dict]:
        logger.info("── Custom URL scraping ──")
        scraper = WebScraper(rate_limit=1.0)
        try:
            leads = scraper.scrape_url(url)
        finally:
            scraper.close()

        for lead in leads:
            if not lead.get("source_url"):
                lead["source_url"] = url
            if not lead.get("source_platform"):
                lead["source_platform"] = "web"

        return self._finalize_leads(leads, limit=max_leads or self.config.get("bot", {}).get("default_amount", 50), persist=persist)

    def browser_login(self, platform: str = "tiktok", profile: str = "default",
                      hold_seconds: int = 180) -> BrowserRunReport:
        platform = self.search_planner.normalize_platform(platform)
        if platform != "tiktok":
            raise ValueError("Browser login is currently implemented for TikTok only.")
        scraper = TikTokBrowserScraper(config=self.config, profile=profile, headless=False)
        self.last_browser_report = scraper.open_login_window(hold_seconds=hold_seconds)
        return self.last_browser_report

    def scrape_tiktok_browser(self, search_text: str, max_leads: int = 30,
                              profile: str = "default", headful: bool = False,
                              persist: bool = True) -> List[Dict]:
        scraper = TikTokBrowserScraper(
            config=self.config,
            profile=profile,
            headless=not headful,
        )
        leads, report = scraper.search(search_text, limit=max_leads)
        leads = DataValidator.filter_leads(leads, require_email=False)
        leads = self.scorer.score_many(leads)
        minimum_score = int(self.config.get("scoring", {}).get("minimum_score", 0) or 0)
        if minimum_score:
            leads = [lead for lead in leads if int(lead.get("lead_score") or 0) >= minimum_score]
        if persist and self.config.get("deduplication", {}).get("persistent", True):
            leads = self.lead_store.filter_new(leads)
        self.all_leads = leads
        self.last_browser_report = report
        return leads

    # ── main run ──────────────────────────────────────────────────────────────

    def run_scraping(
        self,
        platforms: List[str] = None,
        niche: str = None,
        regions: List[str] = None,
        max_leads: int = None,
        search_text: str = "",
        persist: bool = True,
    ) -> List[Dict]:
        """
        Run all enabled platform scrapers and return deduplicated leads.

        Args:
            platforms:  platforms to scrape; defaults to config bot.default_platforms
            niche:      niche name matching config.niches[].name
            regions:    list of region strings to filter searches
            max_leads:  cap on returned leads
            search_text: optional free-form query, e.g.
                         "website developer needed since 10-05-2026"

        Returns:
            List of lead dicts
        """
        platforms = [p.strip().lower() for p in (platforms or self._default_platforms()) if p]
        limit = max_leads or self.config.get("bot", {}).get("default_amount", 50)
        output_cfg = self.config.get("output", {})
        search_limit = output_cfg.get("search_limit", 15)
        region_terms = self._region_terms(regions)

        logger.info(
            "Starting scrape | platforms=%s | niche=%s | regions=%s | query=%s",
            platforms, niche, region_terms, search_text,
        )

        raw: List[Dict] = []

        dispatcher = {
            "web":       lambda: self.scrape_web(niche=niche, regions=region_terms,
                                                  max_urls=search_limit,
                                                  search_text=search_text),
            "instagram": lambda: self.scrape_instagram(niche=niche, regions=region_terms,
                                                        max_posts=next((p.get("max_posts", 30)
                                                            for p in self.config.get("platforms", [])
                                                            if p.get("name") == "instagram"), 30),
                                                        search_text=search_text),
            "tiktok":    lambda: self.scrape_tiktok(niche=niche, regions=region_terms,
                                                     max_videos=next((p.get("max_videos", 30)
                                                         for p in self.config.get("platforms", [])
                                                         if p.get("name") == "tiktok"), 30),
                                                     search_text=search_text),
            "youtube":   lambda: self.scrape_youtube(niche=niche, regions=region_terms,
                                                      max_results=next((p.get("max_results", 20)
                                                          for p in self.config.get("platforms", [])
                                                          if p.get("name") == "youtube"), 20),
                                                      search_text=search_text),
            "linkedin":  lambda: self.scrape_linkedin(niche=niche, regions=region_terms,
                                                       search_text=search_text,
                                                       max_results=limit),
            "facebook":  lambda: self.scrape_facebook(niche=niche, regions=region_terms,
                                                       search_text=search_text,
                                                       max_results=limit),
            "twitter":   lambda: self.scrape_twitter(niche=niche, regions=region_terms,
                                                      search_text=search_text,
                                                      max_results=limit),
        }

        for platform in platforms:
            fn = dispatcher.get(platform)
            if fn:
                try:
                    raw.extend(fn())
                except Exception as exc:
                    logger.error(f"Platform '{platform}' crashed: {exc}", exc_info=True)
            else:
                logger.warning(f"Unknown platform '{platform}' — skipped.")

        # filter + deduplicate
        filtered = DataValidator.filter_leads(raw, require_email=False)
        unique: List[Dict] = []
        self.dedup.clear()
        for lead in filtered:
            if not self.dedup.is_duplicate(
                email=lead.get("email", ""),
                phone=lead.get("phone", ""),
                company=lead.get("company_name", ""),
                handle=lead.get("social_handle", ""),
            ):
                unique.append(lead)

        unique = self.scorer.score_many(unique)
        minimum_score = int(self.config.get("scoring", {}).get("minimum_score", 0) or 0)
        if minimum_score:
            unique = [lead for lead in unique if int(lead.get("lead_score") or 0) >= minimum_score]
        if limit:
            unique = unique[:limit]
        if persist and self.config.get("deduplication", {}).get("persistent", True):
            unique = self.lead_store.filter_new(unique)

        logger.info(f"Raw: {len(raw)} | After filter: {len(filtered)} | Unique: {len(unique)}")
        self.all_leads = unique
        return unique

    def deep_enrich_leads(self, leads: List[Dict] = None, max_pages: int = 20) -> List[Dict]:
        """Visit reachable source/profile URLs and merge any extra contacts found."""
        targets = leads or self.all_leads
        if not targets:
            return []

        scraper = WebScraper(rate_limit=0.5)
        enriched: List[Dict] = []
        try:
            for lead in targets[:max_pages]:
                merged = dict(lead)
                urls = [lead.get("profile_url"), lead.get("source_url")]
                for url in [item for item in urls if item]:
                    found = scraper.scrape_url(url)
                    for extra in found:
                        for field in ("email", "phone", "company_name"):
                            if not merged.get(field) and extra.get(field):
                                merged[field] = extra[field]
                enriched.append(merged)
        finally:
            scraper.close()
        self.all_leads = self.scorer.score_many(enriched)
        if self.config.get("deduplication", {}).get("persistent", True):
            self.lead_store.save_many(self.all_leads)
        return self.all_leads

    # ── output ────────────────────────────────────────────────────────────────

    def save_leads(self, output_filename: str = None) -> Optional[str]:
        """Save current leads to CSV and return the file path."""
        if not self.all_leads:
            logger.warning("No leads to save.")
            return None

        if not output_filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"leads_{ts}.csv"

        data_dir = self.config.get("output", {}).get("output_dir", "data")
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        filepath = Path(data_dir) / output_filename

        with filepath.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.all_leads)

        logger.info(f"Saved {len(self.all_leads)} leads → {filepath}")
        return str(filepath)

    def save_leads_json(self, output_filename: str = None) -> Optional[str]:
        if not self.all_leads:
            logger.warning("No leads to save.")
            return None
        if not output_filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"leads_{ts}.json"
        data_dir = self.config.get("output", {}).get("output_dir", "data")
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        filepath = Path(data_dir) / output_filename
        filepath.write_text(json.dumps(self.all_leads, indent=2), encoding="utf-8")
        logger.info(f"Saved {len(self.all_leads)} leads → {filepath}")
        return str(filepath)

    # ── scheduler ─────────────────────────────────────────────────────────────

    def start_scheduler(self, frequency_hours: int = 24, start_time: str = "08:00") -> None:
        hour, minute = map(int, start_time.split(":"))

        def _job():
            logger.info("Scheduled scraping job starting …")
            leads = self.run_scraping()
            self.save_leads()
            logger.info(f"Scheduled job done — {len(leads)} leads collected.")

        self.scheduler.schedule_daily(_job, hour=hour, minute=minute)
        self.scheduler.start()

    def stop_scheduler(self) -> None:
        self.scheduler.stop()


# ─────────────────────────────────────────────────────────────────────────────

def _parse_platforms(value: str) -> List[str]:
    planner = SearchPlanner()
    return [planner.normalize_platform(item) for item in (value or "").split(",") if item.strip()]


def _parse_csv(value: str) -> List[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def main():
    parser = argparse.ArgumentParser(description="Free/open-source lead generation engine")
    parser.add_argument("-q", "--query", default="", help="Free-form search intent")
    parser.add_argument("-p", "--platforms", default="", help="Comma-separated platforms or all")
    parser.add_argument("-n", "--niche", default=None, help="Niche name from config.yaml")
    parser.add_argument("-r", "--regions", default="", help="Comma-separated region filters")
    parser.add_argument("-a", "--amount", type=int, default=None, help="Maximum leads")
    parser.add_argument("--format", choices=["csv", "json"], default="csv")
    parser.add_argument("--url", default=None, help="Single page URL to scrape for leads")
    parser.add_argument("--deep", action="store_true", help="Visit reachable result/profile URLs for extra contacts")
    parser.add_argument("--browser", action="store_true", help="Use logged-in browser mode where implemented")
    parser.add_argument("--browser-login", action="store_true", help="Open a local browser login window")
    parser.add_argument("--browser-profile", default="default", help="Local browser profile name")
    parser.add_argument("--headful", action="store_true", help="Show browser during browser-mode searches")
    parser.add_argument("--no-persist", action="store_true", help="Do not use SQLite persistent dedup for this run")
    args = parser.parse_args()

    logger.info("Lead Scraping Engine starting …")
    engine = LeadScrappingEngine("config.yaml")
    if args.browser_login:
        report = engine.browser_login(platform="tiktok", profile=args.browser_profile)
        logger.info("Login window closed for %s profile '%s'", report.platform, report.profile)
        return
    platforms = _parse_platforms(args.platforms)
    if platforms and "all" in [p.lower() for p in platforms]:
        platforms = None
    if args.url:
        leads = engine.scrape_custom_url(
            url=args.url,
            max_leads=args.amount,
            persist=not args.no_persist,
        )
    elif args.browser and (platforms == ["tiktok"] or "tiktok" in (platforms or [])):
        leads = engine.scrape_tiktok_browser(
            search_text=args.query,
            max_leads=args.amount or 30,
            profile=args.browser_profile,
            headful=args.headful,
            persist=not args.no_persist,
        )
    else:
        leads = engine.run_scraping(
            platforms=platforms or None,
            niche=args.niche,
            regions=_parse_csv(args.regions),
            max_leads=args.amount,
            search_text=args.query,
            persist=not args.no_persist,
        )
    if args.deep and leads:
        leads = engine.deep_enrich_leads(leads)
    if leads:
        fp = engine.save_leads_json() if args.format == "json" else engine.save_leads()
        logger.info(f"✓ {len(leads)} leads saved to {fp}")
    else:
        logger.warning("No leads extracted. Try a more specific search query or broader region.")
    logger.info("Done.")


if __name__ == "__main__":
    main()
