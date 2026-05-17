"""
validators.py — Lead data validation and deduplication.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional


class DataValidator:
    """Validate extracted lead data."""

    # Spam domains / keywords to reject
    _SPAM_WORDS = frozenset([
        "viagra", "casino", "lottery", "adult", "xxx", "spam",
        "phishing", "scam", "free-money", "clickbait",
    ])
    _SPAM_EMAIL_PREFIXES = frozenset([
        "noreply", "no-reply", "donotreply", "do-not-reply",
        "mailer-daemon", "postmaster", "bounce",
    ])
    _INVALID_COMPANY_WORDS = frozenset([
        "click here", "contact us", "email us", "phone", "address",
        "website", "http", "www",
    ])

    @staticmethod
    def is_valid_email(email: str) -> bool:
        if not email or "@" not in email:
            return False
        pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, email.strip()):
            return False
        local = email.split("@")[0].lower()
        if local in DataValidator._SPAM_EMAIL_PREFIXES:
            return False
        return True

    @staticmethod
    def is_valid_phone(phone: str) -> bool:
        if not phone:
            return False
        digits = re.sub(r"\D", "", phone)
        if len(digits) < 7:
            return False
        # Try phonenumbers library for richer validation
        try:
            import phonenumbers
            parsed = phonenumbers.parse(phone if phone.startswith("+") else f"+{digits}")
            if phonenumbers.is_valid_number(parsed):
                return True
            return 7 <= len(digits) <= 15
        except Exception:
            # Fallback: accept if 7–15 digits
            return 7 <= len(digits) <= 15

    @staticmethod
    def is_valid_company_name(company: str) -> bool:
        company = " ".join((company or "").split())
        if len(company) < 3 or len(company) > 120:
            return False
        cl = company.lower()
        if any(w in cl for w in DataValidator._INVALID_COMPANY_WORDS):
            return False
        if cl.count("@") or cl.startswith(("http://", "https://")):
            return False
        return True

    @staticmethod
    def is_spam_lead(email: str = "", company: str = "") -> bool:
        text = f"{email} {company}".lower()
        return any(w in text for w in DataValidator._SPAM_WORDS)

    @staticmethod
    def validate_lead_object(lead: Dict) -> Dict[str, bool]:
        return {
            "email_valid": DataValidator.is_valid_email(lead.get("email", "")),
            "phone_valid": DataValidator.is_valid_phone(lead.get("phone", "")),
            "company_valid": DataValidator.is_valid_company_name(lead.get("company_name", "")),
            "is_spam": DataValidator.is_spam_lead(lead.get("email", ""), lead.get("company_name", "")),
        }

    @staticmethod
    def filter_leads(leads: List[Dict], require_email: bool = False) -> List[Dict]:
        """Remove spam and leads with no usable contact information."""
        valid = []
        for lead in leads:
            if DataValidator.is_spam_lead(lead.get("email", ""), lead.get("company_name", "")):
                continue
            has_email   = DataValidator.is_valid_email(lead.get("email", ""))
            has_phone   = DataValidator.is_valid_phone(lead.get("phone", ""))
            has_company = DataValidator.is_valid_company_name(lead.get("company_name", ""))
            has_handle  = bool(lead.get("social_handle", "").strip())
            has_social_link = (
                bool((lead.get("profile_url") or lead.get("post_link") or lead.get("bio_link") or "").strip())
                and bool(lead.get("source_platform", "").strip())
                and lead.get("lead_type") != "low_intent_social_result"
            )
            if require_email:
                if has_email:
                    valid.append(lead)
            else:
                if has_email or has_phone or has_company or has_handle or has_social_link:
                    valid.append(lead)
        return valid


class DeduplicateManager:
    """Session-level lead deduplication."""

    def __init__(self):
        self.seen_emails:    set[str] = set()
        self.seen_phones:    set[str] = set()
        self.seen_companies: set[str] = set()
        self.seen_handles:   set[str] = set()
        self._count = 0

    def is_duplicate(self, email: str = "", phone: str = "",
                     company: str = "", handle: str = "") -> bool:
        email_k   = email.lower().strip()   if email   else ""
        phone_k   = re.sub(r"\D", "", phone) if phone   else ""
        company_k = company.lower().strip()  if company else ""
        handle_k  = handle.lower().strip()   if handle  else ""

        if (
            (email_k   and email_k   in self.seen_emails)   or
            (phone_k   and phone_k   in self.seen_phones)   or
            (company_k and company_k in self.seen_companies) or
            (handle_k  and handle_k  in self.seen_handles)
        ):
            return True

        if email_k:   self.seen_emails.add(email_k)
        if phone_k:   self.seen_phones.add(phone_k)
        if company_k: self.seen_companies.add(company_k)
        if handle_k:  self.seen_handles.add(handle_k)
        self._count += 1
        return False

    def clear(self) -> None:
        self.seen_emails.clear()
        self.seen_phones.clear()
        self.seen_companies.clear()
        self.seen_handles.clear()
        self._count = 0

    def get_count(self) -> int:
        return self._count
