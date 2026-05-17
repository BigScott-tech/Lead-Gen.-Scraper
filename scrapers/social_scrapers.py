"""Free/open social discovery scrapers.

The project intentionally avoids paid/social APIs. Where direct scraping is
fragile or blocked, platform classes use public DuckDuckGo HTML search with
site filters and extract lead signals from titles, snippets, URLs, and any
plain HTML that is available.
"""

from __future__ import annotations

import logging
import re
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
    post_link: str = "",
    profile_url: str = "",
    bio_link: str = "",
    title: str = "",
    snippet: str = "",
    search_query: str = "",
    context: str = "",
    lead_type: str = "",
    confidence: int = 0,
) -> Dict:
    profile_url = profile_url or _profile_url_from_handle(handle, platform)
    return LeadNormalizer.normalize_lead({
        "email": email,
        "phone": phone,
        "company_name": company,
        "social_handle": handle,
        "region": region,
        "source_url": url,
        "source_platform": platform,
        "post_link": post_link or url,
        "profile_url": profile_url,
        "bio_link": bio_link,
        "title": title,
        "snippet": snippet,
        "context": context,
        "search_query": search_query,
        "lead_type": lead_type,
        "confidence": confidence,
        "extracted_at": datetime.now().isoformat(),
    })


def _handle_from_url(url: str, platform: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    if platform == "youtube" and parts[0] in {"watch", "shorts"}:
        return ""
    if platform == "linkedin":
        if parts[0] in {"in", "company"} and len(parts) > 1:
            return parts[1]
        if parts[0] in {"posts", "feed", "jobs"}:
            return ""
    if platform == "facebook":
        if parts[0] == "groups" and len(parts) > 1:
            return parts[1]
        if parts[0] in {"posts", "watch", "events"}:
            return ""
    if parts[0] in {"p", "reel", "status", "watch", "shorts"}:
        return ""
    return parts[0].lstrip("@")


def _is_platform_url(url: str, platform: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    allowed_hosts = {
        "twitter": {"x.com", "twitter.com"},
        "linkedin": {"linkedin.com"},
        "facebook": {"facebook.com", "m.facebook.com"},
        "instagram": {"instagram.com"},
        "tiktok": {"tiktok.com"},
        "youtube": {"youtube.com", "youtu.be"},
    }
    if host not in allowed_hosts.get(platform, {host}):
        return False
    excluded_prefixes = {
        "twitter": ("/home", "/explore", "/settings", "/privacy", "/tos", "/i/", "/intent", "/hashtag"),
        "linkedin": (
            "/help", "/legal", "/learning", "/login", "/signup", "/uas/",
            "/jobs/search", "/jobs/collections", "/pulse/topics",
        ),
        "facebook": ("/help", "/privacy", "/policies", "/login"),
        "instagram": ("/accounts", "/about", "/developer", "/legal"),
        "tiktok": ("/about", "/legal", "/login", "/privacy"),
        "youtube": ("/about", "/howyoutubeworks", "/intl/"),
    }
    return not path.startswith(excluded_prefixes.get(platform, ()))


def _has_contact_phone_context(text: str) -> bool:
    text_lower = text.lower()
    return any(term in text_lower for term in [
        "call", "phone", "tel", "text me", "whatsapp", "wa.me", "contact",
        "sms", "mobile",
    ])


def _profile_url_from_handle(handle: str, platform: str) -> str:
    handle = (handle or "").strip()
    if not handle:
        return ""
    clean = handle.lstrip("@")
    if platform == "twitter":
        return f"https://x.com/{clean}"
    if platform == "instagram":
        return f"https://instagram.com/{clean}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{clean}"
    if platform == "youtube":
        return f"https://www.youtube.com/@{clean}" if handle.startswith("@") else ""
    if platform == "linkedin":
        return f"https://www.linkedin.com/in/{clean}"
    if platform == "facebook":
        return f"https://www.facebook.com/{clean}"
    return ""


def _display_handle(handle: str, platform: str) -> str:
    handle = (handle or "").strip()
    if not handle:
        return ""
    clean = handle.lstrip("@")
    if platform in {"twitter", "instagram", "tiktok"}:
        return f"@{clean}"
    if platform == "youtube" and handle.startswith("@"):
        return f"@{clean}"
    return clean


def _profile_url_from_url(url: str, platform: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    base = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    first = parts[0].lower()

    if platform == "twitter" and len(parts) == 1:
        return f"https://x.com/{parts[0].lstrip('@')}"
    if platform == "twitter" and len(parts) > 1 and parts[1].lower() == "status":
        return f"https://x.com/{parts[0].lstrip('@')}"
    if platform == "instagram" and first not in {"p", "reel", "tv", "explore"}:
        return f"https://instagram.com/{parts[0].lstrip('@')}"
    if platform == "tiktok" and parts[0].startswith("@"):
        return f"https://www.tiktok.com/{parts[0]}"
    if platform == "youtube" and parts[0].startswith("@"):
        return f"https://www.youtube.com/{parts[0]}"
    if platform == "linkedin" and first in {"in", "company"} and len(parts) > 1:
        return f"https://www.linkedin.com/{parts[0]}/{parts[1]}"
    if platform == "facebook":
        if first == "groups" and len(parts) > 1:
            return f"https://www.facebook.com/groups/{parts[1]}"
        if first not in {"posts", "watch", "events", "permalink.php"}:
            return f"https://www.facebook.com/{parts[0]}"
    return ""


def _post_link_from_url(url: str, platform: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return url
    path = parsed.path.lower()
    if platform == "twitter" and "/status/" in path:
        return url
    if platform == "instagram" and parts[0].lower() in {"p", "reel", "tv"}:
        return url
    if platform == "tiktok" and "/video/" in path:
        return url
    if platform == "youtube" and (parts[0].lower() in {"watch", "shorts"} or parsed.netloc.endswith("youtu.be")):
        return url
    if platform == "linkedin" and ("/posts/" in path or "/feed/update/" in path or "/jobs/view/" in path):
        return url
    if platform == "facebook" and any(marker in path for marker in ["/posts/", "/videos/", "/watch/", "permalink.php"]):
        return url
    return ""


def _handle_from_text(text: str, platform: str) -> str:
    text = text or ""
    at_match = re.search(r"@([A-Za-z0-9._-]{2,50})", text)
    if at_match:
        return at_match.group(1)

    if platform == "instagram":
        match = re.search(r"\((?:@)?([A-Za-z0-9._]{2,30})\)\s*[•|-]?\s*Instagram", text, re.IGNORECASE)
        if match:
            return match.group(1)
    if platform == "twitter":
        match = re.search(r"\((?:@)?([A-Za-z0-9_]{2,30})\)\s*/\s*X", text, re.IGNORECASE)
        if match:
            return match.group(1)
    if platform == "youtube":
        match = re.search(r"(?:youtube\.com/)?@([A-Za-z0-9._-]{2,50})", text, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _social_link_from_text(text: str, platform: str) -> str:
    links = LeadExtractor.extract_links_from_text(text or "")
    allowed_hosts = {
        "twitter": ("x.com", "twitter.com"),
        "instagram": ("instagram.com",),
        "facebook": ("facebook.com", "m.facebook.com"),
        "linkedin": ("linkedin.com",),
        "tiktok": ("tiktok.com",),
        "youtube": ("youtube.com", "youtu.be"),
    }
    for link in links:
        host = urlparse(link).netloc.lower().removeprefix("www.")
        if host in allowed_hosts.get(platform, ()):
            return link
    return ""


def _external_link_from_text(text: str) -> str:
    social_hosts = (
        "x.com", "twitter.com", "instagram.com", "facebook.com", "m.facebook.com",
        "linkedin.com", "tiktok.com", "youtube.com", "youtu.be",
    )
    for link in LeadExtractor.extract_links_from_text(text or ""):
        host = urlparse(link).netloc.lower().removeprefix("www.")
        if host and host not in social_hosts:
            return link
    return ""


BUYER_INTENT_TERMS = (
    "need", "needed", "looking for", "seeking", "recommend", "recommendation",
    "anyone know", "who can", "can someone", "help with", "quote", "estimate",
    "asap", "urgent", "hire", "build my", "redesign",
)
JOB_POSTING_TERMS = (
    "hiring", "job", "jobs", "apply", "position", "opening", "recruiting",
    "full time", "part time", "remote", "candidate", "role",
)
LOW_INTENT_TERMS = (
    "open to work", "looking for my next", "my resume", "portfolio", "tutorial",
    "course", "free download", "how to become", "developer looking for",
)
LOCAL_SERVICE_TERMS = (
    "hvac", "contractor", "repair", "installation", "furnace", "air conditioning",
    "plumber", "roofing", "services", "service area",
)


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _classify_lead_type(platform: str, url: str, text: str, plan: SearchPlan) -> str:
    parsed = urlparse(url)
    path = parsed.path.lower()
    lowered = text.lower()

    if _has_any(lowered, LOW_INTENT_TERMS):
        return "low_intent_social_result"
    if platform == "facebook" and "/groups/" in path:
        return "community"
    if platform == "linkedin" and "/jobs/view/" in path:
        return "job_listing"
    if platform == "instagram" and (plan.intent == "local_service" or _has_any(lowered, LOCAL_SERVICE_TERMS)):
        return "local_business_profile"
    if _has_any(lowered, BUYER_INTENT_TERMS):
        return "buyer_intent_post"
    if _has_any(lowered, JOB_POSTING_TERMS):
        return "job_or_hiring_post"
    if _post_link_from_url(url, platform):
        return "social_post"
    return "social_profile"


def _confidence_for_lead(*, lead_type: str, email: str, phone: str, handle: str,
                         post_link: str, profile_url: str) -> int:
    score = 20
    if lead_type in {"buyer_intent_post", "job_listing", "local_business_profile"}:
        score += 30
    elif lead_type in {"community", "job_or_hiring_post"}:
        score += 20
    if email:
        score += 20
    if phone:
        score += 15
    if handle:
        score += 10
    if profile_url:
        score += 10
    if post_link:
        score += 10
    if lead_type == "low_intent_social_result":
        score -= 30
    return max(0, min(100, score))


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
        configured_max_queries = self.config.get("free_search", {}).get("max_queries_per_platform", 8)
        max_queries = min(configured_max_queries, max(4, max_results))
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
                if not _is_platform_url(url, self.platform):
                    continue
                seen_urls.add(url)
                leads.extend(self._leads_from_search_result(result, plan))
                if len(leads) >= max_results:
                    break
            if len(leads) >= max_results:
                break

        logger.info("%s public search leads: %s", self.platform.title(), len(leads))
        return leads

    def _leads_from_search_result(self, result: Dict, plan: SearchPlan) -> List[Dict]:
        url = result.get("url", "")
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        query = result.get("query", "")
        text = " ".join([title, snippet])
        text_social_url = _social_link_from_text(text, self.platform)
        handle = _handle_from_url(url, self.platform) or _handle_from_text(text, self.platform)
        handle = _display_handle(handle, self.platform)
        profile_url = (
            _profile_url_from_url(url, self.platform)
            or _profile_url_from_handle(handle, self.platform)
            or _profile_url_from_url(text_social_url, self.platform)
        )
        post_link = _post_link_from_url(url, self.platform) or _post_link_from_url(text_social_url, self.platform)
        bio_link = _external_link_from_text(text)
        lead_type = _classify_lead_type(self.platform, url, text, plan)
        region = ", ".join(plan.regions)
        emails = [
            email for email in sorted(set(self.extractor.extract_emails(text)))
            if DataValidator.is_valid_email(email)
        ]
        phones: List[str] = []
        if _has_contact_phone_context(text):
            phones = [
                phone for phone in sorted(set(self.extractor.extract_phones(text)))
                if DataValidator.is_valid_phone(phone)
            ]
        companies = [
            company for company in sorted(set(self.extractor.extract_company_names(text)))
            if DataValidator.is_valid_company_name(company)
        ]

        base = {
            "handle": handle,
            "region": region,
            "url": url,
            "platform": self.platform,
            "post_link": post_link or url,
            "profile_url": profile_url,
            "bio_link": bio_link,
            "title": title,
            "snippet": snippet,
            "search_query": query,
            "context": text,
            "lead_type": lead_type,
        }
        primary_email = emails[0] if emails else ""
        primary_phone = phones[0] if phones else ""
        primary_company = companies[0] if companies else ""
        confidence = _confidence_for_lead(
            lead_type=lead_type,
            email=primary_email,
            phone=primary_phone,
            handle=handle,
            post_link=post_link,
            profile_url=profile_url,
        )

        leads = [_make_lead(
            email=primary_email,
            phone=primary_phone,
            company=primary_company,
            confidence=confidence,
            **base,
        )]

        for email in emails[1:]:
            leads.append(_make_lead(
                email=email,
                company=primary_company,
                confidence=_confidence_for_lead(
                    lead_type=lead_type, email=email, phone="", handle=handle,
                    post_link=post_link, profile_url=profile_url,
                ),
                **base,
            ))
        for phone in phones[1:]:
            leads.append(_make_lead(
                phone=phone,
                company=primary_company,
                confidence=_confidence_for_lead(
                    lead_type=lead_type, email="", phone=phone, handle=handle,
                    post_link=post_link, profile_url=profile_url,
                ),
                **base,
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
                      days: int = 7, raw_query: str = "", max_results: int = 30) -> List[Dict]:
        all_terms = list(keywords or []) + [f"#{h.lstrip('#')}" for h in (hashtags or [])]
        return self.search_public_posts(all_terms, days=days, max_results=max_results, raw_query=raw_query)

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

        extractor = LeadExtractor()
        leads: List[Dict] = []
        seen_keys: set[str] = set()

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
                    profile_url = f"https://instagram.com/{owner}" if owner else ""
                    bio = ""
                    bio_link = ""
                    try:
                        profile = getattr(post, "owner_profile", None)
                        bio = getattr(profile, "biography", "") or ""
                        bio_link = getattr(profile, "external_url", "") or ""
                    except Exception:
                        pass

                    combined = f"{caption} {owner} {bio} {bio_link}"
                    if regions and not any(r.lower() in combined.lower() for r in regions):
                        continue

                    emails = [
                        email for email in sorted(set(extractor.extract_emails(combined)))
                        if DataValidator.is_valid_email(email)
                    ]
                    phones = [
                        phone for phone in sorted(set(extractor.extract_phones(combined)))
                        if _has_contact_phone_context(combined) and DataValidator.is_valid_phone(phone)
                    ]
                    lead_type = (
                        "local_business_profile"
                        if _has_any(combined, LOCAL_SERVICE_TERMS)
                        else "social_post"
                    )
                    handle = _display_handle(owner, "instagram")
                    email = emails[0] if emails else ""
                    phone = phones[0] if phones else ""
                    key = email or phone or profile_url or post_url
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    leads.append(_make_lead(
                        email=email,
                        phone=phone,
                        handle=handle,
                        region=region_str,
                        url=post_url,
                        platform="instagram",
                        post_link=post_url,
                        profile_url=profile_url,
                        bio_link=bio_link,
                        title=handle or owner,
                        snippet=caption[:280],
                        context=combined,
                        lead_type=lead_type,
                        confidence=_confidence_for_lead(
                            lead_type=lead_type,
                            email=email,
                            phone=phone,
                            handle=handle,
                            post_link=post_url,
                            profile_url=profile_url,
                        ),
                    ))
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
