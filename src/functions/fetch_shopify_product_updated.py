import requests
import time
import json
import random
import logging 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_all_product_inventory(api_shop, api_version, headers, logger):
    """
    Fetches all products and their variant inventory quantities from Shopify.
    Handles pagination and retries failed requests.
    """
    # Request exactly 250 items, but ONLY ask for title, status, and variants
    fields_param = "title,status,variants"
    product_base_url = f"https://{api_shop}.myshopify.com/admin/api/{api_version}/products.json?limit=250&fields={fields_param}"
    all_products_data = []
    next_page_url = product_base_url
    
    max_retries = 5  
    base_backoff_seconds = 2  
    request_timeout = 30 

    # 2. CRITICAL: Open a persistent Session to bypass Cloud Run SSL handshake latency
    session = requests.Session()
    session.headers.update(headers)

    while next_page_url:
        current_page_data = None
        for attempt in range(max_retries):
            try:
                logger.info(f"Fetching page: {next_page_url.split('page_info=')[-1][:20]}... (Attempt {attempt + 1}/{max_retries})")
                
                # 3. Use session.get() instead of requests.get()
                response = session.get(next_page_url, timeout=request_timeout)
                
                if response.status_code == 429: # Too Many Requests
                    retry_after = int(response.headers.get("Retry-After", base_backoff_seconds * (2 ** attempt)))
                    logger.warning(f"Rate limited (429). Retrying after {retry_after} seconds.")
                    time.sleep(retry_after)

                response.raise_for_status() 
                
                current_page_data = response.json()
                break 

            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    retry_after = int(e.response.headers.get("Retry-After", base_backoff_seconds * (2 ** attempt)))
                    if attempt < max_retries - 1:
                        time.sleep(retry_after)
                        continue 
                    else:
                        logger.error(f"Max retries reached for rate limit on {next_page_url}.")
                elif 500 <= e.response.status_code < 600:
                    logger.warning(f"Server error ({e.response.status_code}) fetching {next_page_url}. Attempt {attempt + 1}/{max_retries}. Error: {e}")
                    if attempt < max_retries - 1:
                        delay = (base_backoff_seconds * (2 ** attempt)) + random.uniform(0, 1)
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"Max retries reached for server error on {next_page_url}.")
                else:
                    logger.error(f"Non-retryable HTTP error fetching {next_page_url}: {e}")
                    next_page_url = None 
                    break 

            except requests.exceptions.RequestException as e: 
                if attempt < max_retries - 1:
                    delay = (base_backoff_seconds * (2 ** attempt)) + random.uniform(0, 1)
                    logger.info(f"Retrying in {delay:.2f} seconds...")
                    time.sleep(delay)
                    continue 
                else:
                    logger.error(f"Max retries reached for RequestException on {next_page_url}.")
            
            except json.JSONDecodeError as e:
                if attempt < max_retries - 1:
                    delay = (base_backoff_seconds * (2 ** attempt)) + random.uniform(0, 1)
                    logger.info(f"Retrying in {delay:.2f} seconds...")
                    time.sleep(delay)
                    continue 
                else:
                    logger.error(f"Max retries reached for JSONDecodeError on {next_page_url}.")

            except Exception as e: 
                logger.error(f"An unexpected error occurred while fetching page {next_page_url}: {e}. Attempt {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    delay = (base_backoff_seconds * (2 ** attempt)) + random.uniform(0, 1)
                    logger.info(f"Retrying in {delay:.2f} seconds...")
                    time.sleep(delay)
                    continue 
                else:
                    logger.error(f"Max retries reached for unexpected error on {next_page_url}.")
            
            if attempt == max_retries - 1:
                 logger.error(f"All {max_retries} retries failed for {next_page_url}.")
                 next_page_url = None 

        if current_page_data:
            all_products_data.extend(current_page_data.get('products', []))
            
            link_header = response.headers.get('Link') 
            next_page_url = None 
            if link_header:
                links = link_header.split(',')
                for link in links:
                    if 'rel="next"' in link:
                        next_page_url = link.split(';')[0].strip('<> ')
                        break
        else:
            break 

    logger.info(f"Total products inv retrieved: {len(all_products_data)}")
    print(f"Total products inv retrieved: {len(all_products_data)}") 
    return all_products_data



def extract_inventory_data(products):
    """
    Extracts product title, variant SKU, and inventory quantity.
    """
    inventory_summary = []
    for product in products:
        product_title = product.get('title')
        sku_status = product.get('status')
        for variant in product.get('variants', []):
            sku = variant.get('sku')
            variant_id = variant.get('id')
            inventory_item_id = variant.get('inventory_item_id') # NEW: Needed for accurate inventory
            # inventory_qty = variant.get('inventory_quantity') # inventory_quantity field is deprecated and unreliable
            legacy_qty = variant.get('inventory_quantity')
            inventory_summary.append({
                'product_title': product_title,
                'variant_sku': sku,
                'variant_id': variant_id,
                'inventory_item_id': inventory_item_id,
                'sku_status':sku_status,
                'legacy_inventory_quantity': legacy_qty # Kept for reference, but shouldn't be relied on
            })
    return inventory_summary


def get_inventory_levels(api_shop, api_version, headers, inventory_item_ids, logger):
    """
    Fetches real-time inventory levels for a list of inventory_item_ids in batches of 50.
    """
    all_levels = []
    chunk_size = 50
    
    # 1. Open a persistent Session to bypass Cloud Run SSL handshake latency
    session = requests.Session()
    session.headers.update(headers)
    
    total_chunks = (len(inventory_item_ids) + chunk_size - 1) // chunk_size
    logger.info(f"Splitting {len(inventory_item_ids)} items into {total_chunks} chunks...")

    for chunk_idx, i in enumerate(range(0, len(inventory_item_ids), chunk_size), 1):
        chunk = inventory_item_ids[i:i + chunk_size]
        ids_string = ",".join(map(str, [x for x in chunk if x]))
        
        if not ids_string:
            continue
            
        url = f"https://{api_shop}.myshopify.com/admin/api/{api_version}/inventory_levels.json?inventory_item_ids={ids_string}"
        
        # 2. Add visibility so you know the script hasn't frozen!
        if chunk_idx % 10 == 0:
            logger.info(f"Processing Shopify inventory chunk {chunk_idx}/{total_chunks}...")
        
        for attempt in range(5):
            try:
                # 3. Use session.get() instead of requests.get()
                response = session.get(url, timeout=30)
                
                if response.status_code == 429: # Rate limit
                    retry_after = int(response.headers.get("Retry-After", 2 ** attempt))
                    logger.warning(f"Chunk {chunk_idx}: Rate limit hit (429). Sleeping {retry_after}s...")
                    time.sleep(retry_after)
                    continue
                
                response.raise_for_status()
                all_levels.extend(response.json().get('inventory_levels', []))
                break # Success, break out of retry loop
                
            except Exception as e:
                logger.warning(f"Error fetching inventory levels chunk {chunk_idx}: {e}. Attempt {attempt + 1}/5")
                time.sleep(2 ** attempt)
                
    return all_levels