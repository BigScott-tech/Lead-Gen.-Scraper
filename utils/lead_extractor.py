"""
Lead extractor module - Extract emails, phones, and company names from text and HTML.
"""

import re
from typing import List, Tuple, Dict, Set
from urllib.parse import urljoin
from email_validator import validate_email, EmailNotValidError


class LeadExtractor:
    """Extract contact information from web content."""
    
    # Email regex pattern
    EMAIL_PATTERN = re.compile(
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    )
    
    # Phone patterns (US format, international)
    PHONE_PATTERNS = [
        re.compile(r'(?<!\d)\+?1?[-.\s]?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b(?!\d)'),  # US/CA
        re.compile(r'(?<!\d)\+?[0-9]{1,3}[-.\s]?[0-9]{1,4}[-.\s]?[0-9]{1,4}[-.\s]?[0-9]{1,9}\b(?!\d)'),  # International
    ]
    
    # Company name patterns
    COMPANY_PATTERNS = [
        re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:Inc|LLC|Ltd|Corp|Co|Company|Corporation|Group)\b'),
        re.compile(r'(?:Company|Firm|Agency):\s*([A-Za-z\s&.-]+?)(?:\s*[-|,]|\s*$)'),
    ]
    
    @staticmethod
    def extract_emails(text: str) -> List[str]:
        """Extract valid email addresses from text."""
        if not text:
            return []
        
        emails = []
        matches = LeadExtractor.EMAIL_PATTERN.findall(text)
        
        for email in set(matches):  # Remove duplicates
            try:
                # Validate email format
                validate_email(email, check_deliverability=False)
                if email.lower() not in ['noreply@example.com', 'no-reply@example.com']:
                    emails.append(email.lower())
            except EmailNotValidError:
                continue
        
        return emails
    
    @staticmethod
    def extract_phones(text: str) -> List[str]:
        """Extract phone numbers from text."""
        if not text:
            return []
        
        phones = []
        
        for pattern in LeadExtractor.PHONE_PATTERNS:
            matches = pattern.findall(text)
            for phone in matches:
                # Clean up the phone number
                cleaned = re.sub(r'[^\d+]', '', phone)
                digit_count = len(re.sub(r'\D', '', cleaned))
                if 10 <= digit_count <= 15 and cleaned not in phones:
                    phones.append(cleaned)
        
        return list(set(phones))  # Remove duplicates
    
    @staticmethod
    def extract_company_names(text: str) -> List[str]:
        """Extract company names from text."""
        if not text:
            return []
        
        companies = []
        
        for pattern in LeadExtractor.COMPANY_PATTERNS:
            matches = pattern.findall(text)
            for company in matches:
                if isinstance(company, tuple):
                    company = company[0]
                company = re.sub(r'\s+', ' ', company).strip(" -|,\n\t")
                if len(company) > 2 and company not in companies:
                    companies.append(company)
        
        return list(set(companies))  # Remove duplicates
    
    @staticmethod
    def extract_from_html(html: str) -> Dict[str, List[str]]:
        """Extract all contact information from HTML."""
        return {
            'emails': LeadExtractor.extract_emails(html),
            'phones': LeadExtractor.extract_phones(html),
            'companies': LeadExtractor.extract_company_names(html),
        }
    
    @staticmethod
    def extract_links_from_text(text: str, base_url: str = None) -> List[str]:
        """Extract URLs from text."""
        url_pattern = re.compile(
            r'https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&/=]*)'
        )
        
        links = []
        matches = url_pattern.findall(text)
        
        for link in set(matches):
            if base_url:
                link = urljoin(base_url, link)
            links.append(link)
        
        return links
    
    @staticmethod
    def validate_lead(lead: Dict) -> bool:
        """
        Validate if a lead object has at least one contact method.
        
        Args:
            lead: Dictionary with 'email', 'phone', 'company_name' keys
            
        Returns:
            True if lead has valid contact information
        """
        has_contact = (
            (lead.get('email') and '@' in lead['email']) or
            (lead.get('phone') and len(lead['phone']) >= 10) or
            (lead.get('company_name') and len(lead['company_name']) > 2)
        )
        
        return has_contact


class LeadNormalizer:
    """Normalize and standardize lead data."""
    
    @staticmethod
    def normalize_phone(phone: str) -> str:
        """Normalize phone number format."""
        if not phone:
            return ""
        
        # Remove all non-digit characters
        digits = re.sub(r'\D', '', phone)
        
        # Format as standard
        if len(digits) == 10:
            return f"+1{digits}"
        elif len(digits) == 11 and digits.startswith('1'):
            return f"+{digits}"
        elif len(digits) >= 10:
            return f"+{digits}"
        
        return phone
    
    @staticmethod
    def normalize_email(email: str) -> str:
        """Normalize email address."""
        if not email:
            return ""
        return email.lower().strip()
    
    @staticmethod
    def normalize_lead(lead: Dict) -> Dict:
        """Normalize all fields in a lead object."""
        normalized = {
            'email': LeadNormalizer.normalize_email(lead.get('email', '')),
            'phone': LeadNormalizer.normalize_phone(lead.get('phone', '')),
            'company_name': lead.get('company_name', '').strip(),
            'social_handle': lead.get('social_handle', '').strip(),
            'region': lead.get('region', '').strip(),
            'source_url': lead.get('source_url', '').strip(),
            'source_platform': lead.get('source_platform', '').strip(),
            'post_link': lead.get('post_link', '').strip(),
            'profile_url': lead.get('profile_url', '').strip(),
            'bio_link': lead.get('bio_link', '').strip(),
            'title': lead.get('title', '').strip(),
            'snippet': lead.get('snippet', '').strip(),
            'context': lead.get('context', '').strip(),
            'search_query': lead.get('search_query', '').strip(),
            'lead_type': lead.get('lead_type', '').strip(),
            'confidence': lead.get('confidence', ''),
            'lead_score': lead.get('lead_score', ''),
            'lead_reason': lead.get('lead_reason', '').strip(),
            'extracted_at': lead.get('extracted_at', ''),
        }
        return normalized
