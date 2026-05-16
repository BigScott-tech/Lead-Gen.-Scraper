"""
Unit tests for lead extraction and validation.
"""

import pytest
from bs4 import BeautifulSoup
from datetime import datetime
from utils.lead_extractor import LeadExtractor, LeadNormalizer
from utils.command_parser import parse_search_command
from utils.lead_scoring import LeadScorer
from utils.lead_store import LeadStore
from utils.search_planner import SearchPlanner
from utils.validators import DataValidator, DeduplicateManager
from scrapers.browser_tiktok import TikTokBrowserScraper
from scrapers.social_scrapers import SearchBackedSocialScraper, _handle_from_url, _is_platform_url
from scrapers.web_scraper import WebScraper
from main import LeadScrappingEngine


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

    def test_extract_phones_ignores_long_activity_ids(self):
        text = "linkedin.com/feed/update/urn:li:activity:7108555836871852032 mobile app development"
        phones = LeadExtractor.extract_phones(text)

        assert phones == []
    
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


class TestOutputNaming:
    """Test output filename generation."""

    def test_sanitize_for_filename(self):
        engine = LeadScrappingEngine("config.yaml")
        assert engine._sanitize_for_filename("HVAC Contractor Ontario!") == "hvac_contractor_ontario"
        assert engine._sanitize_for_filename("   #hvacontario search") == "hvacontario_search"

    def test_build_output_filename_includes_context(self):
        engine = LeadScrappingEngine("config.yaml")
        filename = engine.build_output_filename(
            fmt="json",
            query="#hvacontario",
            platforms=["instagram"],
            regions=["Ontario"],
        )

        assert filename.startswith("leads_hvacontario_instagram_ontario_")
        assert filename.endswith(".json")


class TestBrowserFallbackUrls:
    """Test browser fallback URL generation."""

    def test_build_browser_fallback_urls_for_instagram_and_twitter(self):
        engine = LeadScrappingEngine("config.yaml")
        urls = engine.build_browser_fallback_urls(
            query="#hvacontario",
            platforms=["instagram", "twitter"],
            regions=["Ontario"],
        )

        assert any("instagram.com/explore/tags/hvacontario" in url for url in urls)
        assert any("twitter.com/search" in url for url in urls)
        assert len(urls) == 2


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

    def test_filter_accepts_social_profile_links(self):
        leads = DataValidator.filter_leads([{
            "source_platform": "instagram",
            "profile_url": "https://instagram.com/lghomecomfort",
            "lead_type": "local_business_profile",
        }])

        assert len(leads) == 1

    def test_filter_rejects_low_intent_social_links(self):
        leads = DataValidator.filter_leads([{
            "source_platform": "linkedin",
            "profile_url": "https://www.linkedin.com/in/someone",
            "lead_type": "low_intent_social_result",
        }])

        assert leads == []


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
        planner = SearchPlanner({
            "platform_query_terms": {
                "twitter": ['"looking for a web designer"'],
            }
        })
        plan = planner.plan(query="website developer needed since 10-05-2026")
        queries = planner.queries_for_platform("twitter", plan, max_queries=20)

        assert any("site:x.com" in query for query in queries)
        assert any("since:2026-05-10" in query for query in queries)
        assert any("looking for a web designer" in query for query in queries)

    def test_user_query_is_prioritized_for_twitter(self):
        planner = SearchPlanner({
            "platform_query_terms": {
                "twitter": ['"looking for a web designer"'],
            }
        })
        plan = planner.plan(query="website developer needed since 14-05-2026")
        queries = planner.queries_for_platform("twitter", plan, max_queries=2)

        assert queries[0].startswith('"website developer needed" since:2026-05-14')

    def test_instagram_expands_hvac_region_hashtags(self):
        planner = SearchPlanner()
        plan = planner.plan(query="HVAC", regions=["Ontario"])
        terms = planner.instagram_terms(plan)

        assert "#hvacontario" in terms
        assert "#hvactoronto" in terms

    def test_instagram_understands_compound_hashtag_queries(self):
        planner = SearchPlanner()
        plan = planner.plan(query="#hvacontario")
        terms = planner.instagram_terms(plan)

        assert "#hvacontario" in terms
        assert "hvac Ontario" in terms

    def test_facebook_queries_include_group_waypoints(self):
        planner = SearchPlanner()
        plan = planner.plan(query="HVAC contractor Ontario")
        queries = planner.queries_for_platform("facebook", plan, max_queries=6)

        assert any("site:facebook.com/groups" in query for query in queries)


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

    def test_scores_linkedin_job_cards_as_leads(self):
        scorer = LeadScorer()
        lead = scorer.score({
            "company_name": "Example Labs",
            "source_platform": "linkedin",
            "post_link": "https://www.linkedin.com/jobs/view/frontend-developer-123",
            "title": "Frontend Developer",
        })

        assert lead["lead_score"] >= 20
        assert "job listing" in lead["lead_reason"]


class TestSocialSearchFilters:
    def test_linkedin_profile_handle_uses_slug(self):
        assert (
            _handle_from_url("https://www.linkedin.com/in/talent-adquisition-554018b3/", "linkedin")
            == "talent-adquisition-554018b3"
        )

    def test_linkedin_auth_pages_are_not_leads(self):
        assert _is_platform_url("https://www.linkedin.com/login", "linkedin") is False
        assert _is_platform_url("https://www.linkedin.com/signup", "linkedin") is False
        assert _is_platform_url("https://www.linkedin.com/jobs/search", "linkedin") is False
        assert _is_platform_url("https://www.linkedin.com/posts/example-activity-123", "linkedin") is True

    def test_x_result_keeps_post_profile_and_contact(self):
        scraper = SearchBackedSocialScraper()
        scraper.platform = "twitter"
        plan = SearchPlanner().plan(query="website developer needed since 14-05-2026")
        leads = scraper._leads_from_search_result({
            "url": "https://x.com/denverbuyer/status/12345",
            "title": "Denver Buyer (@denverbuyer) on X",
            "snippet": "Need a website developer ASAP. Email buyer@example.com for details.",
            "query": '"website developer needed" since:2026-05-14 site:x.com',
        }, plan)

        assert leads[0]["social_handle"] == "@denverbuyer"
        assert leads[0]["profile_url"] == "https://x.com/denverbuyer"
        assert leads[0]["post_link"] == "https://x.com/denverbuyer/status/12345"
        assert leads[0]["email"] == "buyer@example.com"
        assert leads[0]["lead_type"] == "buyer_intent_post"

    def test_instagram_local_profile_includes_profile_and_bio_link(self):
        scraper = SearchBackedSocialScraper()
        scraper.platform = "instagram"
        plan = SearchPlanner().plan(query="#hvacontario")
        leads = scraper._leads_from_search_result({
            "url": "https://www.instagram.com/lghomecomfort/",
            "title": "LG Home Comfort (@lghomecomfort) • Instagram photos and videos",
            "snippet": "HVAC Ontario. Book service at https://lghomecomfort.ca/contact",
            "query": "#hvacontario site:instagram.com",
        }, plan)

        assert leads[0]["social_handle"] == "@lghomecomfort"
        assert leads[0]["profile_url"] == "https://instagram.com/lghomecomfort"
        assert leads[0]["bio_link"] == "https://lghomecomfort.ca/contact"
        assert leads[0]["lead_type"] == "local_business_profile"

    def test_instagram_post_result_derives_profile_from_title_handle(self):
        scraper = SearchBackedSocialScraper()
        scraper.platform = "instagram"
        plan = SearchPlanner().plan(query="#hvacontario")
        leads = scraper._leads_from_search_result({
            "url": "https://www.instagram.com/p/ABC123/",
            "title": "Air One Peel (@aironepeel) • Instagram photo",
            "snippet": "#hvacontario furnace repair and AC installation",
            "query": "#hvacontario site:instagram.com/p",
        }, plan)

        assert leads[0]["social_handle"] == "@aironepeel"
        assert leads[0]["profile_url"] == "https://instagram.com/aironepeel"
        assert leads[0]["post_link"] == "https://www.instagram.com/p/ABC123/"

    def test_facebook_group_result_is_a_community_lead(self):
        scraper = SearchBackedSocialScraper()
        scraper.platform = "facebook"
        plan = SearchPlanner().plan(query="HVAC contractor Ontario")
        leads = scraper._leads_from_search_result({
            "url": "https://www.facebook.com/groups/ontariohvacpros",
            "title": "Ontario HVAC Pros | Facebook",
            "snippet": "Community for HVAC contractors, repairs, and homeowner recommendations.",
            "query": '"HVAC contractor" site:facebook.com/groups',
        }, plan)

        assert leads[0]["social_handle"] == "ontariohvacpros"
        assert leads[0]["profile_url"] == "https://www.facebook.com/groups/ontariohvacpros"
        assert leads[0]["lead_type"] == "community"


class TestLeadStore:
    def test_contactless_job_fingerprint_uses_post_link(self):
        base = {
            "company_name": "Example Labs",
            "source_url": "https://www.linkedin.com/jobs/web-developer-jobs-denver-co",
            "source_platform": "linkedin",
            "title": "Frontend Developer",
            "region": "Denver, CO",
        }

        first = {**base, "post_link": "https://www.linkedin.com/jobs/view/1"}
        second = {**base, "post_link": "https://www.linkedin.com/jobs/view/2"}

        assert LeadStore.fingerprint(first) != LeadStore.fingerprint(second)

    def test_contact_fingerprint_prefers_email_across_sources(self):
        first = {"email": "buyer@example.com", "source_url": "https://example.com/a"}
        second = {"email": "buyer@example.com", "source_url": "https://example.com/b"}

        assert LeadStore.fingerprint(first) == LeadStore.fingerprint(second)


class TestWebScraper:
    def test_clean_result_url_resolves_google_redirects(self):
        url = (
            "https://www.google.com/url?sa=t&source=web&rct=j&opi=89978449&"
            "url=https://www.linkedin.com/jobs/web-developer-jobs-denver-co&ved=2ahUKEwiKvMvl7ruUAxVDlWoFHQ3uJAMQFnoECCsQAQ&usg=AOvVaw2Tp2LEY5DgJLLLLDFZBxf3"
        )
        resolved = WebScraper._clean_result_url(url)
        assert resolved.startswith("https://www.linkedin.com/jobs/web-developer-jobs-denver-co")

    def test_clean_result_url_resolves_duckduckgo_redirects(self):
        url = "/l/?uddg=https%3A%2F%2Fexample.com%2Fcontact%3Futm_source%3Dsearch%26id%3D42"
        assert WebScraper._clean_result_url(url) == "https://example.com/contact?id=42"

    def test_extracts_linkedin_job_cards_as_structured_leads(self):
        html = """
        <ul class="jobs-search__results-list">
          <li class="base-card base-search-card job-search-card">
            <a class="base-card__full-link" href="/jobs/view/software-engineer-123">
              <span class="sr-only">Software Engineer (Web Developer)</span>
            </a>
            <div class="base-search-card__info">
              <h3 class="base-search-card__title">Software Engineer (Web Developer)</h3>
              <h4 class="base-search-card__subtitle">
                <a class="hidden-nested-link" href="/company/bright-vision-tech">
                  Bright Vision Technologies
                </a>
              </h4>
              <span class="job-search-card__location">Denver, CO</span>
              <time class="job-search-card__listdate">1 week ago</time>
            </div>
          </li>
        </ul>
        """
        soup = BeautifulSoup(html, "html.parser")
        scraper = WebScraper(rate_limit=1000)

        leads = scraper._extract_leads_from_html(
            soup,
            soup.get_text(" ", strip=True),
            "https://www.linkedin.com/jobs/web-developer-jobs-denver-co",
        )

        assert len(leads) == 1
        assert leads[0]["company_name"] == "Bright Vision Technologies"
        assert leads[0]["title"] == "Software Engineer (Web Developer)"
        assert leads[0]["region"] == "Denver, CO"
        assert leads[0]["post_link"] == "https://www.linkedin.com/jobs/view/software-engineer-123"
        assert leads[0]["source_platform"] == "linkedin"

    def test_extracts_schema_org_job_postings(self):
        html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Frontend Developer",
          "datePosted": "2026-05-16",
          "url": "https://example.com/jobs/frontend",
          "hiringOrganization": {
            "@type": "Organization",
            "name": "Example Labs",
            "url": "https://example.com"
          },
          "jobLocation": {
            "@type": "Place",
            "address": {
              "addressLocality": "Denver",
              "addressRegion": "CO",
              "addressCountry": "US"
            }
          }
        }
        </script>
        """
        soup = BeautifulSoup(html, "html.parser")
        scraper = WebScraper(rate_limit=1000)

        leads = scraper._extract_leads_from_html(
            soup,
            soup.get_text(" ", strip=True),
            "https://example.com/jobs",
        )

        assert len(leads) == 1
        assert leads[0]["company_name"] == "Example Labs"
        assert leads[0]["title"] == "Frontend Developer"
        assert leads[0]["region"] == "Denver | CO | US"
        assert leads[0]["post_link"] == "https://example.com/jobs/frontend"

    def test_scrape_custom_url_updates_output_state(self, monkeypatch):
        def fake_scrape_url(self, url):
            return [{
                "email": "",
                "phone": "",
                "company_name": "Acme Labs",
                "source_url": url,
                "source_platform": "web",
                "post_link": url,
                "title": "Website Redesign Needed",
                "snippet": "",
                "extracted_at": datetime.now().isoformat(),
            }]

        monkeypatch.setattr(WebScraper, "scrape_url", fake_scrape_url)
        engine = LeadScrappingEngine("config.yaml")

        leads = engine.scrape_custom_url("https://example.com/jobs", persist=False)

        assert leads
        assert engine.all_leads == leads


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
