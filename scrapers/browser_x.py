"""Playwright-backed X/Twitter scraper with a persistent local browser session."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote_plus, urlparse

from scrapers.browser_tiktok import BrowserRunReport
from utils.lead_extractor import LeadExtractor, LeadNormalizer
from utils.search_planner import SearchPlanner
from utils.validators import DataValidator

logger = logging.getLogger(__name__)


class XBrowserScraper:
    """Use a persistent Playwright browser context for logged-in X searches."""

    LOGIN_URL = "https://x.com/i/flow/login"
    SEARCH_URL = "https://x.com/search?q={query}&f=live"
    DATE_TOKEN = r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})"
    SINCE_RE = re.compile(rf"\bsince:?\s*{DATE_TOKEN}\b", flags=re.IGNORECASE)
    UNTIL_RE = re.compile(rf"\buntil:?\s*{DATE_TOKEN}\b", flags=re.IGNORECASE)

    def __init__(self, config: dict | None = None, profile: str = "default", headless: bool = True):
        self.config = config or {}
        browser_cfg = self.config.get("browser", {})
        profile_root = Path(browser_cfg.get("profile_root", "profiles/browser"))
        self.profile = profile or browser_cfg.get("default_profile", "default")
        self.user_data_dir = profile_root / self.profile / "x"
        self.headless = headless
        self.extractor = LeadExtractor()
        self.planner = SearchPlanner(self.config)

    def open_login_window(self, hold_seconds: int = 180) -> BrowserRunReport:
        """Open X login in a persistent profile and keep it open briefly."""
        with self._playwright() as p:
            context = self._launch_context(p, headless=False)
            page = context.new_page()
            page.goto(self.LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(max(15, hold_seconds) * 1000)
            context.close()
        return BrowserRunReport(
            platform="twitter",
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

    def search(self, query: str, limit: int = 100) -> tuple[List[Dict], BrowserRunReport]:
        effective_limit = self._safe_limit(limit)
        search_queries = self.build_search_queries(query)
        since, until = self._date_window(search_queries[0] if search_queries else query)
        max_scroll_rounds = self._max_scroll_rounds(effective_limit)
        leads: List[Dict] = []

        with self._playwright() as p:
            context = self._launch_context(p, headless=self.headless)
            page = context.new_page()
            for search_query in search_queries:
                if len(leads) >= effective_limit:
                    break
                logger.info("X browser search query: %s", search_query)
                if not self._goto_search(page, search_query):
                    continue
                page.wait_for_timeout(2500)
                leads.extend(self._collect_search_leads(
                    page,
                    limit=effective_limit - len(leads),
                    search_query=search_query,
                    since=since,
                    until=until,
                    max_scroll_rounds=max_scroll_rounds,
                ))
                leads = self._dedupe(leads)
            context.close()

        leads = self._dedupe(leads)[:effective_limit]
        report = BrowserRunReport(
            platform="twitter",
            mode="browser",
            profile=self.profile,
            requested_limit=limit,
            effective_limit=effective_limit,
            collected=len(leads),
            visited_profiles=0,
            next_options=[
                "Continue with browser mode for another batch.",
                "Switch account by using another browser profile.",
                "Switch back to default public search mode.",
            ],
        )
        return leads, report

    def _goto_search(self, page, search_query: str) -> bool:
        try:
            page.goto(
                self.SEARCH_URL.format(query=quote_plus(search_query)),
                wait_until="commit",
                timeout=45000,
            )
            return True
        except Exception as exc:
            logger.warning("X search navigation timed out/failed for %s: %s", search_query, exc)
            return False

    def build_search_queries(self, query: str) -> List[str]:
        """Return exact and broadened X-native queries for a lead intent."""
        primary = self.build_search_query(query)
        base, since, until, retweet_filter = self._search_parts(primary)
        variants = [primary]

        lowered = base.lower()
        if any(term in lowered for term in ["website", "web developer", "web designer", "developer"]):
            variants.extend([
                self._compose_query('web developer', since, until, retweet_filter),
                self._compose_query('website developer', since, until, retweet_filter),
                self._compose_query('web designer', since, until, retweet_filter),
                self._compose_query('need website', since, until, retweet_filter),
                self._compose_query('need web developer', since, until, retweet_filter),
                self._compose_query('looking for web developer', since, until, retweet_filter),
                self._compose_query('looking for web designer', since, until, retweet_filter),
                self._compose_query('"web developer" (needed OR need OR hiring OR hire OR "looking for")',
                                    since, until, retweet_filter),
                self._compose_query('"website developer" (needed OR need OR hiring OR hire OR "looking for")',
                                    since, until, retweet_filter),
                self._compose_query('"web designer" (needed OR need OR hiring OR hire OR "looking for")',
                                    since, until, retweet_filter),
                self._compose_query('"need a website" (developer OR designer OR help)',
                                    since, until, retweet_filter),
                self._compose_query('"looking for" ("web developer" OR "web designer" OR "website developer")',
                                    since, until, retweet_filter),
                self._compose_query('website developer needed',
                                    since, until, retweet_filter),
            ])

        return self._unique_strings(variants)

    def build_search_query(self, query: str) -> str:
        """Normalize free-form text into an X-native live-search query."""
        query = " ".join((query or "").split())
        since = self._extract_operator_date(query, self.SINCE_RE)
        until = self._extract_operator_date(query, self.UNTIL_RE)

        if since:
            base = self.SINCE_RE.sub("", query)
            base = self.UNTIL_RE.sub("", base)
            base = " ".join(base.split())
        else:
            plan = self.planner.plan(query=query)
            since = plan.since
            base = plan.terms[0] if plan.terms else query

        parts = [self._quote_if_phrase(base)]
        if since:
            parts.append(f"since:{since.isoformat()}")
            if not until:
                until = date.today() + timedelta(days=1)
        if until:
            parts.append(f"until:{until.isoformat()}")
        if "-filter:retweets" not in query.lower():
            parts.append("-filter:retweets")
        return " ".join(part for part in parts if part)

    @classmethod
    def _search_parts(cls, search_query: str) -> tuple[str, Optional[date], Optional[date], bool]:
        since = cls._extract_operator_date(search_query, cls.SINCE_RE)
        until = cls._extract_operator_date(search_query, cls.UNTIL_RE)
        base = cls.SINCE_RE.sub("", search_query)
        base = cls.UNTIL_RE.sub("", base)
        retweet_filter = "-filter:retweets" in base.lower()
        base = re.sub(r"-filter:retweets", "", base, flags=re.IGNORECASE)
        base = " ".join(base.split())
        return base, since, until, retweet_filter

    @staticmethod
    def _compose_query(
        base: str,
        since: Optional[date],
        until: Optional[date],
        retweet_filter: bool,
    ) -> str:
        parts = [base]
        if since:
            parts.append(f"since:{since.isoformat()}")
        if until:
            parts.append(f"until:{until.isoformat()}")
        if retweet_filter:
            parts.append("-filter:retweets")
        return " ".join(part for part in parts if part)

    def _collect_search_leads(
        self,
        page,
        *,
        limit: int,
        search_query: str,
        since: Optional[date],
        until: Optional[date],
        max_scroll_rounds: int,
    ) -> List[Dict]:
        leads: List[Dict] = []
        seen_posts: set[str] = set()
        stalled_rounds = 0

        for _ in range(max_scroll_rounds):
            docs = self._tweet_docs_from_page(page)
            logger.info("X browser cards visible: %d", len(docs))
            added_this_round = 0
            for doc in docs:
                post_link = doc.get("post_link", "")
                if not post_link or post_link in seen_posts:
                    continue
                seen_posts.add(post_link)
                if not self._is_within_date_window(doc.get("datetime", ""), since, until):
                    continue
                lead = self._lead_from_tweet_doc(doc, search_query)
                if lead:
                    leads.append(lead)
                    added_this_round += 1
                    if len(leads) >= limit:
                        return leads

            stalled_rounds = stalled_rounds + 1 if added_this_round == 0 else 0
            if stalled_rounds >= 8:
                break
            page.mouse.wheel(0, 1100)
            page.wait_for_timeout(1300)

        return leads

    @staticmethod
    def _tweet_docs_from_page(page) -> List[Dict]:
        try:
            return page.locator("article[data-testid='tweet']").evaluate_all(
                """
                (articles) => articles.map((article) => {
                  const statusLinks = [...article.querySelectorAll('a[href*="/status/"]')]
                    .map((a) => a.href.split("?")[0])
                    .filter(Boolean);
                  const time = article.querySelector("time");
                  const text = article.innerText || "";
                  const postLink = statusLinks[0] || "";
                  const handleMatch = postLink.match(/x\\.com\\/([^/]+)\\/status\\//)
                    || postLink.match(/twitter\\.com\\/([^/]+)\\/status\\//);
                  const handle = handleMatch ? handleMatch[1] : "";
                  return {
                    text,
                    post_link: postLink,
                    profile_url: handle ? `https://x.com/${handle}` : "",
                    handle,
                    datetime: time ? (time.getAttribute("datetime") || "") : "",
                  };
                })
                """
            )
        except Exception as exc:
            logger.warning("Failed to collect X tweet cards: %s", exc)
            return []

    def _lead_from_tweet_doc(self, doc: Dict, search_query: str) -> Dict:
        text = " ".join(str(doc.get("text", "")).split())
        post_link = self._normalize_post_link(str(doc.get("post_link", "")).strip())
        handle = self._display_handle(str(doc.get("handle", "")))
        profile_url = str(doc.get("profile_url", "")).strip() or self._profile_url_from_post(post_link)
        if not post_link or not profile_url:
            return {}

        emails = [
            email for email in sorted(set(self.extractor.extract_emails(text)))
            if DataValidator.is_valid_email(email)
        ]
        phones = [
            phone for phone in sorted(set(self.extractor.extract_phones(text)))
            if self._has_contact_phone_context(text) and DataValidator.is_valid_phone(phone)
        ]
        companies = [
            company for company in sorted(set(self.extractor.extract_company_names(text)))
            if DataValidator.is_valid_company_name(company)
        ]
        email = emails[0] if emails else ""
        phone = phones[0] if phones else ""
        company = companies[0] if companies else ""

        return LeadNormalizer.normalize_lead({
            "email": email,
            "phone": phone,
            "company_name": company,
            "social_handle": handle,
            "region": "",
            "source_url": post_link,
            "source_platform": "twitter",
            "post_link": post_link,
            "profile_url": profile_url,
            "title": handle or profile_url,
            "snippet": text[:280],
            "context": text,
            "search_query": search_query,
            "lead_type": "buyer_intent_post",
            "confidence": self._confidence(email=email, phone=phone, handle=handle),
            "extracted_at": datetime.now().isoformat(),
        })

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

    def _safe_limit(self, limit: int) -> int:
        browser_cfg = self.config.get("browser", {})
        min_size = int(browser_cfg.get("min_batch_size", 20) or 20)
        max_size = int(browser_cfg.get("x_max_batch_size", 250) or 250)
        return max(min_size, min(max_size, int(limit or 100)))

    def _max_scroll_rounds(self, limit: int) -> int:
        browser_cfg = self.config.get("browser", {})
        configured = int(browser_cfg.get("x_scroll_rounds", 0) or 0)
        if configured:
            return configured
        return max(24, min(160, (limit // 2) + 24))

    @classmethod
    def _date_window(cls, query: str) -> tuple[Optional[date], Optional[date]]:
        since = cls._extract_operator_date(query, cls.SINCE_RE)
        until = cls._extract_operator_date(query, cls.UNTIL_RE)
        return since, until

    @classmethod
    def _extract_operator_date(cls, text: str, pattern: re.Pattern) -> Optional[date]:
        match = pattern.search(text or "")
        if not match:
            return None
        return cls._parse_date_token(match.group(1))

    @staticmethod
    def _parse_date_token(value: str) -> Optional[date]:
        parts = [int(part) for part in re.split(r"[-/]", value or "") if part]
        if len(parts) != 3:
            return None
        candidates = []
        if len(str(parts[0])) == 4:
            candidates.append((parts[0], parts[1], parts[2]))
        else:
            year = parts[2] + 2000 if parts[2] < 100 else parts[2]
            candidates.extend([(year, parts[1], parts[0]), (year, parts[0], parts[1])])
        for year, month, day in candidates:
            try:
                return date(year, month, day)
            except ValueError:
                continue
        return None

    @classmethod
    def _is_within_date_window(
        cls,
        timestamp: str,
        since: Optional[date],
        until: Optional[date],
    ) -> bool:
        if not timestamp:
            return True
        try:
            posted_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
        except ValueError:
            return True
        if since and posted_at < since:
            return False
        if until and posted_at >= until:
            return False
        return True

    @staticmethod
    def _quote_if_phrase(value: str) -> str:
        value = " ".join((value or "").split())
        if not value or value.startswith('"') or " " not in value:
            return value
        return f'"{value}"'

    @staticmethod
    def _display_handle(handle: str) -> str:
        handle = (handle or "").strip().lstrip("@")
        return f"@{handle}" if handle else ""

    @staticmethod
    def _profile_url_from_post(post_link: str) -> str:
        parsed = urlparse(post_link or "")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[1].lower() == "status":
            return f"https://x.com/{parts[0]}"
        return ""

    @staticmethod
    def _normalize_post_link(post_link: str) -> str:
        match = re.search(r"https?://(?:x|twitter)\.com/([^/]+)/status/(\d+)", post_link or "")
        if not match:
            return post_link
        return f"https://x.com/{match.group(1)}/status/{match.group(2)}"

    @staticmethod
    def _has_contact_phone_context(text: str) -> bool:
        lowered = (text or "").lower()
        return any(term in lowered for term in [
            "call", "phone", "tel", "text me", "whatsapp", "wa.me", "contact",
            "sms", "mobile",
        ])

    @staticmethod
    def _confidence(*, email: str, phone: str, handle: str) -> int:
        score = 70
        if email:
            score += 15
        if phone:
            score += 10
        if handle:
            score += 5
        return min(100, score)

    @staticmethod
    def _dedupe(leads: List[Dict]) -> List[Dict]:
        seen = set()
        unique: List[Dict] = []
        for lead in leads:
            key = lead.get("post_link") or lead.get("profile_url") or lead.get("social_handle")
            if key and key not in seen:
                seen.add(key)
                unique.append(lead)
        return unique

    @staticmethod
    def _unique_strings(values: List[str]) -> List[str]:
        seen = set()
        unique: List[str] = []
        for value in values:
            item = " ".join(str(value or "").split())
            key = item.lower()
            if item and key not in seen:
                seen.add(key)
                unique.append(item)
        return unique
