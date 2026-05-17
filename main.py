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
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote, quote_plus

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
from scrapers.browser_x import XBrowserScraper
from utils.validators import DataValidator, DeduplicateManager
from utils.search_planner import SearchPlanner
from utils.lead_scoring import LeadScorer
from utils.lead_store import LeadStore
from utils.browser_launcher import BrowserLauncher
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
        "bio_link", "title", "snippet", "search_query", "lead_type",
        "confidence", "lead_score", "lead_reason",
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

    def _sanitize_for_filename(self, value: str) -> str:
        sanitized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
        sanitized = re.sub(r"_+", "_", sanitized).strip("_")
        return sanitized[:40]

    def build_output_filename(
        self,
        fmt: str = "csv",
        query: str = "",
        platforms: Optional[List[str]] = None,
        regions: Optional[List[str]] = None,
        niche: Optional[str] = None,
    ) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        parts = ["leads"]

        if query:
            query_segment = self._sanitize_for_filename(query)
            if query_segment:
                parts.append(query_segment)

        if platforms:
            platform_segment = "_".join(
                self._sanitize_for_filename(platform) for platform in platforms if platform
            )
            if platform_segment:
                parts.append(platform_segment)

        if regions:
            region_segment = "_".join(
                self._sanitize_for_filename(region) for region in regions if region
            )
            if region_segment:
                parts.append(region_segment)

        if niche:
            niche_segment = self._sanitize_for_filename(niche)
            if niche_segment:
                parts.append(niche_segment)

        parts.append(ts)
        filename = "_".join(p for p in parts if p)
        return f"{filename}.{fmt}"

    def _browser_fallback_query(
        self,
        search_text: str = "",
        niche: Optional[str] = None,
        regions: Optional[List[str]] = None,
    ) -> str:
        parts: List[str] = []
        if search_text:
            parts.append(search_text)
        if niche:
            parts.append(niche)
        if regions:
            parts.extend([region for region in regions if region])
        return " ".join(parts).strip()

    def build_browser_fallback_urls(
        self,
        query: str = "",
        platforms: Optional[List[str]] = None,
        niche: Optional[str] = None,
        regions: Optional[List[str]] = None,
    ) -> List[str]:
        query_text = self._browser_fallback_query(query, niche=niche, regions=regions)
        urls: List[str] = []
        platforms = platforms or self._default_platforms()

        for platform in platforms:
            platform_key = self.search_planner.normalize_platform(platform)
            query_value = quote_plus(query_text) if query_text else ""

            if platform_key in {"x", "twitter"}:
                url = f"https://twitter.com/search?q={query_value}&f=live"
            elif platform_key == "linkedin":
                url = f"https://www.linkedin.com/search/results/all/?keywords={query_value}"
            elif platform_key == "facebook":
                url = f"https://www.facebook.com/search/top?q={query_value}"
            elif platform_key == "instagram":
                if query_text.startswith("#"):
                    tag = quote_plus(query_text.lstrip("#"))
                    url = f"https://www.instagram.com/explore/tags/{tag}/"
                elif query_text:
                    url = f"https://www.instagram.com/explore/search/keyword/?q={query_value}"
                else:
                    url = "https://www.instagram.com/"
            elif platform_key == "tiktok":
                url = f"https://www.tiktok.com/search?q={query_value}"
            elif platform_key == "youtube":
                url = f"https://www.youtube.com/results?search_query={query_value}"
            elif platform_key == "web":
                url = f"https://duckduckgo.com/?q={query_value or 'site:instagram.com'}"
            else:
                url = f"https://duckduckgo.com/?q={query_value or 'site:instagram.com'}"

            urls.append(url)

        return urls

    def custom_search_definitions(
        self,
        searches: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None,
    ) -> List[Dict]:
        """Return configured custom browser-search links by name."""
        configured = self.config.get("custom_searches", [])
        if isinstance(configured, dict):
            configured = configured.get("links") or configured.get("searches") or []

        selected = {self._slug(item) for item in (searches or []) if item}
        include_all = bool(selected.intersection({"all", "*", "enabled"}))
        selected_names = selected - {"all", "*", "enabled"}
        platform_filter = {
            self.search_planner.normalize_platform(platform)
            for platform in (platforms or [])
            if platform
        }

        definitions: List[Dict] = []
        for index, item in enumerate(configured):
            if isinstance(item, str):
                item = {"name": f"custom_{index + 1}", "url": item, "enabled": True}
            if not isinstance(item, dict):
                continue

            name = str(item.get("name") or item.get("label") or f"custom_{index + 1}")
            name_key = self._slug(name)
            enabled = item.get("enabled", True) is not False
            explicitly_selected = name_key in selected_names

            if selected_names and not explicitly_selected:
                continue
            if not selected_names and not include_all and not enabled:
                continue
            if include_all and not enabled:
                continue

            item_platforms = {
                self.search_planner.normalize_platform(platform)
                for platform in item.get("platforms", [])
                if platform
            }
            if (
                platform_filter
                and item_platforms
                and not explicitly_selected
                and not platform_filter.intersection(item_platforms)
            ):
                continue

            definition = dict(item)
            definition["name"] = name
            definitions.append(definition)

        return definitions

    def build_custom_search_urls(
        self,
        query: str = "",
        searches: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None,
        niche: Optional[str] = None,
        regions: Optional[List[str]] = None,
        extra_urls: Optional[List[str]] = None,
    ) -> List[str]:
        """Build URLs from custom config templates and one-off CLI templates."""
        query_text = self._browser_fallback_query(query, niche=niche, regions=regions)
        region_text = ", ".join(self._region_terms(regions))
        platform_text = ",".join(platforms or [])
        context = {
            "query": query_text,
            "raw_query": query or "",
            "query_plus": quote_plus(query_text),
            "query_encoded": quote(query_text),
            "raw_query_plus": quote_plus(query or ""),
            "raw_query_encoded": quote(query or ""),
            "region": region_text,
            "region_plus": quote_plus(region_text),
            "region_encoded": quote(region_text),
            "niche": niche or "",
            "niche_plus": quote_plus(niche or ""),
            "niche_encoded": quote(niche or ""),
            "platform": platform_text,
            "platforms": platform_text,
        }

        urls: List[str] = []
        if searches:
            for definition in self.custom_search_definitions(searches, platforms=platforms):
                template = definition.get("url") or definition.get("template")
                if template:
                    urls.append(self._format_search_url_template(str(template), context))

        for template in extra_urls or []:
            urls.append(self._format_search_url_template(str(template), context))

        return self._unique_urls(urls)

    def build_manual_search_urls(
        self,
        query: str = "",
        platforms: Optional[List[str]] = None,
        niche: Optional[str] = None,
        regions: Optional[List[str]] = None,
        custom_searches: Optional[List[str]] = None,
        extra_urls: Optional[List[str]] = None,
    ) -> List[str]:
        urls = self.build_browser_fallback_urls(
            query=query,
            platforms=platforms,
            niche=niche,
            regions=regions,
        )
        urls.extend(self.build_custom_search_urls(
            query=query,
            searches=custom_searches,
            platforms=platforms,
            niche=niche,
            regions=regions,
            extra_urls=extra_urls,
        ))
        return self._unique_urls(urls)

    def open_search_urls_in_browser(
        self,
        urls: List[str],
        firefox_profile: str | None = None,
        browser_app: str = "firefox",
        new_window: bool = False,
    ) -> List[str]:
        result = BrowserLauncher.open_urls(
            urls,
            app=browser_app,
            profile=firefox_profile,
            new_window=new_window,
        )
        return result.urls

    @staticmethod
    def _format_search_url_template(template: str, context: Dict[str, str]) -> str:
        class _SafeDict(dict):
            def __missing__(self, key):
                return ""

        return template.format_map(_SafeDict(context))

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")

    @staticmethod
    def _unique_urls(urls: List[str]) -> List[str]:
        seen = set()
        unique: List[str] = []
        for url in urls:
            item = str(url or "").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return unique

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

    def _finalize_leads(
        self,
        leads: List[Dict],
        limit: int = None,
        persist: bool = True,
        minimum_score: int | None = None,
    ) -> List[Dict]:
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
        minimum_score = (
            int(minimum_score)
            if minimum_score is not None
            else int(self.config.get("scoring", {}).get("minimum_score", 0) or 0)
        )
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

        finalized = self._finalize_leads(
            leads,
            limit=max_leads or self.config.get("bot", {}).get("default_amount", 50),
            persist=persist,
            minimum_score=0,
        )
        self.all_leads = finalized
        return finalized

    def scrape_custom_urls(
        self,
        urls: List[str],
        max_leads: int = None,
        persist: bool = True,
    ) -> List[Dict]:
        logger.info("── Custom URL batch scraping ──")
        scraper = WebScraper(rate_limit=1.0)
        raw: List[Dict] = []
        try:
            for url in self._unique_urls(urls):
                leads = scraper.scrape_url(url)
                for lead in leads:
                    if not lead.get("source_url"):
                        lead["source_url"] = url
                    if not lead.get("source_platform"):
                        lead["source_platform"] = "web"
                raw.extend(leads)
        finally:
            scraper.close()

        finalized = self._finalize_leads(
            raw,
            limit=max_leads or self.config.get("bot", {}).get("default_amount", 50),
            persist=persist,
            minimum_score=0,
        )
        self.all_leads = finalized
        return finalized

    def browser_login(self, platform: str = "tiktok", profile: str = "default",
                      hold_seconds: int = 180) -> BrowserRunReport:
        platform = self.search_planner.normalize_platform(platform)
        if platform == "tiktok":
            scraper = TikTokBrowserScraper(config=self.config, profile=profile, headless=False)
        elif platform == "twitter":
            scraper = XBrowserScraper(config=self.config, profile=profile, headless=False)
        else:
            raise ValueError("Browser login is currently implemented for TikTok and X/Twitter.")
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

    def scrape_x_browser(self, search_text: str, max_leads: int = 100,
                         profile: str = "default", headful: bool = False,
                         persist: bool = True) -> List[Dict]:
        scraper = XBrowserScraper(
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
            output_filename = self.build_output_filename("csv")

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
            output_filename = self.build_output_filename("json")
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


def _read_url_file(path: str) -> List[str]:
    urls: List[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if item and not item.startswith("#"):
            urls.append(item)
    return urls


def _browser_platform_from_platforms(platforms: Optional[List[str]], default: str = "tiktok") -> str:
    normalized = [SearchPlanner().normalize_platform(platform) for platform in (platforms or [])]
    if "twitter" in normalized:
        return "twitter"
    if "tiktok" in normalized:
        return "tiktok"
    return default


def main():
    parser = argparse.ArgumentParser(description="Free/open-source lead generation engine")
    parser.add_argument("-q", "--query", default="", help="Free-form search intent")
    parser.add_argument("-p", "--platforms", default="", help="Comma-separated platforms or all")
    parser.add_argument("-n", "--niche", default=None, help="Niche name from config.yaml")
    parser.add_argument("-r", "--regions", default="", help="Comma-separated region filters")
    parser.add_argument("-a", "--amount", type=int, default=None, help="Maximum leads")
    parser.add_argument("--format", choices=["csv", "json"], default="csv")
    parser.add_argument("--url", default=None, help="Single page URL to scrape for leads")
    parser.add_argument("--url-file", default=None, help="Line-delimited file of URLs to scrape")
    parser.add_argument("--deep", action="store_true", help="Visit reachable result/profile URLs for extra contacts")
    parser.add_argument("--browser", action="store_true", help="Use logged-in browser mode where implemented")
    parser.add_argument("--browser-login", action="store_true", help="Open a local browser login window")
    parser.add_argument("--browser-profile", default="default", help="Local browser profile name for Playwright mode")
    parser.add_argument("--browser-fallback", action="store_true", help="Open manual search tabs in a local browser")
    parser.add_argument("--browser-app", default="firefox", help="Browser app for manual tabs: firefox, brave, chrome, chromium, system")
    parser.add_argument("--browser-new-window", action="store_true", help="Open manual tabs in a new browser window")
    parser.add_argument("--firefox-profile", default=None, help="Firefox profile for manual browser fallback")
    parser.add_argument("--custom-searches", default="", help="Comma-separated custom search names from config.yaml, or all")
    parser.add_argument("--custom-link", action="append", default=[], help="Ad-hoc URL/template to open; supports {query_plus}")
    parser.add_argument("--list-custom-searches", action="store_true", help="List configured custom search links")
    parser.add_argument("--print-search-links", action="store_true", help="Print manual browser search URLs")
    parser.add_argument("--links-only", action="store_true", help="Handle manual search links, then skip scraping")
    parser.add_argument("--headful", action="store_true", help="Show browser during browser-mode searches")
    parser.add_argument("--no-persist", action="store_true", help="Do not use SQLite persistent dedup for this run")
    args = parser.parse_args()

    logger.info("Lead Scraping Engine starting …")
    engine = LeadScrappingEngine("config.yaml")
    if args.list_custom_searches:
        for definition in engine.custom_search_definitions(["all"]):
            print(f"{definition.get('name')}: {definition.get('url') or definition.get('template')}")
        return

    platforms = _parse_platforms(args.platforms)
    if platforms and "all" in [p.lower() for p in platforms]:
        platforms = None

    if args.browser_login:
        login_platform = _browser_platform_from_platforms(platforms)
        report = engine.browser_login(platform=login_platform, profile=args.browser_profile)
        logger.info("Login window closed for %s profile '%s'", report.platform, report.profile)
        return

    regions = _parse_csv(args.regions)
    custom_searches = _parse_csv(args.custom_searches)
    manual_links_requested = (
        args.browser_fallback
        or args.print_search_links
        or args.links_only
        or bool(custom_searches)
        or bool(args.custom_link)
    )
    if manual_links_requested:
        urls = engine.build_manual_search_urls(
            query=args.query,
            platforms=platforms,
            niche=args.niche,
            regions=regions,
            custom_searches=custom_searches,
            extra_urls=args.custom_link,
        )
        if args.print_search_links or args.links_only or (not args.browser_fallback and (custom_searches or args.custom_link)):
            print("\n".join(urls))
        if args.browser_fallback:
            engine.open_search_urls_in_browser(
                urls,
                firefox_profile=args.firefox_profile,
                browser_app=args.browser_app,
                new_window=args.browser_new_window,
            )
        if args.links_only:
            return

    target_urls = [args.url] if args.url else []
    if args.url_file:
        target_urls.extend(_read_url_file(args.url_file))

    if target_urls:
        leads = engine.scrape_custom_urls(
            urls=target_urls,
            max_leads=args.amount,
            persist=not args.no_persist,
        )
    elif args.browser and "twitter" in (platforms or []):
        leads = engine.scrape_x_browser(
            search_text=args.query,
            max_leads=args.amount or 100,
            profile=args.browser_profile,
            headful=args.headful,
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
            regions=regions,
            max_leads=args.amount,
            search_text=args.query,
            persist=not args.no_persist,
        )
    if args.deep and leads:
        leads = engine.deep_enrich_leads(leads)
    if leads:
        output_filename = engine.build_output_filename(
            args.format,
            query=args.query,
            platforms=platforms,
            regions=regions,
            niche=args.niche,
        )
        fp = engine.save_leads_json(output_filename) if args.format == "json" else engine.save_leads(output_filename)
        if fp:
            logger.info(f"✓ {len(leads)} leads saved to {fp}")
        else:
            logger.warning("Leads were extracted but no output file was written.")
    else:
        logger.warning("No leads extracted. Try a more specific search query or broader region.")
    logger.info("Done.")


if __name__ == "__main__":
    main()
