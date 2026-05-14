"""Free/open social discovery scrapers.

The project intentionally avoids paid/social APIs. Where direct scraping is
fragile or blocked, platform classes use public DuckDuckGo HTML search with
site filters and extract lead signals from titles, snippets, URLs, and any
plain HTML that is available.
"""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import urlparse
from typing import Dict, List, Optional

from scrapers.web_scraper import WebScraper
from utils.lead_extractor import LeadExtractor, LeadNormalizer
from utils.search_planner import SearchPlanner, SearchPlan
from utils.validators import DataValidator

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_lead(
    *,
    email: str = "",
    phone: str = "",
    company: str = "",
    handle: str = "",
    region: str = "",
    url: str = "",
    platform: str = "",
    title: str = "",
    snippet: str = "",
    search_query: str = "",
    context: str = "",
) -> Dict:
    profile_url = f"https://instagram.com/{handle}" if platform == "instagram" and handle else ""
    return LeadNormalizer.normalize_lead({
        "email": email,
        "phone": phone,
        "company_name": company,
        "social_handle": handle,
        "region": region,
        "source_url": url,
        "source_platform": platform,
        "post_link": url,
        "profile_url": profile_url,
        "title": title,
        "snippet": snippet,
        "context": context,
        "search_query": search_query,
        "extracted_at": datetime.now().isoformat(),
    })


def _handle_from_url(url: str, platform: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    if platform == "youtube" and parts[0] in {"watch", "shorts"}:
        return ""
    if platform == "linkedin" and parts[0] in {"posts", "feed"}:
        return ""
    if platform == "facebook" and parts[0] in {"groups", "posts"}:
        return ""
    if parts[0] in {"p", "reel", "status", "watch", "shorts"}:
        return ""
    return parts[0].lstrip("@")


class SearchBackedSocialScraper:
    """Shared public-search implementation for API-hostile social platforms."""

    platform = "social"

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.planner = SearchPlanner(self.config)
        self.web = WebScraper(rate_limit=self.config.get("free_search", {}).get("rate_limit", 0.5))
        self.extractor = LeadExtractor()

    def search_public_posts(
        self,
        keywords: List[str],
        *,
        regions: List[str] = None,
        days: int = 7,
        max_results: int = 20,
        raw_query: str = "",
    ) -> List[Dict]:
        plan = self.planner.plan(query=raw_query, niche_keywords=keywords, regions=regions)
        per_query = max(2, min(10, max_results))
        max_queries = self.config.get("free_search", {}).get("max_queries_per_platform", 8)
        queries = self.planner.queries_for_platform(
            self.platform,
            plan,
            max_queries=max_queries,
        )
        leads: List[Dict] = []
        seen_urls = set()

        for query in queries:
            for result in self.web.search_documents(query, max_results=per_query):
                url = result.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                leads.extend(self._leads_from_search_result(result, plan))
                if len(seen_urls) >= max_results:
                    break
            if len(seen_urls) >= max_results:
                break

        logger.info("%s public search leads: %s", self.platform.title(), len(leads))
        return leads

    def _leads_from_search_result(self, result: Dict, plan: SearchPlan) -> List[Dict]:
        url = result.get("url", "")
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        query = result.get("query", "")
        text = " ".join([
            title,
            snippet,
            query,
        ])
        handle = _handle_from_url(url, self.platform)
        region = ", ".join(plan.regions)
        leads: List[Dict] = []

        for email in set(self.extractor.extract_emails(text)):
            if DataValidator.is_valid_email(email):
                leads.append(_make_lead(
                    email=email, handle=handle, region=region,
                    url=url, platform=self.platform, title=title,
                    snippet=snippet, search_query=query, context=text,
                ))
        for phone in set(self.extractor.extract_phones(text)):
            if DataValidator.is_valid_phone(phone):
                leads.append(_make_lead(
                    phone=phone, handle=handle, region=region,
                    url=url, platform=self.platform, title=title,
                    snippet=snippet, search_query=query, context=text,
                ))
        for company in set(self.extractor.extract_company_names(text)):
            if DataValidator.is_valid_company_name(company):
                leads.append(_make_lead(
                    company=company, handle=handle, region=region,
                    url=url, platform=self.platform, title=title,
                    snippet=snippet, search_query=query, context=text,
                ))

        if not leads and (handle or url):
            leads.append(_make_lead(
                handle=handle,
                region=region,
                url=url,
                platform=self.platform,
                title=title,
                snippet=snippet,
                search_query=query,
                context=text,
            ))

        return leads


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn
# ─────────────────────────────────────────────────────────────────────────────

class LinkedInScraper(SearchBackedSocialScraper):
    """LinkedIn lead discovery through public search result pages."""

    platform = "linkedin"

    def search_posts(self, keywords: List[str], days: int = 7) -> List[Dict]:
        return self.search_public_posts(keywords, days=days, max_results=20)

    def extract_from_post(self, post_text: str, post_url: str) -> List[Dict]:
        """Keep compatibility with main.py which still calls this."""
        extractor = LeadExtractor()
        leads = []
        for email in set(extractor.extract_emails(post_text)):
            if DataValidator.is_valid_email(email):
                leads.append(_make_lead(email=email, url=post_url, platform="linkedin"))
        return leads


# ─────────────────────────────────────────────────────────────────────────────
# Facebook
# ─────────────────────────────────────────────────────────────────────────────

class FacebookScraper(SearchBackedSocialScraper):
    """Facebook public post/group discovery through search result pages."""

    platform = "facebook"

    def search_groups(self, keywords: List[str], days: int = 7) -> List[Dict]:
        return self.search_public_posts(keywords, days=days, max_results=20)

    def search_pages(self, keywords: List[str], days: int = 7) -> List[Dict]:
        return self.search_public_posts(keywords, days=days, max_results=20)

    def extract_from_post(self, post_text: str, post_url: str) -> List[Dict]:
        extractor = LeadExtractor()
        leads = []
        for email in set(extractor.extract_emails(post_text)):
            if DataValidator.is_valid_email(email):
                leads.append(_make_lead(email=email, url=post_url, platform="facebook"))
        return leads


# ─────────────────────────────────────────────────────────────────────────────
# Twitter / X
# ─────────────────────────────────────────────────────────────────────────────

class TwitterScraper(SearchBackedSocialScraper):
    """Twitter/X lead discovery through public search result pages."""

    platform = "twitter"

    def search_tweets(self, keywords: List[str], hashtags: List[str] = None,
                      days: int = 7, raw_query: str = "") -> List[Dict]:
        all_terms = list(keywords or []) + [f"#{h.lstrip('#')}" for h in (hashtags or [])]
        return self.search_public_posts(all_terms, days=days, max_results=30, raw_query=raw_query)

    def extract_from_tweet(self, tweet_text: str, tweet_url: str) -> List[Dict]:
        extractor = LeadExtractor()
        leads = []
        for email in set(extractor.extract_emails(tweet_text)):
            if DataValidator.is_valid_email(email):
                leads.append(_make_lead(email=email, url=tweet_url, platform="twitter"))
        return leads


# ─────────────────────────────────────────────────────────────────────────────
# Instagram
# ─────────────────────────────────────────────────────────────────────────────

class InstagramScraper:
    """
    Instagram scraper.
    Priority: instaloader, then public search result discovery.
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._loader = self._init_instaloader()
        self._search = SearchBackedSocialScraper(self.config)
        self._search.platform = "instagram"

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
            logger.warning("instaloader not installed; Instagram will use public search fallback.")
            return None

    # ── public interface ──────────────────────────────────────────────────────
    def search_hashtags(self, hashtags: List[str], regions: List[str] = None,
                        max_posts: int = 30, raw_query: str = "") -> List[Dict]:
        if self._loader and not raw_query:
            leads = self._search_via_instaloader(hashtags, regions, max_posts)
            if leads:
                return leads
        return self._search.search_public_posts(
            hashtags,
            regions=regions,
            max_results=max_posts,
            raw_query=raw_query,
        )

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

class TikTokScraper(SearchBackedSocialScraper):
    """TikTok lead discovery through public search result pages."""

    platform = "tiktok"

    def search_hashtags(self, hashtags: List[str], keywords: List[str] = None,
                        regions: List[str] = None, max_videos: int = 30,
                        raw_query: str = "") -> List[Dict]:
        terms = list(hashtags or []) + list(keywords or [])
        return self.search_public_posts(
            terms,
            regions=regions,
            max_results=max_videos,
            raw_query=raw_query,
        )


# ─────────────────────────────────────────────────────────────────────────────
# YouTube  ← NEW
# ─────────────────────────────────────────────────────────────────────────────

class YouTubeScraper(SearchBackedSocialScraper):
    """YouTube lead discovery through public search result pages."""

    platform = "youtube"

    def search(self, keywords: List[str], regions: List[str] = None,
               max_results: int = 20, raw_query: str = "") -> List[Dict]:
        return self.search_public_posts(
            keywords,
            regions=regions,
            max_results=max_results,
            raw_query=raw_query,
        )
