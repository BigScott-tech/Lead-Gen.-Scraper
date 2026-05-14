"""Small local lead scoring layer.

This is intentionally "AI lite": transparent keyword scoring that runs offline
and is easy to tune from config.yaml.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple


class LeadScorer:
    DEFAULT_POSITIVE = {
        "urgent": 25,
        "asap": 20,
        "needed": 18,
        "need": 15,
        "looking for": 15,
        "hire": 14,
        "hiring": 12,
        "recommendation": 10,
        "quote": 10,
        "website": 8,
        "developer": 8,
        "hvac": 8,
        "repair": 6,
    }
    DEFAULT_NEGATIVE = {
        "tutorial": -15,
        "course": -12,
        "giveaway": -20,
        "job seeker": -20,
        "open to work": -10,
        "free download": -12,
    }

    def __init__(self, config: dict | None = None):
        scoring = (config or {}).get("scoring", {})
        self.positive = {**self.DEFAULT_POSITIVE, **scoring.get("positive_terms", {})}
        self.negative = {**self.DEFAULT_NEGATIVE, **scoring.get("negative_terms", {})}

    def score(self, lead: Dict) -> Dict:
        text = self._lead_text(lead)
        score = 0
        reasons: List[str] = []

        for term, weight in self.positive.items():
            if term.lower() in text:
                score += int(weight)
                reasons.append(f"+{weight} {term}")
        for term, weight in self.negative.items():
            if term.lower() in text:
                score += int(weight)
                reasons.append(f"{weight} {term}")

        if lead.get("email"):
            score += 12
            reasons.append("+12 email")
        if lead.get("phone"):
            score += 10
            reasons.append("+10 phone")
        if lead.get("social_handle"):
            score += 4
            reasons.append("+4 handle")
        if lead.get("source_platform") in {"twitter", "linkedin"}:
            score += 4
            reasons.append("+4 intent platform")

        lead["lead_score"] = max(0, min(100, score))
        lead["lead_reason"] = "; ".join(reasons[:6])
        return lead

    def score_many(self, leads: Iterable[Dict]) -> List[Dict]:
        return sorted((self.score(dict(lead)) for lead in leads),
                      key=lambda item: item.get("lead_score", 0),
                      reverse=True)

    @staticmethod
    def _lead_text(lead: Dict) -> str:
        fields = [
            "company_name", "social_handle", "region", "source_url",
            "source_platform", "post_link", "search_query", "title",
            "snippet", "context",
        ]
        return " ".join(str(lead.get(field, "")) for field in fields).lower()
