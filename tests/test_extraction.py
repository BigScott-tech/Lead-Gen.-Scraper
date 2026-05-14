"""
Unit tests for lead extraction and validation.
"""

import pytest
from utils.lead_extractor import LeadExtractor, LeadNormalizer
from utils.command_parser import parse_search_command
from utils.lead_scoring import LeadScorer
from utils.search_planner import SearchPlanner
from utils.validators import DataValidator, DeduplicateManager
from scrapers.browser_tiktok import TikTokBrowserScraper


class TestLeadExtractor:
    """Test lead extraction functionality."""
    
    def test_extract_emails(self):
        """Test email extraction."""
        text = "Contact us at info@example.com or sales@company.org"
        emails = LeadExtractor.extract_emails(text)
        
        assert len(emails) == 2
        assert 'info@example.com' in [e.lower() for e in emails]
        assert 'sales@company.org' in [e.lower() for e in emails]
    
    def test_extract_invalid_emails(self):
        """Test that invalid emails are filtered."""
        text = "Contact noreply@example.com or invalid@"
        emails = LeadExtractor.extract_emails(text)
        
        assert 'noreply@example.com' not in [e.lower() for e in emails]
        assert len([e for e in emails if '@' in e]) == len(emails)
    
    def test_extract_phones(self):
        """Test phone number extraction."""
        text = "Call us at (555) 123-4567 or +1-800-555-1234"
        phones = LeadExtractor.extract_phones(text)
        
        assert len(phones) >= 1
        assert any('555' in phone for phone in phones)
    
    def test_extract_company_names(self):
        """Test company name extraction."""
        text = "We are Google Inc and Apple Corporation, both tech companies"
        companies = LeadExtractor.extract_company_names(text)
        
        assert len(companies) >= 1
    
    def test_extract_from_html(self):
        """Test extraction from HTML."""
        html = "<p>Email: test@example.com</p><p>Phone: 555-1234567</p>"
        result = LeadExtractor.extract_from_html(html)
        
        assert 'emails' in result
        assert 'phones' in result
        assert 'companies' in result


class TestLeadNormalizer:
    """Test lead normalization."""
    
    def test_normalize_phone(self):
        """Test phone normalization."""
        phone = "(555) 123-4567"
        normalized = LeadNormalizer.normalize_phone(phone)
        
        assert '+1' in normalized or '555' in normalized
    
    def test_normalize_email(self):
        """Test email normalization."""
        email = "INFO@EXAMPLE.COM"
        normalized = LeadNormalizer.normalize_email(email)
        
        assert normalized == 'info@example.com'


class TestDataValidator:
    """Test data validation."""
    
    def test_is_valid_email(self):
        """Test email validation."""
        assert DataValidator.is_valid_email('test@example.com') == True
        assert DataValidator.is_valid_email('invalid-email') == False
        assert DataValidator.is_valid_email('') == False
    
    def test_is_valid_phone(self):
        """Test phone validation."""
        assert DataValidator.is_valid_phone('(555) 123-4567') == True
        assert DataValidator.is_valid_phone('555') == False
        assert DataValidator.is_valid_phone('') == False
    
    def test_is_valid_company_name(self):
        """Test company name validation."""
        assert DataValidator.is_valid_company_name('Apple Inc') == True
        assert DataValidator.is_valid_company_name('click here') == False
        assert DataValidator.is_valid_company_name('') == False
    
    def test_is_spam_lead(self):
        """Test spam detection."""
        assert DataValidator.is_spam_lead('test@viagra.com') == True
        assert DataValidator.is_spam_lead('info@example.com') == False


class TestDeduplicateManager:
    """Test deduplication."""
    
    def test_add_and_check_duplicate(self):
        """Test duplicate detection."""
        manager = DeduplicateManager()
        
        # First entry should not be duplicate
        assert manager.is_duplicate(email='test@example.com') == False
        
        # Same email should be duplicate
        assert manager.is_duplicate(email='test@example.com') == True
    
    def test_multiple_fields(self):
        """Test deduplication with multiple fields."""
        manager = DeduplicateManager()
        
        # First entry
        assert manager.is_duplicate(email='test@example.com', phone='5551234567') == False
        
        # Different email, same phone
        assert manager.is_duplicate(email='other@example.com', phone='5551234567') == True


class TestIntegration:
    """Integration tests."""
    
    def test_extract_and_validate_lead(self):
        """Test extracting and validating a lead."""
        text = "Contact John at john@example.com or call (555) 987-6543 at ABC Corp"
        
        emails = LeadExtractor.extract_emails(text)
        phones = LeadExtractor.extract_phones(text)
        
        assert len(emails) > 0
        assert len(phones) > 0
        
        # Validate
        for email in emails:
            assert DataValidator.is_valid_email(email)
        
        for phone in phones:
            assert DataValidator.is_valid_phone(phone)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


class TestSearchPlanner:
    """Test local smart search planning."""

    def test_extract_since_date_from_free_form_query(self):
        planner = SearchPlanner()
        plan = planner.plan(query="website developer needed since 10-05-2026")

        assert plan.since.isoformat() == "2026-05-10"
        assert "website developer needed" in plan.terms

    def test_twitter_queries_use_public_site_filters(self):
        planner = SearchPlanner()
        plan = planner.plan(query="website developer needed since 10-05-2026")
        queries = planner.queries_for_platform("twitter", plan, max_queries=2)

        assert any("site:x.com" in query for query in queries)
        assert all("since:2026-05-10" in query for query in queries)

    def test_instagram_expands_hvac_region_hashtags(self):
        planner = SearchPlanner()
        plan = planner.plan(query="HVAC", regions=["Ontario"])
        terms = planner.instagram_terms(plan)

        assert "#hvacontario" in terms
        assert "#hvactoronto" in terms


class TestCommandParser:
    def test_parse_search_command_flags(self):
        parsed = parse_search_command(
            '-p x,ig -q "website developer needed" -n 20 -r Ontario '
            '--format json --deep --browser --headful --profile buyer1'
        )

        assert parsed.platforms == ["twitter", "instagram"]
        assert parsed.query == "website developer needed"
        assert parsed.amount == 20
        assert parsed.regions == ["Ontario"]
        assert parsed.output_format == "json"
        assert parsed.deep is True
        assert parsed.browser is True
        assert parsed.headful is True
        assert parsed.profile == "buyer1"


class TestLeadScorer:
    def test_scores_urgent_contact_higher(self):
        scorer = LeadScorer()
        lead = scorer.score({
            "email": "buyer@example.com",
            "source_platform": "twitter",
            "snippet": "Need a website developer urgent today",
        })

        assert lead["lead_score"] > 50
        assert "urgent" in lead["lead_reason"]


class TestTikTokBrowserScraper:
    def test_profile_urls_from_video_urls(self):
        urls = [
            "https://www.tiktok.com/@hvacpro/video/123",
            "https://www.tiktok.com/@hvacpro/video/456?lang=en",
            "https://www.tiktok.com/@other/video/789",
        ]

        profiles = TikTokBrowserScraper._profile_urls_from_video_urls(urls)

        assert profiles == [
            "https://www.tiktok.com/@hvacpro",
            "https://www.tiktok.com/@other",
        ]

    def test_safe_browser_limit_caps_to_range(self):
        assert TikTokBrowserScraper._safe_limit(5) == 20
        assert TikTokBrowserScraper._safe_limit(30) == 30
        assert TikTokBrowserScraper._safe_limit(100) == 50
