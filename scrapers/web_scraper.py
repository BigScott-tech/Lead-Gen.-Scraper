"""
Web scraper module - Scrape general websites for leads.
"""

import logging
import json
import time
from typing import List, Dict, Optional
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse

from utils.lead_extractor import LeadExtractor, LeadNormalizer
from utils.human_behavior import HumanBehavior, RateLimiter
from utils.validators import DataValidator, DeduplicateManager

logger = logging.getLogger(__name__)


class WebScraper:
    """Scrape general websites for lead information."""

    TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
    TRACKING_QUERY_PARAMS = {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "refid",
        "trk",
        "trackingid",
        "utm_campaign",
        "utm_content",
        "utm_medium",
        "utm_source",
        "utm_term",
    }
    
    def __init__(self, rate_limit: float = 1.0):
        """
        Initialize web scraper.
        
        Args:
            rate_limit: Requests per second limit
        """
        self.rate_limiter = RateLimiter(rate_limit)
        self.dedup_manager = DeduplicateManager()
        self.session = requests.Session()
        self.last_search_status: List[Dict] = []
    
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
            url = self._clean_result_url(url)
            
            logger.info(f"Scraping: {url}")
            
            response = self._get(url, timeout=15)
            
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
            leads = self._extract_leads_from_html(soup, text, response.url or url)
            
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
        Search multiple public engines and return result metadata.

        Returned fields: url, title, snippet, query.
        """
        self.last_search_status = []
        providers = [
            self._search_duckduckgo,
            self._search_brave,
            self._search_yahoo,
        ]
        seen = set()
        merged: List[Dict] = []
        for provider in providers:
            if len(merged) >= max_results:
                break
            docs = provider(query, max_results=max_results)
            for doc in docs:
                url = doc.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    merged.append(doc)
                    if len(merged) >= max_results:
                        break
        return merged

    def _get(self, url: str, timeout: int = 15) -> requests.Response:
        """GET with small retries for normal network/search hiccups."""
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = self.session.get(
                    url,
                    headers=HumanBehavior.get_headers(),
                    timeout=timeout,
                    allow_redirects=True,
                )
                if (
                    response.status_code in self.TRANSIENT_STATUS_CODES
                    and attempt < 3
                ):
                    logger.warning(
                        "Transient HTTP %s for %s; retrying (%s/3)",
                        response.status_code, url, attempt,
                    )
                    time.sleep(0.75 * attempt)
                    continue
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt >= 3:
                    raise
                logger.warning("Request failed for %s; retrying (%s/3): %s", url, attempt, exc)
                time.sleep(0.75 * attempt)
        raise last_error or requests.exceptions.RequestException(f"Failed to fetch {url}")

    def _search_duckduckgo(self, query: str, max_results: int = 10) -> List[Dict]:
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
            if self._is_blocked_page(soup):
                self._record_search_status("duckduckgo", query, "blocked")
                return []
            results = []

            for result in soup.select('.result'):
                result_link = result.select_one('a.result__a')
                if not result_link:
                    continue
                href = self._clean_result_url(result_link.get('href', ''))
                if href and href.startswith('http'):
                    snippet_el = result.select_one('.result__snippet')
                    results.append({
                        'url': href,
                        'title': result_link.get_text(' ', strip=True),
                        'snippet': snippet_el.get_text(' ', strip=True) if snippet_el else '',
                        'query': query,
                        'provider': 'duckduckgo',
                    })
                    if len(results) >= max_results:
                        break

            if not results:
                for anchor in soup.find_all('a', href=True):
                    href = self._clean_result_url(anchor['href'])
                    if href.startswith('http'):
                        results.append({
                            'url': href,
                            'title': anchor.get_text(' ', strip=True),
                            'snippet': '',
                            'query': query,
                            'provider': 'duckduckgo',
                        })
                        if len(results) >= max_results:
                            break

            self._record_search_status("duckduckgo", query, f"{len(results)} results")
            return results
        except Exception as e:
            logger.error(f"Error during search query '{query}': {e}")
            self._record_search_status("duckduckgo", query, f"error: {e}")
            return []

    def _search_brave(self, query: str, max_results: int = 10) -> List[Dict]:
        try:
            self.rate_limiter.wait_if_needed()
            response = self.session.get(
                "https://search.brave.com/search",
                params={"q": query, "source": "web"},
                headers=HumanBehavior.get_headers(),
                timeout=15,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            if self._is_blocked_page(soup):
                self._record_search_status("brave", query, "blocked")
                return []
            results: List[Dict] = []
            for result in soup.select(".snippet"):
                link = result.find("a", href=True)
                if not link:
                    continue
                href = self._clean_result_url(link["href"])
                if not href.startswith("http"):
                    continue
                title = link.get_text(" ", strip=True)
                snippet = result.get_text(" ", strip=True)
                results.append({
                    "url": href,
                    "title": title,
                    "snippet": snippet,
                    "query": query,
                    "provider": "brave",
                })
                if len(results) >= max_results:
                    break
            self._record_search_status("brave", query, f"{len(results)} results")
            return results
        except Exception as e:
            logger.error(f"Brave search error for '{query}': {e}")
            self._record_search_status("brave", query, f"error: {e}")
            return []

    def _search_yahoo(self, query: str, max_results: int = 10) -> List[Dict]:
        try:
            self.rate_limiter.wait_if_needed()
            response = self.session.get(
                "https://search.yahoo.com/search",
                params={"p": query},
                headers=HumanBehavior.get_headers(),
                timeout=15,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            if self._is_blocked_page(soup):
                self._record_search_status("yahoo", query, "blocked")
                return []
            results: List[Dict] = []
            for result in soup.select(".algo"):
                link = result.find("a", href=True)
                if not link:
                    continue
                href = self._clean_result_url(link["href"])
                if not href.startswith("http"):
                    continue
                title = link.get_text(" ", strip=True)
                snippet = result.get_text(" ", strip=True)
                results.append({
                    "url": href,
                    "title": title,
                    "snippet": snippet,
                    "query": query,
                    "provider": "yahoo",
                })
                if len(results) >= max_results:
                    break
            self._record_search_status("yahoo", query, f"{len(results)} results")
            return results
        except Exception as e:
            logger.error(f"Yahoo search error for '{query}': {e}")
            self._record_search_status("yahoo", query, f"error: {e}")
            return []

    def _record_search_status(self, provider: str, query: str, status: str) -> None:
        self.last_search_status.append({
            "provider": provider,
            "query": query,
            "status": status,
        })
        logger.info("Search provider %s for '%s': %s", provider, query, status)

    @staticmethod
    def _is_blocked_page(soup: BeautifulSoup) -> bool:
        text = soup.get_text(" ", strip=True).lower()
        blocked_markers = [
            "please complete the following challenge",
            "one last step",
            "solve the challenge",
            "automated queries",
            "unusual traffic",
        ]
        return any(marker in text for marker in blocked_markers)

    @staticmethod
    def _clean_result_url(href: str) -> str:
        if not href:
            return ""
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        host = parsed.netloc.lower()

        if (
            (host == "google.com" or host.endswith(".google.com") or host.startswith("www.google."))
            and parsed.path == "/url"
            and qs.get("url")
        ):
            return WebScraper._strip_tracking_query(unquote(qs["url"][0]))
        if parsed.netloc.endswith("l.facebook.com") and qs.get("u"):
            return WebScraper._strip_tracking_query(unquote(qs["u"][0]))
        if "r.search.yahoo.com" in parsed.netloc:
            path = unquote(parsed.path)
            marker = "/RU="
            if marker in path:
                return WebScraper._strip_tracking_query(path.split(marker, 1)[1].split("/RK=", 1)[0])

        for key in ("uddg", "u", "url"):
            if key in qs and qs[key]:
                return WebScraper._strip_tracking_query(unquote(qs[key][0]))
        if href.startswith("//"):
            href = f"https:{href}"
        return WebScraper._strip_tracking_query(href)

    @staticmethod
    def _strip_tracking_query(url: str) -> str:
        parsed = urlparse(url)
        if not parsed.query:
            return url
        filtered = []
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items():
            key_lower = key.lower()
            if key_lower.startswith("utm_") or key_lower in WebScraper.TRACKING_QUERY_PARAMS:
                continue
            for value in values:
                filtered.append((key, value))
        return urlunparse(parsed._replace(query=urlencode(filtered, doseq=True)))

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
        search_leads: List[Dict] = []
        seen_urls = set()
        for query in query_texts:
            if len(urls) >= max_results:
                break
            search_results = self.search_documents(query, max_results=max_results)
            for result in search_results:
                search_leads.extend(self._leads_from_search_document(result))
                result_url = result.get("url", "")
                if result_url not in seen_urls:
                    seen_urls.add(result_url)
                    urls.append(result_url)
                    if len(urls) >= max_results:
                        break

        page_leads = self.scrape_urls(urls, keywords)
        return self._dedupe_leads(search_leads + page_leads)

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
        structured_leads = self._extract_structured_leads_from_html(soup, url)
        contact_leads = self._extract_contact_leads_from_html(soup, text, url)
        if structured_leads:
            contact_only = [
                lead for lead in contact_leads
                if lead.get("email") or lead.get("phone") or lead.get("social_handle")
            ]
            return self._dedupe_leads(structured_leads + contact_only)
        return contact_leads

    def _extract_contact_leads_from_html(self, soup: BeautifulSoup, text: str, url: str) -> List[Dict]:
        """Extract traditional contact leads from arbitrary page HTML."""
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

    def _extract_structured_leads_from_html(self, soup: BeautifulSoup, url: str) -> List[Dict]:
        """Extract high-signal lead cards from job/search pages and JSON-LD."""
        leads: List[Dict] = []
        leads.extend(self._extract_jsonld_job_leads(soup, url))

        parsed = urlparse(url)
        if "linkedin.com" in parsed.netloc.lower() and "/jobs" in parsed.path.lower():
            leads.extend(self._extract_linkedin_job_cards(soup, url))

        return self._dedupe_leads(leads)

    def _extract_linkedin_job_cards(self, soup: BeautifulSoup, source_url: str) -> List[Dict]:
        leads: List[Dict] = []
        cards = soup.select(".job-search-card, .base-search-card")
        for card in cards:
            title = self._text(card.select_one(".base-search-card__title"))
            company_el = card.select_one(".base-search-card__subtitle")
            company = self._text(company_el)
            location = self._text(card.select_one(".job-search-card__location"))
            list_date = self._text(card.select_one("time"))
            benefit = self._text(card.select_one(".job-posting-benefits__text"))
            job_link = self._href(
                card.select_one("a.base-card__full-link[href]") or card.find("a", href=True),
                source_url,
            )
            company_link = self._href(
                card.select_one(".base-search-card__subtitle a[href], a.hidden-nested-link[href]"),
                source_url,
            )

            if not title or not company:
                continue
            snippet = self._join_text([location, benefit, list_date])
            lead = self._make_structured_lead(
                company=company,
                title=title,
                region=location,
                source_url=source_url,
                post_link=job_link or source_url,
                profile_url=company_link,
                snippet=snippet,
                platform="linkedin",
            )
            if lead:
                leads.append(lead)
        return leads

    def _extract_jsonld_job_leads(self, soup: BeautifulSoup, source_url: str) -> List[Dict]:
        leads: List[Dict] = []
        for item in self._jsonld_items(soup):
            item_type = item.get("@type", "")
            type_values = item_type if isinstance(item_type, list) else [item_type]
            if "JobPosting" not in {str(value) for value in type_values}:
                continue
            org = item.get("hiringOrganization") or item.get("sourceOrganization") or {}
            company = self._jsonld_name(org)
            title = self._text_value(item.get("title"))
            region = self._jsonld_location(item.get("jobLocation"))
            post_link = self._text_value(item.get("url")) or source_url
            profile_url = self._jsonld_url(org)
            snippet = self._join_text([
                self._text_value(item.get("employmentType")),
                self._text_value(item.get("datePosted")),
                self._text_value(item.get("description"))[:240],
            ])
            lead = self._make_structured_lead(
                company=company,
                title=title,
                region=region,
                source_url=source_url,
                post_link=urljoin(source_url, post_link),
                profile_url=urljoin(source_url, profile_url) if profile_url else "",
                snippet=snippet,
                platform=self._platform_from_url(source_url),
            )
            if lead:
                leads.append(lead)
        return leads

    def _leads_from_search_document(self, result: Dict) -> List[Dict]:
        url = result.get("url", "")
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        query = result.get("query", "")
        text = self._join_text([title, snippet])
        leads: List[Dict] = []
        extractor = LeadExtractor()
        platform = self._platform_from_url(url)

        for email in set(extractor.extract_emails(text)):
            if DataValidator.is_valid_email(email):
                leads.append(LeadNormalizer.normalize_lead({
                    "email": email,
                    "source_url": url,
                    "source_platform": platform,
                    "post_link": url,
                    "title": title,
                    "snippet": snippet,
                    "context": text,
                    "search_query": query,
                    "extracted_at": datetime.now().isoformat(),
                }))
        for phone in set(extractor.extract_phones(text)):
            if DataValidator.is_valid_phone(phone):
                leads.append(LeadNormalizer.normalize_lead({
                    "phone": phone,
                    "source_url": url,
                    "source_platform": platform,
                    "post_link": url,
                    "title": title,
                    "snippet": snippet,
                    "context": text,
                    "search_query": query,
                    "extracted_at": datetime.now().isoformat(),
                }))
        for company in set(extractor.extract_company_names(text)):
            if DataValidator.is_valid_company_name(company):
                leads.append(LeadNormalizer.normalize_lead({
                    "company_name": company,
                    "source_url": url,
                    "source_platform": platform,
                    "post_link": url,
                    "title": title,
                    "snippet": snippet,
                    "context": text,
                    "search_query": query,
                    "extracted_at": datetime.now().isoformat(),
                }))
        return DataValidator.filter_leads(leads)

    def _make_structured_lead(
        self,
        *,
        company: str,
        title: str,
        region: str,
        source_url: str,
        post_link: str,
        profile_url: str = "",
        snippet: str = "",
        platform: str = "web",
    ) -> Optional[Dict]:
        company = self._text_value(company)
        title = self._text_value(title)
        if not title or not DataValidator.is_valid_company_name(company):
            return None
        return LeadNormalizer.normalize_lead({
            "email": "",
            "phone": "",
            "company_name": company,
            "social_handle": "",
            "region": self._text_value(region),
            "source_url": source_url,
            "source_platform": platform,
            "post_link": post_link or source_url,
            "profile_url": profile_url,
            "title": title,
            "snippet": snippet,
            "context": self._join_text([title, company, region, snippet]),
            "extracted_at": datetime.now().isoformat(),
        })

    @classmethod
    def _dedupe_leads(cls, leads: List[Dict]) -> List[Dict]:
        seen = set()
        unique: List[Dict] = []
        for lead in DataValidator.filter_leads(leads):
            key = cls._lead_key(lead)
            if key in seen:
                continue
            seen.add(key)
            unique.append(lead)
        return unique

    @staticmethod
    def _lead_key(lead: Dict) -> str:
        for field in ("email", "phone", "post_link", "profile_url", "source_url"):
            value = str(lead.get(field, "")).strip().lower()
            if value:
                return f"{field}:{value}"
        return "|".join([
            str(lead.get("company_name", "")).strip().lower(),
            str(lead.get("title", "")).strip().lower(),
            str(lead.get("region", "")).strip().lower(),
        ])

    @staticmethod
    def _jsonld_items(soup: BeautifulSoup) -> List[Dict]:
        items: List[Dict] = []
        for script in soup.find_all("script", attrs={"type": lambda value: value and "ld+json" in value.lower()}):
            raw = script.string or script.get_text()
            if not raw or not raw.strip():
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            items.extend(WebScraper._walk_jsonld(parsed))
        return items

    @staticmethod
    def _walk_jsonld(value) -> List[Dict]:
        if isinstance(value, list):
            items: List[Dict] = []
            for item in value:
                items.extend(WebScraper._walk_jsonld(item))
            return items
        if not isinstance(value, dict):
            return []
        items = [value]
        graph = value.get("@graph")
        if graph:
            items.extend(WebScraper._walk_jsonld(graph))
        return items

    @staticmethod
    def _jsonld_name(value) -> str:
        if isinstance(value, list):
            for item in value:
                name = WebScraper._jsonld_name(item)
                if name:
                    return name
            return ""
        if isinstance(value, dict):
            return WebScraper._text_value(value.get("name"))
        return WebScraper._text_value(value)

    @staticmethod
    def _jsonld_url(value) -> str:
        if isinstance(value, list):
            for item in value:
                url = WebScraper._jsonld_url(item)
                if url:
                    return url
            return ""
        if isinstance(value, dict):
            return WebScraper._text_value(value.get("url") or value.get("@id"))
        return ""

    @staticmethod
    def _jsonld_location(value) -> str:
        if isinstance(value, list):
            return WebScraper._join_text(WebScraper._jsonld_location(item) for item in value)
        if not isinstance(value, dict):
            return WebScraper._text_value(value)
        address = value.get("address")
        if isinstance(address, dict):
            return WebScraper._join_text([
                address.get("addressLocality"),
                address.get("addressRegion"),
                address.get("addressCountry"),
            ])
        return WebScraper._text_value(value.get("name") or address)

    @staticmethod
    def _href(element, base_url: str) -> str:
        if not element:
            return ""
        href = element.get("href", "")
        return WebScraper._clean_result_url(urljoin(base_url, href)) if href else ""

    @staticmethod
    def _text(element) -> str:
        if not element:
            return ""
        return WebScraper._text_value(element.get_text(" ", strip=True))

    @staticmethod
    def _text_value(value) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return ""
        return " ".join(str(value).split())

    @staticmethod
    def _join_text(parts) -> str:
        return " | ".join(part for part in (WebScraper._text_value(p) for p in parts) if part)

    @staticmethod
    def _platform_from_url(url: str) -> str:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if "linkedin.com" in host:
            return "linkedin"
        if host in {"x.com", "twitter.com"}:
            return "twitter"
        if "facebook.com" in host:
            return "facebook"
        if "instagram.com" in host:
            return "instagram"
        if "tiktok.com" in host:
            return "tiktok"
        if "youtube.com" in host or "youtu.be" in host:
            return "youtube"
        return "web"
    
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
