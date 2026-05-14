"""
Local search planning for free/open lead discovery.

This module deliberately avoids hosted AI and paid APIs. It turns a user's
plain-language intent into search-engine queries that public scrapers can run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional


@dataclass
class SearchPlan:
    raw_query: str = ""
    terms: List[str] = field(default_factory=list)
    regions: List[str] = field(default_factory=list)
    since: Optional[date] = None
    intent: str = "general"


class SearchPlanner:
    """Build practical search queries from config keywords and user text."""

    DEFAULT_INTENT_TERMS = [
        "website developer needed",
        "need a website",
        "looking for web developer",
        "hire web developer",
        "web designer needed",
        "website redesign needed",
        "freelance developer needed",
        "shopify developer needed",
    ]

    PLATFORM_SITE_FILTERS: Dict[str, List[str]] = {
        "twitter": ["x.com", "twitter.com"],
        "x": ["x.com", "twitter.com"],
        "linkedin": ["linkedin.com/posts", "linkedin.com/feed/update", "linkedin.com/in"],
        "facebook": ["facebook.com/groups", "facebook.com/posts"],
        "instagram": ["instagram.com", "instagram.com/p", "instagram.com/reel"],
        "ig": ["instagram.com", "instagram.com/p", "instagram.com/reel"],
        "tiktok": ["tiktok.com"],
        "youtube": ["youtube.com/watch", "youtube.com/shorts"],
    }

    PLATFORM_ALIASES: Dict[str, str] = {
        "x": "twitter",
        "twitter": "twitter",
        "tw": "twitter",
        "ig": "instagram",
        "insta": "instagram",
        "instagram": "instagram",
        "li": "linkedin",
        "linkedin": "linkedin",
        "fb": "facebook",
        "facebook": "facebook",
        "tt": "tiktok",
        "tiktok": "tiktok",
        "yt": "youtube",
        "youtube": "youtube",
        "web": "web",
        "all": "all",
    }

    HVAC_CITY_HINTS: Dict[str, List[str]] = {
        "ontario": ["Toronto", "Ottawa", "Hamilton", "Mississauga", "London"],
        "illinois": ["Chicago", "Aurora", "Naperville", "Joliet", "Springfield"],
        "new york": ["New York", "Buffalo", "Rochester", "Albany", "Syracuse"],
        "ny": ["New York", "Buffalo", "Rochester", "Albany", "Syracuse"],
        "texas": ["Houston", "Dallas", "Austin", "San Antonio", "Fort Worth"],
        "california": ["Los Angeles", "San Diego", "San Jose", "Sacramento", "Fresno"],
        "florida": ["Miami", "Orlando", "Tampa", "Jacksonville", "Tallahassee"],
    }

    _SINCE_RE = re.compile(
        r"\bsince\s+(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b",
        flags=re.IGNORECASE,
    )

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def plan(
        self,
        *,
        query: str = "",
        niche_keywords: Iterable[str] | None = None,
        regions: Iterable[str] | None = None,
    ) -> SearchPlan:
        raw_query = " ".join((query or "").split())
        since = self._extract_since(raw_query)
        cleaned = self._SINCE_RE.sub("", raw_query).strip()

        terms: List[str] = []
        if cleaned:
            terms.append(cleaned)
        terms.extend(niche_keywords or [])
        terms.extend(self.config.get("lead_intent_terms", []))
        if not terms:
            terms.extend(self.DEFAULT_INTENT_TERMS)

        return SearchPlan(
            raw_query=raw_query,
            terms=self._unique(terms),
            regions=self._unique(regions or []),
            since=since,
            intent=self._detect_intent(raw_query, terms),
        )

    def queries_for_platform(
        self,
        platform: str,
        plan: SearchPlan,
        *,
        max_queries: int = 8,
    ) -> List[str]:
        platform = self.normalize_platform(platform or "web")
        base_terms = self.terms_for_platform(platform, plan)
        region_suffixes = plan.regions or [""]
        queries: List[str] = []

        sites = self.PLATFORM_SITE_FILTERS.get(platform, [])
        for term in base_terms:
            for region in region_suffixes:
                base = self._join_parts([self._quote_if_phrase(term), region])
                if plan.since:
                    base = self._add_date_operator(platform, base, plan.since)
                if platform == "twitter":
                    base = f"{base} -filter:retweets"
                if sites:
                    for site in sites:
                        queries.append(f"{base} site:{site}")
                        if len(queries) >= max_queries:
                            return queries
                else:
                    queries.append(base)
                    if len(queries) >= max_queries:
                        return queries
        return queries

    def terms_for_platform(self, platform: str, plan: SearchPlan) -> List[str]:
        platform = self.normalize_platform(platform)
        terms = list(plan.terms or self.DEFAULT_INTENT_TERMS)
        configured = self.config.get("platform_query_terms", {}).get(platform, [])
        terms.extend(configured)

        if platform == "twitter":
            terms.extend(self._twitter_intent_terms(plan.intent))
        elif platform == "instagram":
            terms.extend(self.instagram_terms(plan))
        elif platform == "linkedin":
            terms.extend(self._linkedin_terms(plan.intent))
        elif platform == "youtube":
            terms.extend(self._youtube_terms(plan.intent))

        return self._unique(terms)

    def instagram_terms(self, plan: SearchPlan) -> List[str]:
        terms: List[str] = []
        raw_text = " ".join([plan.raw_query, " ".join(plan.terms)]).lower()
        target = "hvac" if "hvac" in raw_text else self._first_keyword(plan.terms)

        if target:
            terms.append(target)
        for region in plan.regions:
            region_key = region.lower()
            clean_region = re.sub(r"[^a-z0-9]", "", region_key)
            if target and clean_region:
                terms.append(f"#{target}{clean_region}")
                terms.append(f"{target} {region}")
            for city in self.HVAC_CITY_HINTS.get(region_key, []):
                clean_city = re.sub(r"[^a-z0-9]", "", city.lower())
                if target and clean_city:
                    terms.append(f"#{target}{clean_city}")
                    terms.append(f"{target} {city}")

        if target:
            terms.extend([f"{target} contractor", f"{target} services", f"{target} business"])
        return self._unique(terms)

    def normalize_platform(self, platform: str) -> str:
        return self.PLATFORM_ALIASES.get((platform or "").strip().lower(), platform.strip().lower())

    def _extract_since(self, query: str) -> Optional[date]:
        match = self._SINCE_RE.search(query or "")
        if not match:
            return None

        first, second, year = [int(part) for part in match.groups()]
        if year < 100:
            year += 2000

        candidates = []
        for month, day in ((first, second), (second, first)):
            try:
                candidates.append(date(year, month, day))
            except ValueError:
                continue

        if not candidates:
            return None
        today = date.today()
        past_or_today = [candidate for candidate in candidates if candidate <= today]
        return max(past_or_today or candidates)

    def _detect_intent(self, raw_query: str, terms: List[str]) -> str:
        text = " ".join([raw_query, " ".join(terms)]).lower()
        if any(word in text for word in ["hvac", "plumber", "roofing", "contractor"]):
            return "local_service"
        if any(word in text for word in ["developer", "website", "software", "shopify", "app"]):
            return "software_work"
        if any(word in text for word in ["marketing", "smma", "social media"]):
            return "marketing"
        return "general"

    @staticmethod
    def _add_date_operator(platform: str, base: str, since: date) -> str:
        if platform == "twitter":
            return f"{base} since:{since.isoformat()}"
        return f"{base} after:{since.isoformat()}"

    @staticmethod
    def _first_keyword(terms: Iterable[str]) -> str:
        for term in terms:
            words = re.findall(r"[a-zA-Z0-9]+", term.lower())
            if words:
                return words[0]
        return ""

    @staticmethod
    def _twitter_intent_terms(intent: str) -> List[str]:
        if intent == "software_work":
            return [
                '"need a website" (developer OR designer)',
                '"looking for" "web developer"',
                '"website" (needed OR hiring OR urgent)',
                '"shopify developer" (needed OR looking)',
            ]
        if intent == "local_service":
            return [
                '"looking for" recommendation',
                '"need" "contractor"',
                '"urgent" "repair"',
            ]
        return ['"looking for"', '"need help"', '"recommendation"']

    @staticmethod
    def _linkedin_terms(intent: str) -> List[str]:
        if intent == "local_service":
            return ['"Owner" "HVAC"', '"Founder" "HVAC"', '"Operations Manager" "HVAC"']
        if intent == "software_work":
            return ['"hiring" "developer"', '"founder" "need website"', '"startup" "web developer"']
        return ['"owner"', '"founder"', '"hiring"']

    @staticmethod
    def _youtube_terms(intent: str) -> List[str]:
        if intent == "local_service":
            return ['"HVAC" "contact"', '"HVAC contractor"', '"HVAC services"']
        if intent == "software_work":
            return ['"build in public" "need website"', '"startup update" "developer"']
        return ['"contact"', '"business"']

    @staticmethod
    def _quote_if_phrase(term: str) -> str:
        term = " ".join((term or "").split())
        if not term or term.startswith('"') or " " not in term:
            return term
        return f'"{term}"'

    @staticmethod
    def _join_parts(parts: Iterable[str]) -> str:
        return " ".join(part for part in parts if part)

    @staticmethod
    def _unique(values: Iterable[str]) -> List[str]:
        seen = set()
        unique: List[str] = []
        for value in values:
            item = " ".join(str(value or "").split())
            key = item.lower()
            if item and key not in seen:
                seen.add(key)
                unique.append(item)
        return unique
