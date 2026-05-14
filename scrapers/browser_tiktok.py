"""Playwright-backed TikTok scraper with a persistent local browser session."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from urllib.parse import quote_plus, urlparse

from utils.lead_extractor import LeadExtractor, LeadNormalizer
from utils.validators import DataValidator

logger = logging.getLogger(__name__)


@dataclass
class BrowserRunReport:
    platform: str
    mode: str
    profile: str
    requested_limit: int
    effective_limit: int
    collected: int
    visited_profiles: int
    next_options: List[str]


class TikTokBrowserScraper:
    """Use a persistent Playwright browser context for logged-in TikTok runs."""

    LOGIN_URL = "https://www.tiktok.com/login"
    SEARCH_URL = "https://www.tiktok.com/search?q={query}"

    def __init__(self, config: dict | None = None, profile: str = "default", headless: bool = True):
        self.config = config or {}
        browser_cfg = self.config.get("browser", {})
        profile_root = Path(browser_cfg.get("profile_root", "profiles/browser"))
        self.profile = profile or browser_cfg.get("default_profile", "default")
        self.user_data_dir = profile_root / self.profile / "tiktok"
        self.headless = headless
        self.extractor = LeadExtractor()

    def open_login_window(self, hold_seconds: int = 180) -> BrowserRunReport:
        """Open TikTok login in a persistent profile and keep it open briefly."""
        with self._playwright() as p:
            context = self._launch_context(p, headless=False)
            page = context.new_page()
            page.goto(self.LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(max(15, hold_seconds) * 1000)
            context.close()
        return BrowserRunReport(
            platform="tiktok",
            mode="login",
            profile=self.profile,
            requested_limit=0,
            effective_limit=0,
            collected=0,
            visited_profiles=0,
            next_options=[
                "Run browser search after logging in.",
                "Use another profile name to switch account.",
            ],
        )

    def search(self, query: str, limit: int = 30) -> tuple[List[Dict], BrowserRunReport]:
        effective_limit = self._safe_limit(limit)
        leads: List[Dict] = []
        visited_profiles = 0

        with self._playwright() as p:
            context = self._launch_context(p, headless=self.headless)
            page = context.new_page()
            search_url = self.SEARCH_URL.format(query=quote_plus(query))
            page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            self._human_scroll(page, rounds=4)

            video_urls = self._collect_video_urls(page, max_urls=effective_limit * 3)
            profile_urls = self._profile_urls_from_video_urls(video_urls)

            for profile_url in profile_urls:
                if len(leads) >= effective_limit:
                    break
                profile_leads = self._scrape_profile(context, profile_url, query)
                visited_profiles += 1
                leads.extend(profile_leads or [self._lead_from_profile_url(profile_url, query)])

            context.close()

        leads = self._dedupe(leads)[:effective_limit]
        report = BrowserRunReport(
            platform="tiktok",
            mode="browser",
            profile=self.profile,
            requested_limit=limit,
            effective_limit=effective_limit,
            collected=len(leads),
            visited_profiles=visited_profiles,
            next_options=[
                "Continue with browser mode for another small batch.",
                "Switch account by using another browser profile.",
                "Switch back to default public search mode.",
            ],
        )
        return leads, report

    def _playwright(self):
        try:
            from playwright.sync_api import sync_playwright
            return sync_playwright()
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright` and "
                "`python -m playwright install chromium`."
            ) from exc

    def _launch_context(self, p, headless: bool):
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        return p.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=headless,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )

    @staticmethod
    def _safe_limit(limit: int) -> int:
        return max(20, min(50, int(limit or 30)))

    @staticmethod
    def _human_scroll(page, rounds: int = 4) -> None:
        for _ in range(rounds):
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(1400)

    @staticmethod
    def _collect_video_urls(page, max_urls: int) -> List[str]:
        hrefs = page.locator("a[href*='/@'][href*='/video/']").evaluate_all(
            "(els) => els.map((el) => el.href)"
        )
        unique: List[str] = []
        seen = set()
        for href in hrefs:
            clean = href.split("?")[0]
            if clean not in seen:
                seen.add(clean)
                unique.append(clean)
            if len(unique) >= max_urls:
                break
        return unique

    @staticmethod
    def _profile_urls_from_video_urls(video_urls: List[str]) -> List[str]:
        profiles: List[str] = []
        seen = set()
        for url in video_urls:
            match = re.search(r"tiktok\.com/@([^/]+)/video/", url)
            if not match:
                continue
            handle = match.group(1)
            profile_url = f"https://www.tiktok.com/@{handle}"
            if profile_url not in seen:
                seen.add(profile_url)
                profiles.append(profile_url)
        return profiles

    def _scrape_profile(self, context, profile_url: str, query: str) -> List[Dict]:
        page = context.new_page()
        try:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1800)
            title = page.title()
            body_text = page.locator("body").inner_text(timeout=10000)
            profile_links = page.locator("a[href]").evaluate_all(
                "(els) => els.map((el) => el.href).slice(0, 80)"
            )
            text = " ".join([title, body_text, " ".join(profile_links)])
            return self._leads_from_text(text, profile_url, query, title)
        except Exception as exc:
            logger.warning("TikTok profile scrape failed for %s: %s", profile_url, exc)
            return []
        finally:
            page.close()

    def _leads_from_text(self, text: str, profile_url: str, query: str, title: str = "") -> List[Dict]:
        handle = self._handle_from_profile_url(profile_url)
        leads: List[Dict] = []
        emails = set(self.extractor.extract_emails(text))
        phones = set(self.extractor.extract_phones(text))
        companies = set(self.extractor.extract_company_names(text))

        for email in emails:
            if DataValidator.is_valid_email(email):
                leads.append(self._make_lead(email=email, handle=handle, profile_url=profile_url,
                                             query=query, title=title, context=text))
        for phone in phones:
            if DataValidator.is_valid_phone(phone):
                leads.append(self._make_lead(phone=phone, handle=handle, profile_url=profile_url,
                                             query=query, title=title, context=text))
        for company in companies:
            if DataValidator.is_valid_company_name(company):
                leads.append(self._make_lead(company=company, handle=handle, profile_url=profile_url,
                                             query=query, title=title, context=text))
        if not leads:
            leads.append(self._make_lead(handle=handle, profile_url=profile_url,
                                         query=query, title=title, context=text))
        return leads

    def _lead_from_profile_url(self, profile_url: str, query: str) -> Dict:
        return self._make_lead(
            handle=self._handle_from_profile_url(profile_url),
            profile_url=profile_url,
            query=query,
        )

    @staticmethod
    def _handle_from_profile_url(profile_url: str) -> str:
        path = urlparse(profile_url).path.strip("/")
        return path.lstrip("@")

    @staticmethod
    def _make_lead(
        *,
        email: str = "",
        phone: str = "",
        company: str = "",
        handle: str = "",
        profile_url: str = "",
        query: str = "",
        title: str = "",
        context: str = "",
    ) -> Dict:
        return LeadNormalizer.normalize_lead({
            "email": email,
            "phone": phone,
            "company_name": company,
            "social_handle": handle,
            "region": "",
            "source_url": profile_url,
            "source_platform": "tiktok",
            "post_link": profile_url,
            "profile_url": profile_url,
            "title": title,
            "snippet": context[:280],
            "context": context,
            "search_query": query,
            "extracted_at": datetime.now().isoformat(),
        })

    @staticmethod
    def _dedupe(leads: List[Dict]) -> List[Dict]:
        seen = set()
        unique: List[Dict] = []
        for lead in leads:
            key = (
                lead.get("email")
                or lead.get("phone")
                or lead.get("social_handle")
                or lead.get("source_url")
            )
            if key and key not in seen:
                seen.add(key)
                unique.append(lead)
        return unique
