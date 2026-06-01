import os
import requests
import gspread # Use gspread for easy gid lookup
from google.oauth2 import service_account
from googleapiclient.errors import HttpError
from pathlib import Path
import logging
import pandas as pd
import csv
import random
import time
from google.oauth2 import service_account 
from googleapiclient.errors import HttpError as GoogleAPIHttpError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account 
from googleapiclient.errors import HttpError as GoogleAPIHttpError 
from google.auth.transport.requests import Request as GoogleAuthRequest 
import requests
from googleapiclient.errors import HttpError as GoogleAPIHttpError
import gspread # For gspread.exceptions
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest
from oauth2client.service_account import ServiceAccountCredentials
from .cloud_credentials import oauth2client_credentials


# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)  # get module logger

# --- Configuration ---
try:
    dir_path = Path(__file__).resolve().parent
except NameError:
    dir_path = Path.cwd() 


CONFIG_DIRECTORY = dir_path.parent / 'config' 
creds_path = CONFIG_DIRECTORY / 'Google_project.json'

output_path = dir_path.parent / 'input' 
os.makedirs(output_path, exist_ok=True)

# --- Retry Logic ---
MAX_RETRIES = 3  
INITIAL_RETRY_DELAY_SECONDS = 5  
BACKOFF_FACTOR = 2  
JITTER_SECONDS = 1 


# def download_tab_as_csv(spreadsheet_id, tab_name, output_path, column_range=None):
#     """
#     Authenticates with Google, downloads a specific tab from a Google Sheet using its ID,
#     and saves it as a CSV file locally. If any error occurs, or if the retrieved data
#     is empty, it prints a message, does not save a file, and returns False,
#     allowing the script to continue.

#     Args:
#         spreadsheet_id (str): The ID of the Google Sheet you want to access.
#         tab_name (str): The name of the specific tab (worksheet) within the sheet.
#         output_path (str): The local file path where the CSV will be saved (e.g., './my_data.csv').
#         column_range (str, optional): The range of columns to download, in A1 notation (e.g., 'A:C').
#                                   If None, downloads all columns. Defaults to None.

#     Returns:
#         bool: True if a non-empty CSV was successfully downloaded and saved, False otherwise.
#     """

#     try:
#         scope = [
#             'https://spreadsheets.google.com/feeds',
#             'https://www.googleapis.com/auth/spreadsheets',
#             'https://www.googleapis.com/auth/drive.file',
#             'https://www.googleapis.com/auth/drive'
#         ]
#         creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
#         client = gspread.authorize(creds)
#         print("Successfully authenticated with Google.")

#         sheet_obj = client.open_by_key(spreadsheet_id)
#         worksheet = sheet_obj.worksheet(tab_name)
#         print(f"Successfully opened tab '{tab_name}' from sheet with ID '{spreadsheet_id}'.")

#         df = None
#         if column_range:
#             print(f"Retrieving data from specified column range '{column_range}'...")
#             values = worksheet.get_values(column_range)
#             if not values:
#                 print(f"Warning: The range '{column_range}' in tab '{tab_name}' is empty or could not be read. No data to save.")
#                 return False
            
#             header = values[0]
#             data_rows = values[1:]
#             if not data_rows:
#                  print(f"Warning: The range '{column_range}' in tab '{tab_name}' contains only a header or is effectively empty. No data rows to save.")
#                  df = pd.DataFrame(columns=header if header else [])
#             else:
#                 df = pd.DataFrame(data_rows, columns=header)
#         else:
#             print(f"Retrieving all data from the tab '{tab_name}'...")
#             data = worksheet.get_all_records(empty_value='', head=1, default_blank='')
#             if not data:
#                 print(f"Warning: The tab '{tab_name}' is empty or could not be read properly. No data to save.")
#                 df = pd.DataFrame()
#             else:
#                 df = pd.DataFrame(data)

#         # --- Crucial check: If DataFrame is empty, do not save and return False ---
#         if df.empty:
#             print(f"Retrieved data resulted in an empty DataFrame from tab '{tab_name}'. CSV file will not be saved.")
#             return False

#         print(f"Retrieved {len(df)} rows of non-empty data.")

#         # --- 4. Save the DataFrame to a CSV file (only if df is not empty) ---
#         output_dir = os.path.dirname(output_path)
#         if output_dir and not os.path.exists(output_dir):
#             os.makedirs(output_dir, exist_ok=True)
#             print(f"Created output directory: '{output_dir}'")

#         df.to_csv(output_path, index=False)
#         print(f"Data successfully saved to '{output_path}'")
#         logger.info(f"Data successfully saved to '{output_path}'")
#         return True

#     except gspread.exceptions.SpreadsheetNotFound:
#         print(f"Error: The sheet with ID '{spreadsheet_id}' was not found. Download failed.")
#         logger.error("downloading the latest sku list failed, use the local version")
#         return False
#     except gspread.exceptions.WorksheetNotFound:
#         print(f"Error: The tab named '{tab_name}' was not found in sheet ID '{spreadsheet_id}'. Download failed.")
#         logger.error("downloading the latest sku list failed, use the local version")
#         return False
#     except FileNotFoundError as fnf_error: # Catches issues like bad output_path if not caught by os.makedirs
#         print(f"A FileNotFoundError occurred: {fnf_error}. This might be related to the output path or other file operations. Download/Save failed.")
#         logger.error("downloading the latest sku list failed, use the local version")
#         return False
#     except NameError as ne:
#         print(f"A NameError occurred during execution: {ne}. Please check variable definitions. Download/Save failed.")
#         logger.error("downloading the latest sku list failed, use the local version")
#         return False
#     except Exception as e:
#         print(f"An unexpected error occurred during download/saving: {e}. Download/Save failed.")
#         logger.error("downloading the latest sku list failed, use the local version")
#         return False





# --- HELPER FUNCTION TO REPLACE gspread.utils.numeric_to_a1 ---
def _numeric_to_a1_compat(n):
    """Converts a 1-based column index to A1 notation (e.g., 1 -> 'A', 27 -> 'AA')."""
    if not isinstance(n, int) or n < 1:
        raise ValueError("Column index must be a positive integer.")
    
    div = n
    string = ""
    while div > 0:
        module = (div - 1) % 26
        string = chr(65 + module) + string
        div = (div - 1) // 26
    return string


def download_tab_as_csv(spreadsheet_id, tab_name, output_path, column_range=None, chunk_size=1500):
    """
    Authenticates with Google, downloads a specific tab from a Google Sheet in chunks,
    and saves it as a CSV file locally. Implements a retry mechanism for network errors.
    """
    for attempt in range(MAX_RETRIES):
        try:
            scope = [
                'https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive.file',
                'https://www.googleapis.com/auth/drive'
            ]
            creds = oauth2client_credentials(scope)
            client = gspread.authorize(creds)
            
            print("Successfully authenticated with Google.")
            sheet_obj = client.open_by_key(spreadsheet_id)
            worksheet = sheet_obj.worksheet(tab_name)
            print(f"Successfully opened tab '{tab_name}' from sheet ID '{spreadsheet_id}'.")

            print("Determining data boundaries...")
            
            header_values = worksheet.row_values(1)
            if not header_values:
                print(f"Warning: Header row is empty in tab '{tab_name}'. Cannot proceed.")
                return False
            # *** FIX: Use our compatible helper function here ***
            last_col_letter = _numeric_to_a1_compat(len(header_values))

            first_col_values = worksheet.col_values(1)
            last_data_row = len(first_col_values)
            
            if last_data_row <= 1:
                print(f"Warning: No data found after the header row in tab '{tab_name}'.")
                return False

            print(f"Found data up to row {last_data_row} and column {last_col_letter}.")
            
            start_col_letter = 'A'
            if column_range:
                start_col_letter, end_col_letter = column_range.split(':')
            else:
                end_col_letter = last_col_letter

            header = header_values
            all_data_rows = []
            
            for start_row in range(2, last_data_row + 1, chunk_size):
                end_row = min(start_row + chunk_size - 1, last_data_row)
                
                print(f"Fetching rows {start_row} to {end_row}...")
                chunk_range = f'{start_col_letter}{start_row}:{end_col_letter}{end_row}'
                chunk_values = worksheet.get(chunk_range, value_render_option='FORMATTED_VALUE')
                
                if chunk_values:
                    all_data_rows.extend(chunk_values)

            if not all_data_rows:
                print(f"Warning: No data rows could be retrieved. DataFrame will be empty.")
                df = pd.DataFrame(columns=header)
            else:
                df = pd.DataFrame(all_data_rows)
                df.columns = header[:df.shape[1]]


            if df.empty:
                print(f"Retrieved data resulted in an empty DataFrame. CSV file will not be saved.")
                return False

            print(f"Retrieved a total of {len(df)} rows of data.")
            logger.info(f"Retrieved a total of {len(df)} rows of data.")

            output_dir = os.path.dirname(output_path)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)
                print(f"Created output directory: '{output_dir}'")

            df.to_csv(output_path, index=False)
            print(f"Data successfully saved to '{output_path}'")
            logger.info(f"Data successfully saved to '{output_path}'")
            return True

        # --- Exception Handling ---
        except (gspread.exceptions.SpreadsheetNotFound, gspread.exceptions.WorksheetNotFound) as fatal_err:
            print(f"Fatal Error: {fatal_err}. No further retries will be attempted.")
            return False
        except gspread.exceptions.APIError as api_err:
            print(f"Attempt {attempt + 1}/{MAX_RETRIES} failed with API Error: {api_err}")
            if attempt + 1 == MAX_RETRIES:
                print("Max retries reached. Download failed.")
                return False
            sleep_time = (INITIAL_RETRY_DELAY_SECONDS * (BACKOFF_FACTOR ** attempt)) + (random.uniform(0, JITTER_SECONDS))
            print(f"Retrying in {sleep_time:.2f} seconds...")
            time.sleep(sleep_time)
        except Exception as e:
            print(f"An unexpected error occurred: {e}. Download failed.")
            return False

    return False
