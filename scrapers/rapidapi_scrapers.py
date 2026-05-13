"""
RapidAPI scrapers — production-grade API-backed scrapers for all social platforms.

Each scraper hits a RapidAPI-hosted endpoint and falls back gracefully if the key
is missing or the call fails.  A single RAPIDAPI_KEY environment variable (set in
.env) unlocks all platforms.

APIs used (verify / swap at rapidapi.com if a host changes):
  Instagram  → instagram-scraper-api2.p.rapidapi.com
  Twitter/X  → twitter241.p.rapidapi.com
  LinkedIn   → linkedin-data-api.p.rapidapi.com
  TikTok     → tiktok-api23.p.rapidapi.com
  YouTube    → youtube-v31.p.rapidapi.com
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from utils.lead_extractor import LeadExtractor, LeadNormalizer
from utils.validators import DataValidator

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Base
# ─────────────────────────────────────────────────────────────────────────────

class RapidAPIBase:
    """Shared HTTP client for all RapidAPI scrapers."""

    def __init__(self, host: str, api_key: Optional[str] = None, timeout: int = 15, retries: int = 2):
        self.host = host
        self.api_key = api_key or os.getenv("RAPIDAPI_KEY", "")
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "x-rapidapi-host": self.host,
            "x-rapidapi-key": self.api_key,
        }

    def _get(self, url: str, params: Dict = None) -> Optional[Any]:
        """GET with retry and error handling."""
        if not self.api_key:
            logger.debug("No RAPIDAPI_KEY — skipping API call")
            return None

        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.get(url, headers=self._headers, params=params, timeout=self.timeout)
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning(f"Rate limited by {self.host}. Waiting {wait}s …")
                    time.sleep(wait)
                    continue
                if resp.status_code == 401:
                    logger.error(f"Invalid RAPIDAPI_KEY for {self.host}.")
                    return None
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as exc:
                logger.warning(f"[{self.host}] attempt {attempt}/{self.retries} failed: {exc}")
                time.sleep(1)
        return None

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat()

    @staticmethod
    def _make_lead(
        *,
        email: str = "",
        phone: str = "",
        company: str = "",
        handle: str = "",
        region: str = "",
        url: str = "",
        platform: str = "",
    ) -> Dict:
        lead = {
            "email": email,
            "phone": phone,
            "company_name": company,
            "social_handle": handle,
            "region": region,
            "source_url": url,
            "source_platform": platform,
            "post_link": url,
            "extracted_at": datetime.now().isoformat(),
        }
        return LeadNormalizer.normalize_lead(lead)

    def _leads_from_text(self, text: str, url: str, platform: str,
                          handle: str = "", region: str = "") -> List[Dict]:
        """Extract all leads from a block of text."""
        extractor = LeadExtractor()
        leads: List[Dict] = []

        for email in set(extractor.extract_emails(text)):
            if DataValidator.is_valid_email(email):
                leads.append(self._make_lead(email=email, handle=handle,
                                              region=region, url=url, platform=platform))

        for phone in set(extractor.extract_phones(text)):
            if DataValidator.is_valid_phone(phone):
                leads.append(self._make_lead(phone=phone, handle=handle,
                                              region=region, url=url, platform=platform))

        for company in set(extractor.extract_company_names(text)):
            if DataValidator.is_valid_company_name(company):
                leads.append(self._make_lead(company=company, handle=handle,
                                              region=region, url=url, platform=platform))

        # If nothing extracted but we have a handle, record profile as lead
        if not leads and handle:
            leads.append(self._make_lead(handle=handle, region=region,
                                          url=url, platform=platform))

        return leads


# ─────────────────────────────────────────────────────────────────────────────
# Instagram
# ─────────────────────────────────────────────────────────────────────────────

class RapidInstagramScraper(RapidAPIBase):
    """Instagram scraper via RapidAPI."""

    BASE = "https://instagram-scraper-api2.p.rapidapi.com/v1"

    def search_hashtag(self, hashtag: str, max_posts: int = 30,
                       regions: List[str] = None) -> List[Dict]:
        """Scrape posts under a hashtag and extract leads."""
        data = self._get(f"{self.BASE}/hashtag", params={"hashtag": hashtag.lstrip("#")})
        if not data:
            return []

        leads: List[Dict] = []
        posts = data.get("data", {}).get("edge_hashtag_to_media", {}).get("edges", [])

        for edge in posts[:max_posts]:
            node = edge.get("node", {})
            caption = (node.get("edge_media_to_caption", {})
                           .get("edges", [{}])[0]
                           .get("node", {})
                           .get("text", ""))
            shortcode = node.get("shortcode", "")
            post_url = f"https://www.instagram.com/p/{shortcode}/" if shortcode else ""
            owner = node.get("owner", {}).get("username", "")

            if regions:
                region_text = (caption + " " + owner).lower()
                if not any(r.lower() in region_text for r in regions):
                    continue

            region_str = ", ".join(regions) if regions else ""
            leads.extend(self._leads_from_text(caption, post_url, "instagram",
                                                handle=owner, region=region_str))

        logger.info(f"Instagram hashtag #{hashtag}: {len(leads)} leads")
        return leads

    def get_user_info(self, username: str) -> Optional[Dict]:
        """Fetch a public profile and return a lead if contact info found."""
        data = self._get(f"{self.BASE}/user/by/username", params={"username": username})
        if not data:
            return None

        user = data.get("data", {})
        bio = user.get("biography", "")
        url = f"https://www.instagram.com/{username}/"

        leads = self._leads_from_text(bio, url, "instagram", handle=username)
        return leads[0] if leads else None


# ─────────────────────────────────────────────────────────────────────────────
# Twitter / X
# ─────────────────────────────────────────────────────────────────────────────

class RapidTwitterScraper(RapidAPIBase):
    """Twitter/X scraper via RapidAPI."""

    BASE = "https://twitter241.p.rapidapi.com"

    def search_tweets(self, query: str, max_tweets: int = 30,
                      days: int = 7) -> List[Dict]:
        """Search recent tweets and extract leads."""
        data = self._get(f"{self.BASE}/search-v2",
                         params={"query": query, "count": str(max_tweets), "type": "Latest"})
        if not data:
            return []

        leads: List[Dict] = []
        timeline = (data.get("result", {})
                       .get("timeline", {})
                       .get("instructions", []))

        for instruction in timeline:
            for entry in instruction.get("entries", []):
                tweet_result = (entry.get("content", {})
                                    .get("itemContent", {})
                                    .get("tweet_results", {})
                                    .get("result", {}))
                legacy = tweet_result.get("legacy", {})
                text = legacy.get("full_text", "")
                user = (tweet_result.get("core", {})
                                    .get("user_results", {})
                                    .get("result", {})
                                    .get("legacy", {}))
                handle = user.get("screen_name", "")
                tweet_id = legacy.get("id_str", "")
                tweet_url = f"https://twitter.com/{handle}/status/{tweet_id}" if handle and tweet_id else ""

                leads.extend(self._leads_from_text(text, tweet_url, "twitter", handle=handle))

        logger.info(f"Twitter search '{query}': {len(leads)} leads")
        return leads


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn
# ─────────────────────────────────────────────────────────────────────────────

class RapidLinkedInScraper(RapidAPIBase):
    """LinkedIn scraper via RapidAPI (public data only)."""

    BASE = "https://linkedin-data-api.p.rapidapi.com"

    def search_posts(self, keywords: List[str], max_results: int = 20) -> List[Dict]:
        """Search LinkedIn posts and extract leads."""
        leads: List[Dict] = []
        query = " ".join(keywords[:3])   # API limits query length

        data = self._get(f"{self.BASE}/search-posts", params={"query": query, "count": str(max_results)})
        if not data:
            return []

        for post in data.get("data", [])[:max_results]:
            text = post.get("text", "")
            author_url = post.get("authorUrl", "")
            post_url = post.get("postUrl", author_url)
            handle = post.get("authorUsername", "")

            leads.extend(self._leads_from_text(text, post_url, "linkedin", handle=handle))

        logger.info(f"LinkedIn search '{query}': {len(leads)} leads")
        return leads

    def get_company_info(self, company_url: str) -> List[Dict]:
        """Scrape a company page for contact leads."""
        data = self._get(f"{self.BASE}/get-company-details", params={"url": company_url})
        if not data:
            return []

        company_data = data.get("data", {})
        description = company_data.get("description", "")
        website = company_data.get("website", "")
        name = company_data.get("name", "")

        leads = self._leads_from_text(description + " " + website, company_url, "linkedin")
        if not leads and name:
            leads.append(self._make_lead(company=name, url=company_url, platform="linkedin"))

        return leads


# ─────────────────────────────────────────────────────────────────────────────
# TikTok  ← NEW PLATFORM
# ─────────────────────────────────────────────────────────────────────────────

class RapidTikTokScraper(RapidAPIBase):
    """TikTok scraper via RapidAPI — extracts leads from bios and captions."""

    BASE = "https://tiktok-api23.p.rapidapi.com/api"

    def search_hashtag(self, hashtag: str, max_videos: int = 30,
                       regions: List[str] = None) -> List[Dict]:
        """Search TikTok by hashtag and extract contact info from bios/captions."""
        data = self._get(f"{self.BASE}/challenge/posts",
                         params={"keyword": hashtag.lstrip("#"), "count": str(max_videos)})
        if not data:
            return []

        leads: List[Dict] = []
        items = data.get("itemList", []) or data.get("data", {}).get("itemList", [])

        for item in items[:max_videos]:
            caption = item.get("desc", "")
            author = item.get("author", {})
            handle = author.get("uniqueId", "")
            bio = author.get("signature", "")
            video_id = item.get("id", "")
            video_url = f"https://www.tiktok.com/@{handle}/video/{video_id}" if handle and video_id else ""

            combined_text = f"{caption} {bio}"
            if regions:
                if not any(r.lower() in combined_text.lower() for r in regions):
                    continue

            region_str = ", ".join(regions) if regions else ""
            leads.extend(self._leads_from_text(combined_text, video_url, "tiktok",
                                                handle=handle, region=region_str))

        logger.info(f"TikTok hashtag #{hashtag}: {len(leads)} leads")
        return leads

    def search_keyword(self, keyword: str, max_results: int = 20,
                       regions: List[str] = None) -> List[Dict]:
        """Full-text keyword search across TikTok."""
        data = self._get(f"{self.BASE}/search",
                         params={"keyword": keyword, "count": str(max_results)})
        if not data:
            return []

        leads: List[Dict] = []
        items = data.get("item_list", []) or data.get("data", {}).get("item_list", [])

        for item in items[:max_results]:
            caption = item.get("desc", "")
            author = item.get("author", {})
            handle = author.get("uniqueId", "")
            bio = author.get("signature", "")
            video_id = item.get("id", "")
            video_url = f"https://www.tiktok.com/@{handle}/video/{video_id}" if handle else ""
            region_str = ", ".join(regions) if regions else ""

            leads.extend(self._leads_from_text(f"{caption} {bio}", video_url, "tiktok",
                                                handle=handle, region=region_str))

        return leads

    def get_user_profile(self, username: str) -> List[Dict]:
        """Fetch TikTok profile and extract bio contact details."""
        data = self._get(f"{self.BASE}/user/info", params={"uniqueId": username})
        if not data:
            return []

        user = data.get("userInfo", {}).get("user", {}) or data.get("data", {}).get("user", {})
        bio = user.get("signature", "")
        profile_url = f"https://www.tiktok.com/@{username}"

        return self._leads_from_text(bio, profile_url, "tiktok", handle=username)


# ─────────────────────────────────────────────────────────────────────────────
# YouTube  ← NEW PLATFORM
# ─────────────────────────────────────────────────────────────────────────────

class RapidYouTubeScraper(RapidAPIBase):
    """YouTube scraper via RapidAPI — extracts leads from channel/video descriptions."""

    BASE = "https://youtube-v31.p.rapidapi.com"

    def search_videos(self, query: str, max_results: int = 20,
                      regions: List[str] = None) -> List[Dict]:
        """
        Search YouTube videos by query and extract contact info from
        titles, descriptions, and channel names.
        """
        params = {
            "q": query,
            "part": "snippet",
            "maxResults": str(max_results),
            "type": "video",
            "order": "date",
        }
        data = self._get(f"{self.BASE}/search", params=params)
        if not data:
            return []

        leads: List[Dict] = []

        for item in data.get("items", [])[:max_results]:
            snippet = item.get("snippet", {})
            title = snippet.get("title", "")
            description = snippet.get("description", "")
            channel = snippet.get("channelTitle", "")
            video_id = item.get("id", {}).get("videoId", "")
            video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
            channel_id = snippet.get("channelId", "")

            text = f"{title} {description} {channel}"
            if regions:
                if not any(r.lower() in text.lower() for r in regions):
                    continue

            region_str = ", ".join(regions) if regions else ""
            extracted = self._leads_from_text(text, video_url, "youtube",
                                               handle=f"@{channel}", region=region_str)

            # If no contact info in snippet, fetch full description
            if not extracted and video_id:
                extracted = self._fetch_video_details(video_id, channel, region_str)

            leads.extend(extracted)

        logger.info(f"YouTube search '{query}': {len(leads)} leads")
        return leads

    def _fetch_video_details(self, video_id: str, channel: str, region: str) -> List[Dict]:
        """Fetch full video description which often contains contact links."""
        data = self._get(f"{self.BASE}/videos",
                         params={"id": video_id, "part": "snippet,statistics"})
        if not data:
            return []

        items = data.get("items", [])
        if not items:
            return []

        snippet = items[0].get("snippet", {})
        description = snippet.get("description", "")
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        return self._leads_from_text(description, video_url, "youtube",
                                      handle=f"@{channel}", region=region)

    def search_channels(self, query: str, max_results: int = 10) -> List[Dict]:
        """Search channels and extract contact info from descriptions."""
        params = {
            "q": query,
            "part": "snippet",
            "maxResults": str(max_results),
            "type": "channel",
        }
        data = self._get(f"{self.BASE}/search", params=params)
        if not data:
            return []

        leads: List[Dict] = []

        for item in data.get("items", [])[:max_results]:
            snippet = item.get("snippet", {})
            description = snippet.get("description", "")
            title = snippet.get("title", "")
            channel_id = item.get("id", {}).get("channelId", "")
            channel_url = f"https://www.youtube.com/channel/{channel_id}" if channel_id else ""

            leads.extend(self._leads_from_text(
                f"{title} {description}", channel_url, "youtube", handle=title
            ))

        return leads
