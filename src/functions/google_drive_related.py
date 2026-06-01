from googleapiclient.discovery import build
from google.oauth2 import service_account
import io
import os
import re
import pandas as pd
from typing import List, Union
from googleapiclient.http import MediaIoBaseDownload
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.http import MediaFileUpload
from pathlib import Path
from datetime import date
import logging
import sys
from googleapiclient.errors import HttpError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle
import datetime
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import numpy as np
import time
import httplib2
from .cloud_credentials import google_credentials

import socket # Import socket to access TimeoutError


SCOPES = ['https://www.googleapis.com/auth/drive']

dir_path = Path(__file__).resolve().parent
current_directory = dir_path.parent / 'config' 
SERVICE_ACCOUNT_FILE = os.path.join(current_directory, 'Google_project.json')

credentials = google_credentials(SCOPES)

drive_service = build('drive', 'v3', credentials=credentials)
logger = logging.getLogger(__name__)  # get module logger



# single file upload with order_date specified:
def upload_to_drive_with_date(local_path, min_order_date, max_order_date, drive_folder_id=None):
    # 1. Get the original filename from the local path, e.g., 'my_report.csv'
    original_name = os.path.basename(local_path)
    # 2. Split the original name into its base and extension: 'my_report.csv' becomes ('my_report', '.csv')
    name_without_extension, extension = os.path.splitext(original_name)
    # 3. Create the NEW filename that will be used on Google Drive
    file_name_on_drive = f"{name_without_extension}_{min_order_date}_{max_order_date}{extension}"
    # 4. Prepare the file for upload
    media = MediaFileUpload(local_path, resumable=True)
    # 5. Set up the metadata for the file on Drive
    file_metadata = {'name': file_name_on_drive} # <-- This is where the new name is used!
    if drive_folder_id:
        file_metadata['parents'] = [drive_folder_id]
    # 6. Perform the actual upload to Google Drive
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, name, webViewLink'
    ).execute()

    print(f"✅ Uploaded: {file['name']} (ID: {file['id']})")
    logger.info(f" ✅ Uploaded: {file['name']} (ID: {file['id']})")






# single file upload and overwrite existing file with the same name:
def upload_or_overwrite_to_drive(local_path, drive_folder_id=None):
    """
    Uploads a file to Google Drive, overwriting it if a file with the same name
    already exists in the specified folder, or creating a new one otherwise.

    Includes robust error handling and retries for network-related issues,
    including timeouts.

    Args:
        local_path (str): The full path to the local file to upload.
        drive_folder_id (str, optional): The ID of the Google Drive folder
                                         where the file should be uploaded.
                                         If None, the file is uploaded to the
                                         root folder. Defaults to None.
    """
    file_name = os.path.basename(local_path)
    file_size_bytes = os.path.getsize(local_path) 

    CHUNK_SIZE = 2 * 1024 * 1024 # 2MB chunk size

    media = MediaFileUpload(local_path, resumable=True, chunksize=CHUNK_SIZE)

    @retry(
        stop=stop_after_attempt(5), 
        wait=wait_exponential(multiplier=1, min=4, max=60), # Wait 4s, then 8s, 16s, etc., up to a max of 60s between retries.

        retry=retry_if_exception_type((BrokenPipeError, ConnectionError, HttpError, socket.timeout))
    )
    def _execute_upload(request):
        """
        Internal helper function to execute the Google Drive API upload request.
        This function is wrapped by the tenacity retry decorator.
        """
        response = None

        while response is None:
            status, response = request.next_chunk()
        return response

    try:
        query = f"name='{file_name}'"
        if drive_folder_id:
            query += f" and '{drive_folder_id}' in parents"
        query += " and trashed=false" 

        logger.info(f"Searching for existing file: '{file_name}' in folder '{drive_folder_id}'...")
        results = drive_service.files().list(q=query, fields='files(id)').execute()
        files = results.get('files', [])

        if files:
            file_id_to_update = files[0]['id']
            logger.info(f"Existing file found. Attempting to overwrite: {file_name} (ID: {file_id_to_update}). File size: {file_size_bytes / (1024*1024):.2f} MB")

            request = drive_service.files().update(
                fileId=file_id_to_update,
                media_body=media
            )

            updated_file = _execute_upload(request)
            print(f"✅ Overwritten: {file_name} (ID: {updated_file.get('id')})")
            logger.info(f"✅ Overwritten: {file_name} (ID: {updated_file.get('id')})")
        else:
            file_metadata = {'name': file_name}
            if drive_folder_id:
                file_metadata['parents'] = [drive_folder_id]

            logger.info(f"No existing file found. Attempting to upload new file: {file_name}. File size: {file_size_bytes / (1024*1024):.2f} MB")

            request = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, webViewLink' 
            )

            new_file = _execute_upload(request)
            print(f"✅ Uploaded: {new_file['name']} (ID: {new_file['id']})")
            logger.info(f"✅ Uploaded: {new_file['name']} (ID: {new_file['id']})")

    except HttpError as e:
        logger.error(f"Google Drive API error for {file_name}: {e}")
        if e.resp.status == 403:
            logger.error("Permission denied or quota exceeded for Google Drive API.")
        elif e.resp.status == 404:
            logger.error("File or folder not found for Google Drive API operation.")
        raise 
    except (BrokenPipeError, ConnectionError, socket.timeout) as e:
        logger.error(f"Network error during upload of {file_name}: {e}")
        raise 
    except Exception as e:
        logger.error(f"An unexpected error occurred during upload of {file_name}: {e}", exc_info=True)
        raise 










# retrive data from google sheet tab:
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

# def get_sheet_data(spreadsheet_id, tab_name, columns_to_retrieve=None, use_col_indices=False,header_row=1,cell_to_retrieve=None):
#     """
#     Retrieves data from a Google Sheet tab or a specific cell.

#     If 'cell_to_retrieve' is specified (e.g., "A1", "C5"), it fetches only that cell's value and returns it as a string.
#     Otherwise, it fetches data for the specified columns (or all columns if 'columns_to_retrieve' is None) into a pandas DataFrame.

#     Args:
#         spreadsheet_id (str): The ID of the Google Sheet.
#         tab_name (str): The name of the tab to retrieve data from.
#         columns_to_retrieve (list, optional): A list of column names (if use_col_indices is False) or 0-based column indices (if use_col_indices is True) to retrieve. If None, all columns are retrieved.This parameter is IGNORED if 'cell_to_retrieve' is specified.
#         use_col_indices (bool, optional): If True, 'columns_to_retrieve' is treated as a list of column indices. Defaults to False. This parameter is IGNORED if 'cell_to_retrieve' is specified.
#         header_row (int, optional): The 1-based row number where the header is located. Data is read from the row after this. Defaults to 1. Ignored if 'cell_to_retrieve' is specified.
#         cell_to_retrieve (str, optional): A1 notation of a single cell to retrieve (e.g., "A1", "B5"). If provided, only this cell's value is returned (as a string).

#     Returns:
#         str or None: If 'cell_to_retrieve' is specified, returns the cell's value as a string. Returns None if the cell is empty, not found, or an error occurs during single cell retrieval.
#         pd.DataFrame: If 'cell_to_retrieve' is NOT specified, returns a pandas DataFrame containing the retrieved sheet data. Returns an empty DataFrame on error or if no data is found for sheet retrieval.
#     """
#     try:
#         sheet_service = build('sheets', 'v4', credentials=credentials)

#         # --- NEW SECTION for cell_to_retrieve ---
#         if cell_to_retrieve:
#             if not isinstance(cell_to_retrieve, str):
#                 logger.error(f"'cell_to_retrieve' must be a string (A1 notation), got: {type(cell_to_retrieve)}")
#                 print(f"Error: 'cell_to_retrieve' must be a string (A1 notation), got: {type(cell_to_retrieve)}")
#                 return None # Return None as per docstring for error in this mode

#             full_range = f"{tab_name}!{cell_to_retrieve}"
#             print(f"Attempting to retrieve single cell: '{full_range}' from spreadsheet '{spreadsheet_id}'")
#             try:
#                 result_cell = sheet_service.spreadsheets().values().get(
#                     spreadsheetId=spreadsheet_id,
#                     range=full_range
#                 ).execute()

#                 values_cell = result_cell.get('values', [])
#                 if values_cell and values_cell[0] and len(values_cell[0]) > 0:
#                     cell_value = values_cell[0][0]
#                     print(f"Retrieved cell value for '{full_range}': '{cell_value}'")
#                     return str(cell_value)  # Ensure it's returned as a string
#                 else:
#                     # logger.warning(f"No value found in cell '{full_range}' in sheet '{tab_name}' of spreadsheet '{spreadsheet_id}'.")
#                     print(f"Warning: No value found in cell '{full_range}'.")
#                     return None # Return None as per docstring for empty cell
#             except Exception as e_cell:
#                 # logger.error(f"Error retrieving single cell '{full_range}' from spreadsheet '{spreadsheet_id}': {e_cell}")
#                 print(f"Error retrieving single cell '{full_range}': {e_cell}")
#                 return None # Return None as per docstring for error in this mode
#         # --- END NEW SECTION ---

#         # Original code starts here, will only be reached if cell_to_retrieve is None
#         result = sheet_service.spreadsheets().values().get(
#             spreadsheetId=spreadsheet_id,
#             range=tab_name,
#             majorDimension="ROWS"
#         ).execute()

#         values = result.get('values', [])

#         if not values:
#             print(f"No data found in sheet '{tab_name}'.")
#             # logger.warning(f"No data found in sheet '{tab_name}'.")
#             return pd.DataFrame()
#         else:
#             # Check if header row is empty (e.g. sheet exists but has no data)
#             if not values[0]:
#                 print(f"Header row is empty or no data rows in sheet '{tab_name}'.")
#                 # logger.warning(f"Header row is empty or no data rows in sheet '{tab_name}'.")
#                 return pd.DataFrame() # Return empty DataFrame if header is missing/empty

#             header = values[0]
#             data = values[1:]

#             if columns_to_retrieve is None:
#                 df = pd.DataFrame(data, columns=header)
#                 return df
#             elif use_col_indices:
#                 # Treat columns_to_retrieve as 0-based indices
#                 # Original filtering ensures indices are within current header length
#                 column_indices = [idx for idx in columns_to_retrieve if idx < len(header)]

#                 if not column_indices:
#                     # If after filtering, no valid indices remain from the request
#                     print(f"Warning: No valid column indices from {columns_to_retrieve} found within header length {len(header)} for tab '{tab_name}'.")
#                     # logger.warning(f"No valid column indices from {columns_to_retrieve} found within header length {len(header)} for tab '{tab_name}'.")
#                     return pd.DataFrame()

#                 # Check if all originally specified indices were valid
#                 if len(column_indices) != len(columns_to_retrieve):
#                     invalid_indices = [idx for idx in columns_to_retrieve if idx >= len(header)]
#                     print(f"Warning: Not all specified column indices are valid (out of range): {invalid_indices} for header length {len(header)}.")
#                     # logger.warning(f"Not all specified column indices are valid (out of range): {invalid_indices} for header length {len(header)}.")

#                 extracted_data = []
#                 for row in data:
#                     # Ensure row has enough elements for each index in column_indices
#                     extracted_row = [row[i] if i < len(row) else None for i in column_indices]
#                     extracted_data.append(extracted_row)

#                 extracted_columns_names = [header[i] for i in column_indices] # Use actual header names for extracted columns
#                 df = pd.DataFrame(extracted_data, columns=extracted_columns_names)
#                 # Original print statement used the initial request, which might be confusing if some were invalid.
#                 # Let's print the actual columns extracted.
#                 print(f"Extracted data for columns (by index, actual): {extracted_columns_names} from tab '{tab_name}'.")
#                 # logger.info(f"Extracted data for columns (by index): {columns_to_retrieve} from tab '{tab_name}'.")
#                 return df
#             else:
#                 # Original logic: Treat columns_to_retrieve as header names
#                 # Original filtering ensures only names present in the header are used
#                 column_indices = [i for i, col_name in enumerate(header) if col_name in columns_to_retrieve]
                
#                 # Reconstruct the list of columns that were actually found, in the order they were requested (if found)
#                 # This makes the final DataFrame columns match the order of 'columns_to_retrieve' for found columns.
#                 found_columns_in_order = [col_name for col_name in columns_to_retrieve if col_name in header]

#                 if not found_columns_in_order: # If no requested columns are found in the header
#                     print(f"Warning: None of the specified columns {columns_to_retrieve} found in the header: {header} for tab '{tab_name}'.")
#                     # logger.warning(f"None of the specified columns {columns_to_retrieve} found in the header: {header} for tab '{tab_name}'.")
#                     return pd.DataFrame()

#                 if len(found_columns_in_order) != len(columns_to_retrieve):
#                     not_found_columns = [col for col in columns_to_retrieve if col not in header]
#                     print(f"Warning: Not all specified columns found in the header: {not_found_columns}. Found: {found_columns_in_order}")
#                     # logger.warning( f"Not all specified columns found in the header: {not_found_columns}. Found: {found_columns_in_order}")
                
#                 # Get the indices based on the found_columns_in_order to preserve order
#                 final_column_indices = [header.index(col_name) for col_name in found_columns_in_order]

#                 extracted_data = []
#                 for row in data:
#                     # Ensure row has enough elements for each index
#                     extracted_row = [row[i] if i < len(row) else None for i in final_column_indices]
#                     extracted_data.append(extracted_row)

#                 # Use found_columns_in_order for the DataFrame column names to maintain consistency and requested order
#                 df = pd.DataFrame(extracted_data, columns=found_columns_in_order)
#                 print(f"Extracted data for columns (by name, actual): {found_columns_in_order} from tab '{tab_name}'.")
#                 # logger.info(f"Extracted data for columns: {extracted_columns} from tab '{tab_name}'.")
#                 return df

#     except Exception as e:
#         # The return type here should also respect the 'cell_to_retrieve' mode
#         if cell_to_retrieve:
#             # If an exception happened before or during single cell retrieval mode,
#             # this general catch might apply if the specific one inside didn't.
#             print(f"Error in get_sheet_data (single cell mode) for spreadsheet '{spreadsheet_id}': {e}")
#             logger.error(f"Error in get_sheet_data (single cell mode) for spreadsheet '{spreadsheet_id}': {e}")
#             return None
#         else:
#             print(f"Error retrieving data from Google Sheet: {e}")
#             logger.error(f"Error retrieving data from Google Sheet: {e}")
#             return pd.DataFrame()
        



def get_sheet_data(spreadsheet_id, tab_name, columns_to_retrieve=None, use_col_indices=False, header_row=1, cell_to_retrieve=None):
    """
    Retrieves data from a Google Sheet tab or a specific cell.

    Args:
        spreadsheet_id (str): The ID of the Google Sheet.
        tab_name (str): The name of the tab to retrieve data from.
        columns_to_retrieve (list, optional): A list of column names or indices to retrieve. Ignored if 'cell_to_retrieve' is specified.
        use_col_indices (bool, optional): If True, 'columns_to_retrieve' is treated as 0-based indices. Ignored if 'cell_to_retrieve' is specified.
        header_row (int, optional): The 1-based row number where the header is located. Data is read from the row after this. Defaults to 1. Ignored if 'cell_to_retrieve' is specified.
        cell_to_retrieve (str, optional): A1 notation of a single cell to retrieve (e.g., "A1"). If provided, only this cell's value is returned.

    Returns:
        str or None: If 'cell_to_retrieve' is specified, returns the cell's value.
        pd.DataFrame: If 'cell_to_retrieve' is NOT specified, returns a pandas DataFrame.
    """
    try:
        sheet_service = build('sheets', 'v4', credentials=credentials)

        if cell_to_retrieve:
            # (No changes needed in this section, it remains as is)
            if not isinstance(cell_to_retrieve, str):
                # logger.error(f"'cell_to_retrieve' must be a string (A1 notation), got: {type(cell_to_retrieve)}")
                print(f"Error: 'cell_to_retrieve' must be a string (A1 notation), got: {type(cell_to_retrieve)}")
                return None
            full_range = f"{tab_name}!{cell_to_retrieve}"
            print(f"Attempting to retrieve single cell: '{full_range}' from spreadsheet '{spreadsheet_id}'")
            try:
                result_cell = sheet_service.spreadsheets().values().get(
                    spreadsheetId=spreadsheet_id, range=full_range
                ).execute()
                values_cell = result_cell.get('values', [])
                if values_cell and values_cell[0] and len(values_cell[0]) > 0:
                    return str(values_cell[0][0])
                else:
                    print(f"Warning: No value found in cell '{full_range}'.")
                    return None
            except Exception as e_cell:
                print(f"Error retrieving single cell '{full_range}': {e_cell}")
                return None
        
        # --- MODIFICATIONS START HERE ---

        if not isinstance(header_row, int) or header_row < 1:
            print(f"Error: 'header_row' must be a positive integer (1-based), but got: {header_row}")
            # logger.error(f"'header_row' must be a positive integer (1-based), but got: {header_row}")
            return pd.DataFrame()

        # Convert 1-based header_row to 0-based index for list access
        header_index = header_row - 1

        # Original code to fetch the entire sheet
        result = sheet_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=tab_name, majorDimension="ROWS"
        ).execute()

        values = result.get('values', [])

        if not values:
            print(f"No data found in sheet '{tab_name}'.")
            return pd.DataFrame()
        
        # Check if the specified header row is valid
        if header_index >= len(values):
            print(f"Warning: 'header_row' ({header_row}) is out of bounds. The sheet only has {len(values)} rows.")
            # logger.warning(f"Warning: 'header_row' ({header_row}) is out of bounds for sheet with {len(values)} rows.")
            return pd.DataFrame()

        # Set header and data based on the header_index
        header = values[header_index]
        data = values[header_index + 1:]

        # --- MODIFICATIONS END HERE ---
        
        # The rest of the function's logic remains exactly the same
        if not header:
            print(f"Header row {header_row} is empty or no data rows in sheet '{tab_name}'.")
            return pd.DataFrame()

        if columns_to_retrieve is None:
            df = pd.DataFrame(data, columns=header)
            return df
        elif use_col_indices:
            column_indices = [idx for idx in columns_to_retrieve if idx < len(header)]
            if not column_indices:
                print(f"Warning: No valid column indices from {columns_to_retrieve} found.")
                return pd.DataFrame()
            if len(column_indices) != len(columns_to_retrieve):
                invalid_indices = [idx for idx in columns_to_retrieve if idx >= len(header)]
                print(f"Warning: Invalid column indices (out of range): {invalid_indices}.")

            extracted_data = [[row[i] if i < len(row) else None for i in column_indices] for row in data]
            extracted_columns_names = [header[i] for i in column_indices]
            df = pd.DataFrame(extracted_data, columns=extracted_columns_names)
            print(f"Extracted data for columns (by index, actual): {extracted_columns_names} from tab '{tab_name}'.")
            return df
        else: # by name
            found_columns_in_order = [col_name for col_name in columns_to_retrieve if col_name in header]
            if not found_columns_in_order:
                print(f"Warning: None of the specified columns {columns_to_retrieve} found in the header.")
                return pd.DataFrame()
            if len(found_columns_in_order) != len(columns_to_retrieve):
                not_found_columns = [col for col in columns_to_retrieve if col not in header]
                print(f"Warning: Columns not found: {not_found_columns}.")
            
            final_column_indices = [header.index(col_name) for col_name in found_columns_in_order]
            extracted_data = [[row[i] if i < len(row) else None for i in final_column_indices] for row in data]
            df = pd.DataFrame(extracted_data, columns=found_columns_in_order)
            print(f"Extracted data for columns (by name, actual): {found_columns_in_order} from tab '{tab_name}'.")
            return df

    except Exception as e:
        if cell_to_retrieve:
            print(f"Error in get_sheet_data (single cell mode) for spreadsheet '{spreadsheet_id}': {e}")
            return None
        else:
            print(f"Error retrieving data from Google Sheet: {e}")
            return pd.DataFrame()




# append/ overwrite data to google sheet tab:
SCOPES_WRITE = ['https://www.googleapis.com/auth/spreadsheets']
# Helper function to convert 0-based column index to A1 column letter
def _column_index_to_letter(index):
    """Converts a 0-based column index to its corresponding A1 notation letter."""
    result = ""
    while index >= 0:
        result = chr(index % 26 + ord('A')) + result
        index = index // 26 - 1
    return result


def manage_google_sheet_data(spreadsheet_id, sheet_name, column_indices, data_df, 
                             overwrite_with_header='FALSE', mode='append', start_row=1,
                             updated_at_indicator=None):
    """
    Manages data in a Google Sheet using pandas DataFrame as input.

    Args:
        spreadsheet_id (str): The ID of the spreadsheet.
        sheet_name (str): The name of the sheet.
        column_indices (list): List of column letters (e.g., ['A', 'B', 'C']) for data_df.
        data_df (pd.DataFrame): DataFrame with data to write.
        overwrite_with_header (str, optional): 'TRUE' or 'FALSE'. If 'TRUE', header is written.
        mode (str, optional): 'append' (writes to next empty row without inserting)
                              or 'overwrite' (clears and writes).
        start_row (int, optional): 1-based start row for 'overwrite'. Ignored in 'append'.
        updated_at_indicator (str, optional): Cell (e.g., 'A1' or 'SheetName!A1') for timestamp.

    Returns:
        dict: Response from Google Sheets API, or None on error.
    """
    start_column_letter = column_indices[0]
    end_column_letter = column_indices[-1]

    df_to_process = data_df.copy()
    new_columns = []

    for col_name_obj in df_to_process.columns:
        if isinstance(col_name_obj, (pd.Timestamp, np.datetime64, datetime.date, datetime.datetime)):
            standardized_dt_obj = pd.to_datetime(col_name_obj)
            new_columns.append(standardized_dt_obj.date().isoformat()) # 'YYYY-MM-DD'
        else:
            new_columns.append(str(col_name_obj))
    df_to_process.columns = new_columns

    # Standardize cell values, especially datetime objects, to ISO strings or None
    for col_str_name in df_to_process.columns:
        series = df_to_process[col_str_name]
        if pd.api.types.is_datetime64_any_dtype(series.dtype):
            df_to_process[col_str_name] = series.apply(lambda x: x.isoformat(sep='T') if pd.notnull(x) else None)
        elif series.dtype == 'object' and \
            series.map(lambda x: isinstance(x, (pd.Timestamp, np.datetime64, datetime.date, datetime.datetime))).any():
            def to_iso_if_datetime_else_str(val):
                if pd.isna(val): return None
                if isinstance(val, np.datetime64):
                    if np.isnat(val): return None
                    val = pd.Timestamp(val)
                if isinstance(val, (pd.Timestamp, datetime.datetime)): return val.isoformat(sep='T')
                if isinstance(val, datetime.date): return val.isoformat()
                return str(val)
            df_to_process[col_str_name] = series.map(to_iso_if_datetime_else_str)
        else:
            df_to_process[col_str_name] = series.astype(str).replace(['nan', 'NaT', '<NA>'], [None, None, None])

    try:
        service = build('sheets', 'v4', credentials=credentials)

        if overwrite_with_header.upper() == 'TRUE': 
            header = df_to_process.columns.tolist()
            values_to_write = [header] + df_to_process.fillna('').values.tolist()
        else:
            values_to_write = df_to_process.fillna('').values.tolist()

        main_operation_result = None


        if not values_to_write:
            logger.info("No data to write (values_to_write list is empty after processing).")

        elif mode == 'append':
            next_row_to_write_to = 1 
            try:
                check_range = f"{sheet_name}!{start_column_letter}:{start_column_letter}"
                get_result = service.spreadsheets().values().get(
                    spreadsheetId=spreadsheet_id, range=check_range
                ).execute()
                existing_data_in_start_col = get_result.get('values', [])
                next_row_to_write_to = len(existing_data_in_start_col) + 1
            except HttpError as e:
                logger.error(f"HttpError determining next available row in {check_range} for append: {e}. Defaulting to row {next_row_to_write_to}.")
            except Exception as e: 
                logger.error(f"Unexpected error determining next available row in {check_range} for append: {e}. Defaulting to row {next_row_to_write_to}.")

            target_update_range = f"{sheet_name}!{start_column_letter}{next_row_to_write_to}"

            main_operation_result = service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=target_update_range,
                valueInputOption='USER_ENTERED',
                body={'values': values_to_write}
            ).execute()

            num_rows = main_operation_result.get('updatedRows', 0)
            num_cols = main_operation_result.get('updatedColumns', 0)
            if num_rows > 0 and num_cols == 0 and values_to_write and values_to_write[0]: # Estimate if API doesn't return cols
                 num_cols = len(values_to_write[0])
            msg = f"Append mode (update): {num_rows} skus tracked, append starting at {target_update_range}."
            logger.info(msg)
            print(msg)

        elif mode == 'overwrite':
            clear_range_for_overwrite = f"{sheet_name}!{start_column_letter}{start_row}:{end_column_letter}"
            service.spreadsheets().values().clear(
                spreadsheetId=spreadsheet_id,
                range=clear_range_for_overwrite,
                body={}
            ).execute()
            logger.info(f"Overwrite mode: Cleared range {clear_range_for_overwrite}.")

            write_range = f"{sheet_name}!{start_column_letter}{start_row}"
            main_operation_result = service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=write_range,
                valueInputOption='USER_ENTERED',
                body={'values': values_to_write}
            ).execute()
            num_rows = main_operation_result.get('updatedRows', 0)
            num_cols = main_operation_result.get('updatedColumns', 0)
            if num_rows > 0 and num_cols == 0 and values_to_write and values_to_write[0]:
                 num_cols = len(values_to_write[0])
            msg = f"Overwrite mode: {num_rows} skus data inserted."
            logger.info(msg)
            print(msg) 

        # --- Update timestamp indicator cell ---
        if updated_at_indicator: 
            current_time_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            indicator_text = f"Updated at: {current_time_str}"
            indicator_range_full = f"{sheet_name}!{updated_at_indicator}" if '!' not in updated_at_indicator else updated_at_indicator

            try:
                service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=indicator_range_full,
                    valueInputOption='RAW', 
                    body={'values': [[indicator_text]]}
                ).execute()
            except HttpError as indicator_err:
                logger.error(f"Failed to update timestamp indicator at {indicator_range_full}: {indicator_err}")
            except Exception as indicator_e:
                logger.error(f"An unexpected error occurred while updating timestamp at {indicator_range_full}: {indicator_e}")

        return main_operation_result

    except HttpError as err:
        logger.error(f"An HTTP error occurred during main Google Sheets operation: {err}")
        if err.resp.status == 400 and hasattr(err, 'content'):
            error_content = err.content.decode('utf-8')
            logger.error(f"Error details (HTTP 400): {error_content}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during main Google Sheets operation: {e}", exc_info=True)
        return None
    


# add a simple string to a google sheet tab:
def simple_str_add_to_sheet(spreadsheet_id, sheet_name, input_cell_a1, input_str):
    service = build('sheets', 'v4', credentials=credentials)
    formatted_sheet_name = sheet_name.replace("'", "''") 
    if ' ' in sheet_name or '!' in sheet_name or "'" in sheet_name: 
        range_a1_notation = f"'{formatted_sheet_name}'!{input_cell_a1}"
    else:
        range_a1_notation = f"{formatted_sheet_name}!{input_cell_a1}"

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_a1_notation,     
        valueInputOption='USER_ENTERED', 
        body= {'values': [[input_str]]  }
    ).execute()
