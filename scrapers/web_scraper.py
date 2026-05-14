"""
Web scraper module - Scrape general websites for leads.
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

from utils.lead_extractor import LeadExtractor, LeadNormalizer
from utils.human_behavior import HumanBehavior, RateLimiter
from utils.validators import DataValidator, DeduplicateManager

logger = logging.getLogger(__name__)


class WebScraper:
    """Scrape general websites for lead information."""
    
    def __init__(self, rate_limit: float = 1.0):
        """
        Initialize web scraper.
        
        Args:
            rate_limit: Requests per second limit
        """
        self.rate_limiter = RateLimiter(rate_limit)
        self.dedup_manager = DeduplicateManager()
        self.session = requests.Session()
    
    def scrape_url(self, url: str, keywords: List[str] = None) -> List[Dict]:
        """
        Scrape a single URL for leads.
        
        Args:
            url: URL to scrape
            keywords: Keywords to filter by (optional)
            
        Returns:
            List of extracted leads
        """
        try:
            self.rate_limiter.wait_if_needed()
            HumanBehavior.random_delay(2, 5)
            
            logger.info(f"Scraping: {url}")
            
            headers = HumanBehavior.get_headers()
            response = self.session.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract all text
            text = soup.get_text(separator=' ')
            
            # Check if keywords match (if provided)
            if keywords:
                text_lower = text.lower()
                if not any(kw.lower() in text_lower for kw in keywords):
                    logger.debug(f"Keywords not found in {url}")
                    return []
            
            # Extract contact information
            leads = self._extract_leads_from_html(soup, text, url)
            
            logger.info(f"Found {len(leads)} leads from {url}")
            return leads
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Error scraping {url}: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error scraping {url}: {e}")
            return []
    
    def search_query(self, query: str, max_results: int = 10) -> List[str]:
        """
        Perform a DuckDuckGo HTML search and return top result URLs.
        """
        return [result["url"] for result in self.search_documents(query, max_results=max_results)]

    def search_documents(self, query: str, max_results: int = 10) -> List[Dict]:
        """
        Perform a DuckDuckGo HTML search and return result metadata.

        Returned fields: url, title, snippet, query.
        """
        try:
            self.rate_limiter.wait_if_needed()
            headers = HumanBehavior.get_headers()
            response = self.session.post(
                'https://html.duckduckgo.com/html/',
                data={'q': query},
                headers=headers,
                timeout=15
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            results = []

            for result in soup.select('.result'):
                result_link = result.select_one('a.result__a')
                if not result_link:
                    continue
                href = result_link.get('href')
                if href and href.startswith('http'):
                    snippet_el = result.select_one('.result__snippet')
                    results.append({
                        'url': href,
                        'title': result_link.get_text(' ', strip=True),
                        'snippet': snippet_el.get_text(' ', strip=True) if snippet_el else '',
                        'query': query,
                    })
                    if len(results) >= max_results:
                        break

            if not results:
                for anchor in soup.find_all('a', href=True):
                    href = anchor['href']
                    if href.startswith('http'):
                        results.append({
                            'url': href,
                            'title': anchor.get_text(' ', strip=True),
                            'snippet': '',
                            'query': query,
                        })
                        if len(results) >= max_results:
                            break

            return results
        except Exception as e:
            logger.error(f"Error during search query '{query}': {e}")
            return []

    def scrape_search_queries(self, keywords: List[str] = None, regions: List[str] = None,
                              max_results: int = 10, queries: List[str] = None) -> List[Dict]:
        """
        Perform search queries for keywords and optional regions, then scrape resulting URLs.
        """
        if not queries and not keywords and not regions:
            return []

        query_texts = list(queries or [])
        if not query_texts:
            if keywords and regions:
                for region in regions:
                    for keyword in keywords:
                        query_texts.append(f"{keyword} {region}")
            elif keywords:
                query_texts.append(' '.join(keywords))
            else:
                query_texts.append(' '.join(regions))

        urls = []
        seen_urls = set()
        for query in query_texts:
            if len(urls) >= max_results:
                break
            search_results = self.search_query(query, max_results=max_results)
            for result_url in search_results:
                if result_url not in seen_urls:
                    seen_urls.add(result_url)
                    urls.append(result_url)
                    if len(urls) >= max_results:
                        break

        return self.scrape_urls(urls, keywords)

    def _extract_leads_from_html(self, soup: BeautifulSoup, text: str, url: str) -> List[Dict]:
        """
        Extract leads from HTML and text content.
        
        Args:
            soup: BeautifulSoup object
            text: Full page text
            url: Source URL
            
        Returns:
            List of lead dictionaries
        """
        leads = []
        
        # Extract emails and phones from page text
        extractor = LeadExtractor()
        emails = extractor.extract_emails(text)
        phones = extractor.extract_phones(text)
        companies = extractor.extract_company_names(text)
        
        # Extract from specific contact elements
        contact_sections = soup.find_all(['div', 'section'], class_=lambda x: x and 'contact' in x.lower())
        
        for section in contact_sections:
            section_text = section.get_text()
            emails.extend(extractor.extract_emails(section_text))
            phones.extend(extractor.extract_phones(section_text))
        
        # Extract from meta tags
        description = soup.find('meta', attrs={'name': 'description'})
        if description and description.get('content'):
            emails.extend(extractor.extract_emails(description['content']))
            phones.extend(extractor.extract_phones(description['content']))
        
        # Create lead objects
        for email in set(emails):
            if not self.dedup_manager.is_duplicate(email=email):
                lead = {
                    'email': email,
                    'phone': '',
                    'company_name': '',
                    'source_url': url,
                    'source_platform': 'web',
                    'post_link': url,
                    'extracted_at': datetime.now().isoformat(),
                }
                
                if DataValidator.is_valid_email(email):
                    lead = LeadNormalizer.normalize_lead(lead)
                    leads.append(lead)
        
        for phone in set(phones):
            if not self.dedup_manager.is_duplicate(phone=phone):
                lead = {
                    'email': '',
                    'phone': phone,
                    'company_name': '',
                    'source_url': url,
                    'source_platform': 'web',
                    'post_link': url,
                    'extracted_at': datetime.now().isoformat(),
                }
                
                if DataValidator.is_valid_phone(phone):
                    lead = LeadNormalizer.normalize_lead(lead)
                    leads.append(lead)
        
        for company in set(companies):
            if not self.dedup_manager.is_duplicate(company=company):
                lead = {
                    'email': '',
                    'phone': '',
                    'company_name': company,
                    'source_url': url,
                    'source_platform': 'web',
                    'post_link': url,
                    'extracted_at': datetime.now().isoformat(),
                }
                
                if DataValidator.is_valid_company_name(company):
                    lead = LeadNormalizer.normalize_lead(lead)
                    leads.append(lead)
        
        # Filter out invalid leads
        filtered_leads = DataValidator.filter_leads(leads)
        
        return filtered_leads
    
    def scrape_urls(self, urls: List[str], keywords: List[str] = None) -> List[Dict]:
        """
        Scrape multiple URLs.
        
        Args:
            urls: List of URLs to scrape
            keywords: Keywords to filter by
            
        Returns:
            Combined list of leads from all URLs
        """
        all_leads = []
        
        for url in urls:
            leads = self.scrape_url(url, keywords)
            all_leads.extend(leads)
        
        logger.info(f"Total leads found: {len(all_leads)}")
        return all_leads
    
    def close(self) -> None:
        """Close the session."""
        self.session.close()
