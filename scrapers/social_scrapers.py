"""
Social media scrapers — each class tries RapidAPI first and falls back to a
free/open method when no API key is available.

  LinkedInScraper   — RapidAPI → no free fallback (LinkedIn blocks all scraping)
  FacebookScraper   — placeholder (FB requires Selenium / Graph API)
  TwitterScraper    — RapidAPI → no free fallback
  InstagramScraper  — RapidAPI → instaloader fallback
  TikTokScraper     — RapidAPI → no free fallback  (NEW)
  YouTubeScraper    — RapidAPI → no free fallback  (NEW)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rapidapi_cfg(config: dict) -> dict:
    """Pull RapidAPI section from main config."""
    return config.get("rapidapi", {})

def _api_key(config: dict) -> str:
    cfg = _rapidapi_cfg(config)
    env_var = cfg.get("key_env", "RAPIDAPI_KEY")
    return os.getenv(env_var, "")

def _host(config: dict, platform: str) -> str:
    return _rapidapi_cfg(config).get("hosts", {}).get(platform, "")


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn
# ─────────────────────────────────────────────────────────────────────────────

class LinkedInScraper:
    """LinkedIn lead scraper — RapidAPI only (LinkedIn blocks all public scraping)."""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._key = _api_key(self.config)
        self._host_name = _host(self.config, "linkedin")

    def _get_api(self):
        from scrapers.rapidapi_scrapers import RapidLinkedInScraper
        return RapidLinkedInScraper(
            host=self._host_name or "linkedin-data-api.p.rapidapi.com",
            api_key=self._key,
        )

    def search_posts(self, keywords: List[str], days: int = 7) -> List[Dict]:
        if not self._key:
            logger.warning("LinkedIn scraper needs RAPIDAPI_KEY — skipped.")
            return []
        try:
            return self._get_api().search_posts(keywords, max_results=20)
        except Exception as exc:
            logger.error(f"LinkedIn RapidAPI error: {exc}")
            return []

    def extract_from_post(self, post_text: str, post_url: str) -> List[Dict]:
        """Keep compatibility with main.py which still calls this."""
        from utils.lead_extractor import LeadExtractor, LeadNormalizer
        from utils.validators import DataValidator
        extractor = LeadExtractor()
        leads = []
        for email in set(extractor.extract_emails(post_text)):
            if DataValidator.is_valid_email(email):
                leads.append(LeadNormalizer.normalize_lead({
                    "email": email, "phone": "", "company_name": "",
                    "source_url": post_url, "source_platform": "linkedin",
                    "post_link": post_url, "extracted_at": datetime.now().isoformat(),
                }))
        return leads


# ─────────────────────────────────────────────────────────────────────────────
# Facebook
# ─────────────────────────────────────────────────────────────────────────────

class FacebookScraper:
    """
    Facebook scraper — placeholder.

    Facebook's aggressive anti-scraping means reliable access requires either
    the official Graph API (with a verified app) or a paid proxy service.
    The extract_from_post() method still works if you feed it text manually.
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        logger.warning(
            "FacebookScraper: no reliable free scraping method is available. "
            "Enable the platform but expect 0 results unless you add a Graph API integration."
        )

    def search_groups(self, keywords: List[str], days: int = 7) -> List[Dict]:
        logger.info("Facebook group search not available without Graph API.")
        return []

    def search_pages(self, keywords: List[str], days: int = 7) -> List[Dict]:
        logger.info("Facebook page search not available without Graph API.")
        return []

    def extract_from_post(self, post_text: str, post_url: str) -> List[Dict]:
        from utils.lead_extractor import LeadExtractor, LeadNormalizer
        from utils.validators import DataValidator
        extractor = LeadExtractor()
        leads = []
        for email in set(extractor.extract_emails(post_text)):
            if DataValidator.is_valid_email(email):
                leads.append(LeadNormalizer.normalize_lead({
                    "email": email, "phone": "", "company_name": "",
                    "source_url": post_url, "source_platform": "facebook",
                    "post_link": post_url, "extracted_at": datetime.now().isoformat(),
                }))
        return leads


# ─────────────────────────────────────────────────────────────────────────────
# Twitter / X
# ─────────────────────────────────────────────────────────────────────────────

class TwitterScraper:
    """Twitter/X scraper — RapidAPI (twitter241) with graceful fallback."""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._key = _api_key(self.config)
        self._host_name = _host(self.config, "twitter")

    def _get_api(self):
        from scrapers.rapidapi_scrapers import RapidTwitterScraper
        return RapidTwitterScraper(
            host=self._host_name or "twitter241.p.rapidapi.com",
            api_key=self._key,
        )

    def search_tweets(self, keywords: List[str], hashtags: List[str] = None,
                      days: int = 7) -> List[Dict]:
        if not self._key:
            logger.warning("Twitter scraper needs RAPIDAPI_KEY — skipped.")
            return []

        results: List[Dict] = []
        all_terms = list(keywords or []) + [f"#{h.lstrip('#')}" for h in (hashtags or [])]
        api = self._get_api()

        for term in all_terms[:5]:          # avoid burning too many API calls
            try:
                results.extend(api.search_tweets(term, max_tweets=20, days=days))
            except Exception as exc:
                logger.error(f"Twitter search '{term}' error: {exc}")
        return results

    def extract_from_tweet(self, tweet_text: str, tweet_url: str) -> List[Dict]:
        from utils.lead_extractor import LeadExtractor, LeadNormalizer
        from utils.validators import DataValidator
        extractor = LeadExtractor()
        leads = []
        for email in set(extractor.extract_emails(tweet_text)):
            if DataValidator.is_valid_email(email):
                leads.append(LeadNormalizer.normalize_lead({
                    "email": email, "phone": "", "company_name": "",
                    "source_url": tweet_url, "source_platform": "twitter",
                    "post_link": tweet_url, "extracted_at": datetime.now().isoformat(),
                }))
        return leads


# ─────────────────────────────────────────────────────────────────────────────
# Instagram
# ─────────────────────────────────────────────────────────────────────────────

class InstagramScraper:
    """
    Instagram scraper.
    Priority: RapidAPI → instaloader (free fallback).
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._key = _api_key(self.config)
        self._host_name = _host(self.config, "instagram")
        self._loader = None
        if not self._key:
            self._loader = self._init_instaloader()

    # ── instaloader fallback ──────────────────────────────────────────────────
    def _init_instaloader(self):
        try:
            import instaloader
            loader = instaloader.Instaloader(
                download_pictures=False, download_videos=False,
                save_metadata=False, compress_json=False, quiet=True,
            )
            return loader
        except ImportError:
            logger.warning("instaloader not installed; Instagram will be skipped without RAPIDAPI_KEY.")
            return None

    # ── RapidAPI path ─────────────────────────────────────────────────────────
    def _get_api(self):
        from scrapers.rapidapi_scrapers import RapidInstagramScraper
        return RapidInstagramScraper(
            host=self._host_name or "instagram-scraper-api2.p.rapidapi.com",
            api_key=self._key,
        )

    # ── public interface ──────────────────────────────────────────────────────
    def search_hashtags(self, hashtags: List[str], regions: List[str] = None,
                        max_posts: int = 30) -> List[Dict]:
        if self._key:
            return self._search_via_api(hashtags, regions, max_posts)
        if self._loader:
            return self._search_via_instaloader(hashtags, regions, max_posts)
        logger.warning("Instagram: no API key and instaloader unavailable.")
        return []

    def _search_via_api(self, hashtags, regions, max_posts) -> List[Dict]:
        api = self._get_api()
        leads: List[Dict] = []
        for tag in hashtags:
            try:
                leads.extend(api.search_hashtag(tag, max_posts=max_posts, regions=regions))
            except Exception as exc:
                logger.error(f"Instagram API error for #{tag}: {exc}")
        return leads

    def _search_via_instaloader(self, hashtags, regions, max_posts) -> List[Dict]:
        """Instaloader fallback — slower but free."""
        try:
            from instaloader import Hashtag
        except ImportError:
            return []

        from utils.lead_extractor import LeadExtractor, LeadNormalizer
        from utils.validators import DataValidator
        extractor = LeadExtractor()
        leads: List[Dict] = []

        for hashtag in hashtags:
            try:
                tag = Hashtag.from_name(self._loader.context, hashtag.lstrip("#"))
                for idx, post in enumerate(tag.get_posts()):
                    if idx >= max_posts:
                        break
                    caption = post.caption or ""
                    owner = getattr(post, "owner_username", "")
                    post_url = f"https://www.instagram.com/p/{post.shortcode}/"
                    region_str = ", ".join(regions) if regions else ""

                    combined = f"{caption} {owner}"
                    if regions and not any(r.lower() in combined.lower() for r in regions):
                        continue

                    for email in set(extractor.extract_emails(caption)):
                        if DataValidator.is_valid_email(email):
                            leads.append(LeadNormalizer.normalize_lead({
                                "email": email, "social_handle": owner,
                                "region": region_str, "source_url": post_url,
                                "source_platform": "instagram", "post_link": post_url,
                                "extracted_at": datetime.now().isoformat(),
                            }))
                    if not leads and owner:
                        leads.append(LeadNormalizer.normalize_lead({
                            "email": "", "phone": "", "company_name": "",
                            "social_handle": owner, "region": region_str,
                            "source_url": post_url, "source_platform": "instagram",
                            "post_link": post_url, "extracted_at": datetime.now().isoformat(),
                        }))
            except Exception as exc:
                logger.error(f"Instaloader error for #{hashtag}: {exc}")

        return leads

    def extract_from_post(self, post_text: str, post_url: str,
                           social_handle: str = "", region: str = "") -> List[Dict]:
        from utils.lead_extractor import LeadExtractor, LeadNormalizer
        from utils.validators import DataValidator
        extractor = LeadExtractor()
        leads = []
        for email in set(extractor.extract_emails(post_text)):
            if DataValidator.is_valid_email(email):
                leads.append(LeadNormalizer.normalize_lead({
                    "email": email, "social_handle": social_handle, "region": region,
                    "source_url": post_url, "source_platform": "instagram",
                    "post_link": post_url, "extracted_at": datetime.now().isoformat(),
                }))
        return leads


# ─────────────────────────────────────────────────────────────────────────────
# TikTok  ← NEW
# ─────────────────────────────────────────────────────────────────────────────

class TikTokScraper:
    """TikTok lead scraper via RapidAPI."""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._key = _api_key(self.config)
        self._host_name = _host(self.config, "tiktok")

    def _get_api(self):
        from scrapers.rapidapi_scrapers import RapidTikTokScraper
        return RapidTikTokScraper(
            host=self._host_name or "tiktok-api23.p.rapidapi.com",
            api_key=self._key,
        )

    def search_hashtags(self, hashtags: List[str], keywords: List[str] = None,
                        regions: List[str] = None, max_videos: int = 30) -> List[Dict]:
        if not self._key:
            logger.warning("TikTok scraper needs RAPIDAPI_KEY — skipped.")
            return []

        api = self._get_api()
        leads: List[Dict] = []

        for tag in (hashtags or []):
            try:
                leads.extend(api.search_hashtag(tag, max_videos=max_videos, regions=regions))
            except Exception as exc:
                logger.error(f"TikTok hashtag #{tag} error: {exc}")

        for kw in (keywords or [])[:3]:    # limit extra calls
            try:
                leads.extend(api.search_keyword(kw, max_results=20, regions=regions))
            except Exception as exc:
                logger.error(f"TikTok keyword '{kw}' error: {exc}")

        logger.info(f"TikTok total leads: {len(leads)}")
        return leads


# ─────────────────────────────────────────────────────────────────────────────
# YouTube  ← NEW
# ─────────────────────────────────────────────────────────────────────────────

class YouTubeScraper:
    """YouTube lead scraper via RapidAPI."""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._key = _api_key(self.config)
        self._host_name = _host(self.config, "youtube")

    def _get_api(self):
        from scrapers.rapidapi_scrapers import RapidYouTubeScraper
        return RapidYouTubeScraper(
            host=self._host_name or "youtube-v31.p.rapidapi.com",
            api_key=self._key,
        )

    def search(self, keywords: List[str], regions: List[str] = None,
               max_results: int = 20) -> List[Dict]:
        if not self._key:
            logger.warning("YouTube scraper needs RAPIDAPI_KEY — skipped.")
            return []

        api = self._get_api()
        leads: List[Dict] = []

        for kw in (keywords or [])[:4]:
            try:
                leads.extend(api.search_videos(kw, max_results=max_results, regions=regions))
            except Exception as exc:
                logger.error(f"YouTube search '{kw}' error: {exc}")

        logger.info(f"YouTube total leads: {len(leads)}")
        return leads
