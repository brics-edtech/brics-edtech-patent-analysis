"""
Google Patents Scraper Pipeline (Improved Version)
- Includes modular parsing methods for metadata, classifications, abstract, description, claims, and citations
- Handles the /en endpoint fallback
- Uses lxml parser and full error handling
- Uses a fallback method to extract a patent identifier if the expected "id" key is missing
"""

import json
import requests
import logging
import time
from typing import Dict, List, Optional
from bs4 import BeautifulSoup
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from tqdm import tqdm

# Configure logging: logging to both file and console.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('patent_scraper.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)


class PatentScraper:
    """Complete patent scraper with improved parsing methods."""

    def __init__(self):
        self.session = self._create_session()
        self.headers = {
            'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'),
            'Accept-Language': 'en-US,en;q=0.5',
        }

    def _create_session(self) -> requests.Session:
        """Create HTTP session with retry logic."""
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        return session

    def _convert_id_to_url_format(self, original_id: str) -> str:
        """Convert the patent identifier into a URL–friendly format (e.g. by removing hyphens)."""
        # Remove hyphens and extra spaces if necessary.
        return original_id.replace('-', '').strip()

    def _get_page_html(self, original_id: str) -> Optional[BeautifulSoup]:
        """Fetch the patent page. Try /en version first; if that fails, fallback to base URL."""
        url_id = self._convert_id_to_url_format(original_id)
        base_url = f"https://patents.google.com/patent/{url_id}"
        en_url = f"{base_url}/en"
        # Try the English version first
        try:
            response = self.session.get(en_url, headers=self.headers, timeout=15)
            response.raise_for_status()
            logging.info(f"Fetched English version for {original_id}")
            return BeautifulSoup(response.content, 'lxml')
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                logging.info(f"English version not found for {original_id}, trying base URL")
            else:
                logging.warning(f"Error fetching English version for {original_id}: {e}")
        except Exception as e:
            logging.warning(f"Connection issue for {original_id} (/en): {e}")

        # Fallback to the base URL
        try:
            response = self.session.get(base_url, headers=self.headers, timeout=15)
            response.raise_for_status()
            logging.info(f"Fetched base URL for {original_id}")
            return BeautifulSoup(response.content, 'lxml')
        except Exception as e:
            logging.error(f"Failed to fetch {original_id}: {e}")
            return None

    def _parse_metadata(self, soup: BeautifulSoup) -> Dict:
        """Parse patent metadata using JSON-LD and fallback on meta tags."""
        metadata = {}
        try:
            # Try using JSON-LD first if available
            script_tag = soup.find("script", {"type": "application/ld+json"})
            if script_tag:
                try:
                    data = json.loads(script_tag.string)
                    metadata['title'] = data.get('name', '').strip()
                    metadata['publication_date'] = data.get('datePublished', '').strip()
                    metadata['abstract'] = data.get('description', '').strip()
                except json.JSONDecodeError:
                    logging.warning("JSON-LD parsing failed.")
            # Fallback: use meta tags from head if necessary
            if not metadata.get('title'):
                meta_title = soup.find("meta", {"name": "DC.title"})
                if meta_title and meta_title.get("content"):
                    metadata['title'] = meta_title["content"].strip()

            pub_date_tag = soup.find("meta", {"itemprop": "publicationDate"})
            if pub_date_tag and pub_date_tag.get("content"):
                metadata['publication_date'] = pub_date_tag["content"].strip()

            # Retrieve inventor names (if available)
            inventors = [tag.get_text(strip=True) for tag in soup.find_all(attrs={"itemprop": "inventor"})]
            metadata['inventors'] = inventors

        except Exception as e:
            logging.error(f"Error parsing metadata: {e}")
        return metadata

    def _parse_classifications(self, soup: BeautifulSoup) -> Dict:
        """Extract classification codes and descriptions from the patent page HTML."""
        classifications = {"numbers": [], "descriptions": []}
        try:
            # Locate the heading with 'Classifications'
            h2_element = soup.find("h2", string=lambda t: t and "Classifications" in t)
            if not h2_element:
                logging.info("No Classifications heading found.")
                return classifications

            # Get the parent section of the h2 element.
            section = h2_element.find_parent("section")
            if not section:
                logging.info("No parent section for Classifications found.")
                return classifications

            # Find all list items that have the classification information.
            classification_items = section.find_all("li", attrs={"itemprop": "classifications"})
            if not classification_items:
                logging.info("No classification items found in the Classifications section.")
                return classifications

            seen_codes = set()
            for item in classification_items:
                code_tag = item.find("span", attrs={"itemprop": "Code"})
                desc_tag = item.find("span", attrs={"itemprop": "Description"})
                if code_tag:
                    code = code_tag.get_text(strip=True)
                    if code and code not in seen_codes:
                        classifications["numbers"].append(code)
                        seen_codes.add(code)
                if desc_tag:
                    description = desc_tag.get_text(strip=True)
                    if description:
                        classifications["descriptions"].append(description)
                        
        except Exception as e:
            logging.error(f"Error parsing classifications: {e}")
        return classifications

    def _parse_abstract(self, soup: BeautifulSoup) -> str:
        """Extract the patent abstract from the section with itemprop 'abstract'."""
        abstract_text = ""
        try:
            section = soup.find("section", {"itemprop": "abstract"})
            if section:
                content = section.find(attrs={"itemprop": "content"})
                if content:
                    abstract_text = content.get_text(separator="\n", strip=True)
                else:
                    abstract_text = section.get_text(separator="\n", strip=True)
        except Exception as e:
            logging.error(f"Error parsing abstract: {e}")
        return abstract_text

    def _parse_description(self, soup: BeautifulSoup) -> str:
        """Extract the patent description from the section with itemprop 'description'."""
        description_text = ""
        try:
            section = soup.find("section", {"itemprop": "description"})
            if section:
                content = section.find(attrs={"itemprop": "content"})
                if content:
                    description_text = content.get_text(separator="\n", strip=True)
                else:
                    description_text = section.get_text(separator="\n", strip=True)
        except Exception as e:
            logging.error(f"Error parsing description: {e}")
        return description_text

    def _parse_claims(self, soup: BeautifulSoup) -> List[str]:
        """Extract the claims from the section with itemprop 'claims'."""
        claims = []
        try:
            section = soup.find("section", {"itemprop": "claims"})
            if section:
                # Try to extract each individual <claim> tag first.
                for claim in section.find_all("claim"):
                    text = claim.get_text(separator=" ", strip=True)
                    if text:
                        claims.append(text)
                # If no <claim> tags exist, look for paragraphs.
                if not claims:
                    for p in section.find_all("p"):
                        text = p.get_text(separator=" ", strip=True)
                        if text:
                            claims.append(text)
        except Exception as e:
            logging.error(f"Error parsing claims: {e}")
        return claims

    def _parse_citations(self, soup: BeautifulSoup) -> Dict:
        """Extract citation data (forward and backward)."""
        citations = {"forward": [], "backward": []}
        try:
            # Forward citations: look for a section with heading including "Cited By"
            cited_by_section = None
            for sec in soup.find_all("section"):
                h2 = sec.find("h2")
                if h2 and "Cited By" in h2.get_text():
                    cited_by_section = sec
                    break
            if cited_by_section:
                for tr in cited_by_section.find_all("tr"):
                    a = tr.find("a")
                    if a and a.get_text():
                        citations["forward"].append(a.get_text(strip=True))
            # Backward citations: look for a section with "Citations" (but not "Cited By")
            citations_section = None
            for sec in soup.find_all("section"):
                h2 = sec.find("h2")
                if h2 and "Citations" in h2.get_text() and "Cited By" not in h2.get_text():
                    citations_section = sec
                    break
            if citations_section:
                for tr in citations_section.find_all("tr"):
                    a = tr.find("a")
                    if a and a.get_text():
                        citations["backward"].append(a.get_text(strip=True))
        except Exception as e:
            logging.error(f"Error parsing citations: {e}")
        return citations

    def scrape_patent(self, original_id: str) -> Optional[Dict]:
        """Main method to scrape a patent using its original id and call all parser functions."""
        start_time = time.time()
        result = None
        try:
            soup = self._get_page_html(original_id)
            if not soup:
                return None

            # Parse each component.
            metadata = self._parse_metadata(soup)
            classifications = self._parse_classifications(soup)
            abstract = self._parse_abstract(soup)
            description = self._parse_description(soup)
            claims = self._parse_claims(soup)
            citations = self._parse_citations(soup)

            result = {
                'id': original_id,
                'application_number': self._convert_id_to_url_format(original_id),
                'country': original_id[:2] if len(original_id) >= 2 else '',
                **metadata,
                'classification_numbers': classifications.get('numbers', []),
                'classification_descriptions': classifications.get('descriptions', []),
                'abstract': abstract,
                'description': description,
                'claims': " ".join(claims) if claims else "",
                'forward_cites': citations.get('forward', []),
                'backward_cites': citations.get('backward', []),
                'all_cites': citations.get('forward', []) + citations.get('backward', []),
                'processing_time': time.time() - start_time
            }
        except Exception as e:
            logging.error(f"Error processing {original_id}: {e}")
        return result


def load_patent_data(file_path: str) -> List[Dict]:
    """Load and validate input patent data from a JSON file."""
    try:
        with open(file_path, 'r', encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("Input data should be a list of patent objects.")
        return data
    except Exception as e:
        logging.error(f"Error loading input file: {e}")
        raise


def process_patents(input_file: str, output_file: str):
    """
    Main processing pipeline:
      - Loads patent data from JSON
      - For each record, determines a valid patent identifier (using "id" or "patent")
      - Scrapes the patent data from Google Patents and merges with the original record
      - Saves the successfully processed patents and logs any failures.
    """
    scraper = PatentScraper()
    patents = load_patent_data(input_file)
    
    if not patents:
        logging.error("No patent data found in the input file.")
        return

    results = []
    failed_patents = []

    with tqdm(total=len(patents), desc='Scraping Patents', unit='patent') as pbar:
        for patent in patents:
            # Try to retrieve the patent identifier from one of the available fields.
            original_id = patent.get('id') or patent.get('patent')
            if not original_id:
                error_msg = f"Missing identifier in patent record: {patent}"
                logging.error(error_msg)
                failed_patents.append({'error': 'Missing ID', 'data': patent})
                pbar.update(1)
                continue

            try:
                scraped_data = scraper.scrape_patent(original_id)
                if scraped_data:
                    # Merge original record with scraped data.
                    merged_data = {**patent, **scraped_data}
                    results.append(merged_data)
                else:
                    failed_patents.append(original_id)
            except Exception as e:
                logging.error(f"Critical error processing {original_id}: {str(e)}")
                failed_patents.append(original_id)
            pbar.update(1)
            time.sleep(1)  # Rate limiting

    # Save the successfully scraped patents to the output file.
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Error saving the output file: {e}")

    logging.info("Scraping complete!")
    logging.info(f"Successfully processed: {len(results)}/{len(patents)}")
    logging.info(f"Failed patents: {len(failed_patents)}")

    # Save failures for further analysis.
    if failed_patents:
        try:
            with open('failed_patents.json', 'w', encoding='utf-8') as f:
                json.dump(failed_patents, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"Error saving failed patents file: {e}")

    if not results:
        logging.error("No patents were processed. Please check your input file structure and identifiers.")


if __name__ == '__main__':
    process_patents(
        input_file='json_output_filtered.json',
        output_file='patents_with_description.json'
    )
