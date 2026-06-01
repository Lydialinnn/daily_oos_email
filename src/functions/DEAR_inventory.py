import requests
import math
import time
import logging
from tqdm import tqdm  # Use standard tqdm, NOT notebook
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_log, after_log

logger = logging.getLogger(__name__)

# Configure Tenacity logger
tenacity_logger = logging.getLogger("tenacity")
tenacity_logger.setLevel(logging.INFO)

def getList(api_url, headers, content_key, search_keys=None, most_recent=False, limit_max_pages=20, mute_print=False):
    """
    Cloud-optimized DEAR list fetcher using Session, Tenacity retries, and strict timeouts.
    """
    if search_keys is None:
        search_keys = {}
        
    limit_n = 100
    response_js = []
    
    # Use a Session for connection pooling (much faster and more stable in the cloud)
    session = requests.Session()
    session.headers.update(headers)

    # 1. Define the robust retry logic matching your CircleK pattern
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        before=before_log(tenacity_logger, logging.INFO),
        after=after_log(tenacity_logger, logging.WARNING)
    )
    def _fetch_page(page_num):
        params = {'Page': page_num, 'Limit': limit_n}
        params.update(search_keys)
        # CRITICAL: 30 second timeout prevents infinite hangs
        response = session.get(api_url, params=params, timeout=30)
        
        # If rate limited by DEAR (HTTP 429), force a specific exception or sleep
        if response.status_code == 429:
            logger.warning("DEAR Rate Limit Hit (429). Forcing retry...")
            response.raise_for_status() 

        response.raise_for_status()
        return response.json()

    # 2. Get the first page to determine total pages
    if not mute_print:
        logger.info(f"Initializing DEAR fetch for {api_url}...")
        
    try:
        first_page_data = _fetch_page(1)
    except Exception as e:
        logger.error(f"Failed to fetch initial page from DEAR: {e}")
        raise

    page_total = math.ceil(first_page_data.get('Total', 0) / limit_n)
    
    if limit_max_pages == 0:
        most_recent = False

    if page_total == 0:
        if not mute_print:
            logger.info("DEAR returned 0 records.")
        return response_js

    # 3. Determine the range of pages to fetch
    if page_total == 1:
        page_range = [1]
        response_js.extend(first_page_data.get(content_key, []))
        if not mute_print:
            logger.info("DEAR has 1 page. Fetch complete.")
        return response_js
    else:
        if most_recent:
            start_page = max(1, page_total - limit_max_pages + 1)
            page_range = range(start_page, page_total + 1)
        else:
            end_page = page_total if limit_max_pages == 0 else min(limit_max_pages, page_total)
            page_range = range(1, end_page + 1)

    if not mute_print:
        logger.info(f"DEAR Total Pages: {page_total}. Fetching range: {page_range[0]} to {page_range[-1]}")

    # Rate limit buffer
    sleep_sec = 1.05 if page_total > 45 else 0

    # 4. Fetch the remaining pages using the robust retry wrapper
    iterator = page_range if mute_print else tqdm(page_range, desc="Fetching DEAR pages")
    
    for i in iterator:
        if i == 1:
            # We already have page 1 data, skip re-fetching if it's in the range
            if 1 in page_range and not response_js:
                response_js.extend(first_page_data.get(content_key, []))
            continue
            
        time.sleep(sleep_sec)
        
        try:
            page_data = _fetch_page(i)
            response_js.extend(page_data.get(content_key, []))
        except Exception as e:
            logger.error(f"Failed to fetch DEAR page {i} after all retries: {e}")
            # Depending on your business logic, you might want to `raise` here to fail the job, 
            # or `continue` to grab what you can. Raising is usually safer for inventory.
            raise

    if not mute_print:
        logger.info("DEAR Complete List Loading Finished.")

    return response_js