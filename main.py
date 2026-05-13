"""
main.py — Lead scraping orchestrator.

Run once:      python main.py
Scheduled:     set scheduler.enabled = true in config.yaml
Telegram bot:  python bot.py
"""

from __future__ import annotations

import csv
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
from utils.validators import DataValidator, DeduplicateManager
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
        "region", "source_url", "source_platform", "post_link", "extracted_at",
    ]

    def __init__(self, config_file: str = "config.yaml"):
        self.config = self._load_config(config_file)
        self.dedup = DeduplicateManager()
        self.scheduler = ScrapingScheduler()
        self.all_leads: List[Dict] = []

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
                   max_urls: int = 10) -> List[Dict]:
        logger.info("── Web scraping ──")
        scraper = WebScraper(rate_limit=1.0)
        try:
            keywords = self._keywords_for_niche(niche)
            return scraper.scrape_search_queries(
                keywords, self._region_terms(regions), max_results=max_urls
            )
        finally:
            scraper.close()

    def scrape_instagram(self, niche: str = None, regions: List[str] = None,
                          max_posts: int = 30) -> List[Dict]:
        logger.info("── Instagram scraping ──")
        platform_cfg = next(
            (p for p in self.config.get("platforms", []) if p.get("name") == "instagram"), {}
        )
        hashtags = list(platform_cfg.get("hashtags", []))
        if niche:
            hashtags.extend(self._keywords_for_niche(niche))
        hashtags = list(dict.fromkeys(t.strip().lstrip("#") for t in hashtags if t))
        scraper = InstagramScraper(config=self.config)
        return scraper.search_hashtags(hashtags, regions=self._region_terms(regions),
                                       max_posts=max_posts)

    def scrape_tiktok(self, niche: str = None, regions: List[str] = None,
                      max_videos: int = 30) -> List[Dict]:
        logger.info("── TikTok scraping ──")
        platform_cfg = next(
            (p for p in self.config.get("platforms", []) if p.get("name") == "tiktok"), {}
        )
        hashtags = list(platform_cfg.get("hashtags", []))
        keywords = self._keywords_for_niche(niche)
        scraper = TikTokScraper(config=self.config)
        return scraper.search_hashtags(hashtags, keywords=keywords,
                                       regions=self._region_terms(regions),
                                       max_videos=max_videos)

    def scrape_youtube(self, niche: str = None, regions: List[str] = None,
                       max_results: int = 20) -> List[Dict]:
        logger.info("── YouTube scraping ──")
        platform_cfg = next(
            (p for p in self.config.get("platforms", []) if p.get("name") == "youtube"), {}
        )
        keywords = platform_cfg.get("search_keywords", [])
        if niche:
            keywords = self._keywords_for_niche(niche) + keywords
        scraper = YouTubeScraper(config=self.config)
        return scraper.search(keywords, regions=self._region_terms(regions),
                               max_results=max_results)

    def scrape_linkedin(self, niche: str = None) -> List[Dict]:
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
        posts = scraper.search_posts(all_keywords or ["web developer"], days=days)
        for post in posts:
            leads.extend(scraper.extract_from_post(post.get("text", ""), post.get("url", "")))
        # search_posts already returns lead dicts when using RapidAPI path
        leads.extend(posts)
        return leads

    def scrape_facebook(self) -> List[Dict]:
        logger.info("── Facebook scraping ──")
        scraper = FacebookScraper(config=self.config)
        days = self.config.get("time_filters", {}).get("max_age_days", 7)
        leads: List[Dict] = []
        for niche in self.config.get("niches", []):
            keywords = niche.get("keywords", [])
            for post in scraper.search_groups(keywords, days):
                leads.extend(scraper.extract_from_post(post.get("text", ""), post.get("url", "")))
            for post in scraper.search_pages(keywords, days):
                leads.extend(scraper.extract_from_post(post.get("text", ""), post.get("url", "")))
        return leads

    def scrape_twitter(self, niche: str = None) -> List[Dict]:
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
        # TwitterScraper.search_tweets now returns lead dicts directly via RapidAPI
        return scraper.search_tweets(keywords or [], days=days)

    # ── main run ──────────────────────────────────────────────────────────────

    def run_scraping(
        self,
        platforms: List[str] = None,
        niche: str = None,
        regions: List[str] = None,
        max_leads: int = None,
    ) -> List[Dict]:
        """
        Run all enabled platform scrapers and return deduplicated leads.

        Args:
            platforms:  platforms to scrape; defaults to config bot.default_platforms
            niche:      niche name matching config.niches[].name
            regions:    list of region strings to filter searches
            max_leads:  cap on returned leads

        Returns:
            List of lead dicts
        """
        platforms = [p.strip().lower() for p in (platforms or self._default_platforms()) if p]
        limit = max_leads or self.config.get("bot", {}).get("default_amount", 50)
        output_cfg = self.config.get("output", {})
        search_limit = output_cfg.get("search_limit", 15)
        region_terms = self._region_terms(regions)

        logger.info(f"Starting scrape | platforms={platforms} | niche={niche} | regions={region_terms}")

        raw: List[Dict] = []

        dispatcher = {
            "web":       lambda: self.scrape_web(niche=niche, regions=region_terms, max_urls=search_limit),
            "instagram": lambda: self.scrape_instagram(niche=niche, regions=region_terms,
                                                        max_posts=next((p.get("max_posts", 30)
                                                            for p in self.config.get("platforms", [])
                                                            if p.get("name") == "instagram"), 30)),
            "tiktok":    lambda: self.scrape_tiktok(niche=niche, regions=region_terms,
                                                     max_videos=next((p.get("max_videos", 30)
                                                         for p in self.config.get("platforms", [])
                                                         if p.get("name") == "tiktok"), 30)),
            "youtube":   lambda: self.scrape_youtube(niche=niche, regions=region_terms,
                                                      max_results=next((p.get("max_results", 20)
                                                          for p in self.config.get("platforms", [])
                                                          if p.get("name") == "youtube"), 20)),
            "linkedin":  lambda: self.scrape_linkedin(niche=niche),
            "facebook":  lambda: self.scrape_facebook(),
            "twitter":   lambda: self.scrape_twitter(niche=niche),
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
            ):
                unique.append(lead)

        if limit:
            unique = unique[:limit]

        logger.info(f"Raw: {len(raw)} | After filter: {len(filtered)} | Unique: {len(unique)}")
        self.all_leads = unique
        return unique

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

def main():
    logger.info("Lead Scraping Engine starting …")
    engine = LeadScrappingEngine("config.yaml")
    leads = engine.run_scraping()
    if leads:
        fp = engine.save_leads()
        logger.info(f"✓ {len(leads)} leads saved to {fp}")
    else:
        logger.warning("⚠  No leads extracted. Check your RAPIDAPI_KEY and platform config.")
    logger.info("Done.")


if __name__ == "__main__":
    main()
