import io
import json
import logging
import os
import smtplib
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import os
import time

# Force the container's environment to use Toronto time
os.environ['TZ'] = 'America/Toronto'
time.tzset()

import numpy as np
import pandas as pd
import requests
from google.api_core.exceptions import NotFound
from google.cloud import bigquery, secretmanager, storage
from google.oauth2 import service_account
from googleapiclient.discovery import build
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from zoneinfo import ZoneInfo

from .functions import (
    download_tab_as_csv as vendor_download_tab_as_csv,
    extract_inventory_data as vendor_extract_inventory_data,
    get_all_product_inventory as vendor_get_all_product_inventory,
    get_inventory_levels as vendor_get_inventory_levels,
    get_sheet_data as vendor_get_sheet_data,
    getList as vendor_getList,
    manage_google_sheet_data as vendor_manage_google_sheet_data,
    send_email as vendor_send_email,
    simple_str_add_to_sheet as vendor_simple_str_add_to_sheet,
)


PROJECT_ID = os.getenv("PROJECT_ID", "valor-sales")
BQ_PROJECT_ID = os.getenv("BQ_PROJECT_ID", "valor-sales")
BQ_DATASET = os.getenv("BQ_DATASET", "valor_inventory_logs")
GCS_BUCKET = os.environ["GCS_BUCKET"]
TIME_ZONE = ZoneInfo(os.getenv("TIME_ZONE") or "America/Toronto")

API_SHOP = "valordistributions"
API_VERSION = "2025-04"

STLTH_TRACKER_ID = '12Xkz-R0_pTojjuYyvazLEP52SyP19viBmUHQ0o1UkzU' # "1nfPgekdL4VBwls5FuTOBgbE6Xt0HgbDkxm599aQr7hk"
JUICE_TRACKER_ID = '1AoPXz3cuaS99MgPdl6KbeHxnhkt9mZxxQIsdfWuSGDQ' # "1p9_V9XeBr_RGlT_Bw9Sdo2U7c_vycNNx7vaJ83d9_W0"
DISPOSABLE_TRACKER_ID = '1QD1q1Z4vYEeY5XvKKU_YFRWtnGEeMzfeMNnZV-RBoaE' # "1MPt1oiGcb3UJrwnUtOjJ-D4CjS7b6pgsb07ZYeTq73U"

TABLE_STLTH = "STLTH_tracker_daily_low_stock_log"
TABLE_JUICE = "Juice_tracker_daily_low_stock_log"
TABLE_DISPOSABLE = "Valor_disposable_daily_low_stock_log"

DOWNLOAD_PREFIX = "downloads"
INPUT_PREFIX = "input"
OUTPUT_PREFIX = "output"

# EMAIL_RECIPIENTS_STLTH = [
#     "abby@valordistributions.com", "sergei@stlthvape.com", "gk@stlthvape.com",
#     "avneet@stlthvape.com", "darika@stlthvape.com", "sherry@stlthvape.com",
#     "donald@stlthvape.com",  "priyana@stlthvape.com",
#     "dw@stlthvape.com", "michael@valordistributions.com",
#     "jessica@valordistributions.com", "viola@valordistributions.com",
#     "jakob@valordistributions.com", "kishor@stlthvape.com",
#     "inventory@valordistributions.com", "ryan.cruz@valordistributions.com",
#     "tim@stlthvape.com", "vaisak@stlthvape.com", 
#     "andrew@valordistributions.com", "deshan.wijesekera@valordistributions.com",
#     "lydia@stlthvape.com", "akshat.bahl@valordistributions.com",
#     "thomas.soumbos@valordistributions.com", "abdul@stlthvape.com",
#     "wei@stlthvape.com", "amal_joseph@stlthvape.com", "suchit@stlthvape.com",
#     "palak@stlthvape.com", "dharmendar@stlthvape.com",
# ]
# EMAIL_RECIPIENTS_JUICE = [
#     "abby@valordistributions.com", "Wei@canadavapelab.com",
#     "jessica@valordistributions.com", "sergei@valordistributions.com",
#     "warehouse@valordistributions.com", "martina@canadavapelab.com",
#     "tim@valordistributions.com", "lydia@stlthvape.com",
#     "akshat.bahl@valordistributions.com", "thomas.soumbos@valordistributions.com",
# ]
# EMAIL_RECIPIENTS_DISPOSABLE = [
#     "viola@valordistributions.com", "abby@valordistributions.com",
#     "sergei@valordistributions.com", "amal@valordistributions.com",
#     "sherry@stlthvape.com", "gk@stlthvape.com", "tim@valordistributions.com",
#     "andrew@valordistributions.com", "deshan.wijesekera@valordistributions.com",
#     "thomas.soumbos@valordistributions.com", "kevin.lau@valordistributions.com",
#     "roshin@valordistributions.com", "steven.ruan@valordistributions.com",
#     "glenn@valordistributions.com", "lydia@stlthvape.com",
#     "akshat.bahl@valordistributions.com", "wei@stlthvape.com",
#     "kishor@stlthvape.com", "deep@valordistributions.com",
#     "vaisak@stlthvape.com", "suchit@stlthvape.com",
# ]


EMAIL_RECIPIENTS_STLTH = ['lydia@stlthvape.com']
EMAIL_RECIPIENTS_JUICE = ['lydia@stlthvape.com']
EMAIL_RECIPIENTS_DISPOSABLE = ['lydia@stlthvape.com']

@dataclass
class RuntimePaths:
    root: Path
    downloads: Path
    input: Path
    output: Path
    logs: Path


def setup_logging(log_path: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_path, mode="w")],
    )


def make_paths() -> RuntimePaths:
    root = Path(tempfile.mkdtemp(prefix="valor_inventory_"))
    paths = RuntimePaths(
        root=root,
        downloads=root / "downloads",
        input=root / "input",
        output=root / "output",
        logs=root / "log",
    )
    for path in [paths.downloads, paths.input, paths.output, paths.logs]:
        path.mkdir(parents=True, exist_ok=True)
    return paths


def secret_json(secret_env: str, inline_env: str | None = None) -> dict[str, Any]:
    if inline_env and os.getenv(inline_env):
        payload = os.environ[inline_env]
        source = inline_env
    else:
        secret_id = os.environ[secret_env]
        name = secret_id if secret_id.startswith("projects/") else f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/latest"
        client = secretmanager.SecretManagerServiceClient()
        payload = client.access_secret_version(request={"name": name}).payload.data.decode("utf-8")
        source = secret_id
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Secret {source!r} must contain valid JSON for {secret_env}.") from exc


def secret_payload(secret_env: str, inline_env: str | None = None) -> str:
    if inline_env and os.getenv(inline_env):
        return os.environ[inline_env]
    secret_id = os.environ[secret_env]
    name = secret_id if secret_id.startswith("projects/") else f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/latest"
    client = secretmanager.SecretManagerServiceClient()
    return client.access_secret_version(request={"name": name}).payload.data.decode("utf-8").strip()


def shopify_secret(secret_env: str, inline_env: str | None = None) -> dict[str, Any]:
    payload = secret_payload(secret_env, inline_env)
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"Shopify_store_password": payload, "Store_Key": None}


def sheets_service():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.readonly"]
    if os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"):
        info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        return build("sheets", "v4", credentials=creds, cache_discovery=False)
    return build("sheets", "v4", cache_discovery=False)


def storage_bucket():
    return storage.Client(project=PROJECT_ID).bucket(GCS_BUCKET)


def upload_file_to_gcs(path: Path, object_name: str) -> None:
    storage_bucket().blob(object_name).upload_from_filename(path)
    logging.info("Uploaded %s to gs://%s/%s", path.name, GCS_BUCKET, object_name)


def upload_csv(df: pd.DataFrame, path: Path, object_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    upload_file_to_gcs(path, object_name)


def read_csv_from_gcs(object_name: str, **kwargs) -> pd.DataFrame:
    blob = storage_bucket().blob(object_name)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{GCS_BUCKET}/{object_name} does not exist")
    return pd.read_csv(io.BytesIO(blob.download_as_bytes()), **kwargs)


def quote_sheet(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"


def col_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def sheet_values(spreadsheet_id: str, sheet_name: str, a1_range: str) -> list[list[Any]]:
    service = sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{quote_sheet(sheet_name)}!{a1_range}",
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute()
    return result.get("values", [])


def download_tab_as_csv(
    spreadsheet_id: str,
    sheet_name: str,
    destination: Path,
    column_range: str,
    fallback_object: str,
    dtype: dict[str, str] | None = None,
) -> pd.DataFrame:
    success = vendor_download_tab_as_csv(spreadsheet_id, sheet_name, destination, column_range=column_range)
    if success:
        upload_file_to_gcs(destination, fallback_object)
        return pd.read_csv(destination, dtype=dtype)
    logging.warning("Sheet download failed; using latest GCS fallback %s", fallback_object)
    return read_csv_from_gcs(fallback_object, dtype=dtype)


def manage_google_sheet_data(
    spreadsheet_id: str,
    sheet_name: str,
    column_indices: list[str],
    data_df: pd.DataFrame,
    mode: str,
    start_row: int,
    updated_at_indicator: str | None = None,
    overwrite_with_header: str = "False",
) -> None:
    return vendor_manage_google_sheet_data(
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        column_indices=column_indices,
        data_df=data_df,
        overwrite_with_header=overwrite_with_header,
        mode=mode,
        start_row=start_row,
        updated_at_indicator=updated_at_indicator,
    )


def get_sheet_data(
    spreadsheet_id: str,
    sheet_name: str,
    columns_to_retrieve: list[int],
    header_row: int,
) -> pd.DataFrame:
    return vendor_get_sheet_data(
        spreadsheet_id=spreadsheet_id,
        tab_name=sheet_name,
        columns_to_retrieve=columns_to_retrieve,
        use_col_indices=True,
        header_row=header_row,
    )


def simple_str_add_to_sheet(spreadsheet_id: str, sheet_name: str, input_cell_a1: str, input_str: str) -> None:
    return vendor_simple_str_add_to_sheet(spreadsheet_id, sheet_name, input_cell_a1, input_str)


def requests_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PUT"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_all_product_inventory(api_shop: str, api_version: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    return vendor_get_all_product_inventory(api_shop, api_version, headers, logging.getLogger(__name__))


def extract_inventory_data(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return vendor_extract_inventory_data(products)


def get_inventory_levels(api_shop: str, api_version: str, headers: dict[str, str], item_ids: list[Any]) -> list[dict[str, Any]]:
    return vendor_get_inventory_levels(api_shop, api_version, headers, item_ids, logging.getLogger(__name__))


def http_error_detail(exc: BaseException) -> str:
    last_attempt = getattr(exc, "last_attempt", None)
    if last_attempt is not None:
        try:
            exc = last_attempt.exception()
        except Exception:
            pass

    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)

    body = getattr(response, "text", "") or ""
    return f"status={response.status_code}, body={body[:500]}"


def get_list(url: str, headers: dict[str, str], content_key: str, search_keys: dict[str, str]) -> list[dict[str, Any]]:
    try:
        return vendor_getList(url, headers, content_key, search_keys=search_keys, limit_max_pages=0, mute_print=True)
    except Exception as exc:
        logging.exception(
            "DEAR API request failed for %s with search_keys=%s: %s",
            url,
            search_keys,
            http_error_detail(exc),
        )
        raise


def send_email(sender: str, password: str, recipients: list[str], subject: str, html: str, attachments: list[Path]) -> None:
    return vendor_send_email(sender, password, recipients, subject, html, attachments)


def now_local() -> datetime:
    # This forces the current time to be calculated specifically for Toronto,
    # completely ignoring whatever the Linux container thinks the time is.
    return datetime.now(ZoneInfo("America/Toronto"))


def normalize_date(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce").dt.date
    return parsed.where(pd.notna(parsed), None)


def coerce_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    return df


def coerce_bq_numeric_value(value: Any) -> Decimal | None:
    if pd.isna(value):
        return None
    text = str(value).replace(",", "").strip()
    if not text or text.lower() in {"nan", "nat", "none", "null"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def coerce_bq_numeric_series(series: pd.Series) -> pd.Series:
    return series.map(coerce_bq_numeric_value)


def bq_table_id(table: str) -> str:
    return f"{BQ_PROJECT_ID}.{BQ_DATASET}.{table}"




def align_for_bq(df: pd.DataFrame, table: str) -> pd.DataFrame:
    client = bigquery.Client(project=BQ_PROJECT_ID)
    schema = client.get_table(bq_table_id(table)).schema
    out = df.copy()
    for field in schema:
        if field.name not in out.columns:
            out[field.name] = None
        if field.field_type == "NUMERIC":
            out[field.name] = coerce_bq_numeric_series(out[field.name])
        elif field.field_type == "DATE":
            out[field.name] = normalize_date(out[field.name])
        else:
            out[field.name] = out[field.name].where(pd.notnull(out[field.name]), None)
    return out[[field.name for field in schema]]



# Configuration Mapping
BQ_CONFIG = {
    TABLE_STLTH: {
        "table_id": bq_table_id(TABLE_STLTH),
        "numeric_cols": ["QTY_BOX", "STOCK_IN_DAYS", "SHOPIFY_QTY_merged", "NUM_OF_DAYS_0_STOCK"],
    },
    TABLE_JUICE: {
        "table_id": bq_table_id(TABLE_JUICE),
        "numeric_cols": ["SHOPIFY_QTY_merged", "NUM_OF_DAYS_0_STOCK"],
    },
    TABLE_DISPOSABLE: {
        "table_id": bq_table_id(TABLE_DISPOSABLE),
        "numeric_cols": ["AVG_DAILY_SALES", "SHOPIFY_STOCK_merged", "STOCK_IN_DAYS", "IN_PRO_VALOR", "KIT_INVENTORY_AT_SC", "NUM_OF_DAYS_0_STOCK"], 
    }
}



def replace_recording_date_rows(config_key: str, rows: pd.DataFrame, recording_date: str) -> None:
    if rows.empty:
        logging.info("No rows to write for %s", config_key)
        return
        
    client = bigquery.Client(project=BQ_PROJECT_ID)
    config = BQ_CONFIG[config_key]
    table_id = config["table_id"]
    
    # 1. Align/Clean data
    aligned = align_for_bq(rows, config_key)
    
    # 2. Fix Numeric columns for PyArrow alignment
    for col in config["numeric_cols"]:
        if col in aligned.columns:
            aligned[col] = coerce_bq_numeric_series(aligned[col])

    # 3. Delete old date rows
    delete_job = client.query(
        f"DELETE FROM `{table_id}` WHERE Recording_date = @recording_date",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("recording_date", "STRING", recording_date)]
        ),
    )
    delete_job.result()
    
    # 4. Load with the table schema so PyArrow uses BigQuery NUMERIC correctly.
    job_config = bigquery.LoadJobConfig(
        schema=client.get_table(table_id).schema,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND
    )
    
    load_job = client.load_table_from_dataframe(aligned, table_id, job_config=job_config)
    load_job.result()
    
    logging.info("Wrote %s rows to %s", len(aligned), table_id)



def get_stlth_historical_counts() -> pd.DataFrame:
    client = bigquery.Client(project=BQ_PROJECT_ID)
    query = f"""
        WITH date_spine AS (
            SELECT d AS raw_date FROM UNNEST(GENERATE_DATE_ARRAY(DATE_SUB(CURRENT_DATE('America/Toronto'), INTERVAL 6 DAY), CURRENT_DATE('America/Toronto'))) AS d
        ),
        shifts AS (SELECT 'AM' AS shift UNION ALL SELECT 'PM' AS shift),
        scaffold AS (
            SELECT CAST(raw_date AS STRING) || shift AS Recording_date, raw_date, shift
            FROM date_spine CROSS JOIN shifts
            WHERE NOT (raw_date = CURRENT_DATE('America/Toronto') AND shift = 'PM' AND EXTRACT(HOUR FROM CURRENT_DATETIME('America/Toronto')) < 12)
        ),
        actual_data AS (
            SELECT 
                Recording_date,
                COUNTIF(SHOPIFY_QTY_merged <= 0) AS OOS_SKU_count,
                COUNTIF(SHOPIFY_QTY_merged > 0) AS Low_Stock_SKU_count
            FROM `{bq_table_id(TABLE_STLTH)}`
            WHERE Recording_date >= CAST(DATE_SUB(CURRENT_DATE('America/Toronto'), INTERVAL 6 DAY) AS STRING) || 'AM'
            GROUP BY Recording_date
        )
        SELECT 
            s.Recording_date,
            COALESCE(a.OOS_SKU_count, 0) AS OOS_SKU_count,
            COALESCE(a.Low_Stock_SKU_count, 0) AS Low_Stock_SKU_count
        FROM scaffold s LEFT JOIN actual_data a USING (Recording_date)
        ORDER BY s.raw_date ASC, s.shift ASC
    """
    return client.query(query).to_dataframe()


def get_juice_historical_counts() -> pd.DataFrame:
    client = bigquery.Client(project=BQ_PROJECT_ID)
    query = f"""
        WITH date_spine AS (
            SELECT d AS raw_date FROM UNNEST(GENERATE_DATE_ARRAY(DATE_SUB(CURRENT_DATE('America/Toronto'), INTERVAL 6 DAY), CURRENT_DATE('America/Toronto'))) AS d
        ),
        shifts AS (SELECT 'AM' AS shift UNION ALL SELECT 'PM' AS shift),
        scaffold AS (
            SELECT CAST(raw_date AS STRING) || shift AS Recording_date, raw_date, shift
            FROM date_spine CROSS JOIN shifts
            WHERE NOT (raw_date = CURRENT_DATE('America/Toronto') AND shift = 'PM' AND EXTRACT(HOUR FROM CURRENT_DATETIME('America/Toronto')) < 12)
        ),
        actual_data AS (
            SELECT 
                Recording_date,
                COUNT(SKU) AS OOS_SKU_count
            FROM `{bq_table_id(TABLE_JUICE)}`
            WHERE Recording_date >= CAST(DATE_SUB(CURRENT_DATE('America/Toronto'), INTERVAL 6 DAY) AS STRING) || 'AM'
            GROUP BY Recording_date
        )
        SELECT 
            s.Recording_date,
            COALESCE(a.OOS_SKU_count, 0) AS OOS_SKU_count
        FROM scaffold s LEFT JOIN actual_data a USING (Recording_date)
        ORDER BY s.raw_date ASC, s.shift ASC
    """
    return client.query(query).to_dataframe()


def get_disposable_historical_counts() -> pd.DataFrame:
    client = bigquery.Client(project=BQ_PROJECT_ID)
    query = f"""
        WITH date_spine AS (
            SELECT d AS raw_date FROM UNNEST(GENERATE_DATE_ARRAY(DATE_SUB(CURRENT_DATE('America/Toronto'), INTERVAL 6 DAY), CURRENT_DATE('America/Toronto'))) AS d
        ),
        shifts AS (SELECT 'AM' AS shift UNION ALL SELECT 'PM' AS shift),
        scaffold AS (
            SELECT CAST(raw_date AS STRING) || shift AS Recording_date, raw_date, shift
            FROM date_spine CROSS JOIN shifts
            WHERE NOT (raw_date = CURRENT_DATE('America/Toronto') AND shift = 'PM' AND EXTRACT(HOUR FROM CURRENT_DATETIME('America/Toronto')) < 12)
        ),
        actual_data AS (
            SELECT 
                Recording_date,
                COUNTIF(SHOPIFY_STOCK_merged <= 0 AND `ORDER` != ' - ') AS OOS_open,
                COUNTIF(SHOPIFY_STOCK_merged <= 0 AND `ORDER` = ' - ') AS OOS_no,
                COUNTIF(SHOPIFY_STOCK_merged > 0 AND `ORDER` != ' - ') AS Low_open
            FROM `{bq_table_id(TABLE_DISPOSABLE)}`
            WHERE Recording_date >= CAST(DATE_SUB(CURRENT_DATE('America/Toronto'), INTERVAL 6 DAY) AS STRING) || 'AM'
            GROUP BY Recording_date
        )
        SELECT 
            s.Recording_date,
            COALESCE(a.OOS_open, 0) AS OOS_SKU_count_open_orders,
            COALESCE(a.OOS_no, 0) AS OOS_SKU_count_no_order,
            COALESCE(a.Low_open, 0) AS Low_Stock_SKU_count_open_orders
        FROM scaffold s LEFT JOIN actual_data a USING (Recording_date)
        ORDER BY s.raw_date ASC, s.shift ASC
    """
    df = client.query(query).to_dataframe()
    
    # Rename the clean SQL columns back to your original email display names
    df = df.rename(columns={
        "OOS_SKU_count_open_orders": "OOS_SKU_count(open orders)",
        "OOS_SKU_count_no_order": "OOS_SKU_count(no order)",
        "Low_Stock_SKU_count_open_orders": "Low_Stock_SKU_count(open orders)"
    })
    
    return df


def dear_headers_from_config(config: dict[str, Any]) -> dict[str, str]:
    account_id = config.get("api-auth-accountid") or config.get("account_id")
    application_key = config.get("api-auth-applicationkey") or config.get("api_key")
    if not account_id or not application_key:
        raise ValueError(
            "DEAR config must include account_id/api_key or "
            "api-auth-accountid/api-auth-applicationkey."
        )
    return {
        "api-auth-accountid": str(account_id),
        "api-auth-applicationkey": str(application_key),
    }


def load_configs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    shopify = shopify_secret("SHOPIFY_SECRET_ID", "SHOPIFY_CONFIG_JSON")
    dear = dear_headers_from_config(secret_json("DEAR_SECRET_ID", "DEAR_CONFIG_JSON"))
    email = secret_json("EMAIL_SECRET_ID", "EMAIL_CONFIG_JSON")
    return shopify, dear, email


def build_inventory(shopify_config: dict[str, Any], paths: RuntimePaths) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:

    # --- CHECKPOINT 1: Shopify Initial Pull ---
    logging.info("--> Starting Shopify API pull...")

    headers = {"X-Shopify-Access-Token": shopify_config["Shopify_store_password"]}
    products = get_all_product_inventory(API_SHOP, API_VERSION, headers)
    inventory_df = pd.DataFrame(extract_inventory_data(products))
    inventory_df["variant_sku"] = inventory_df["variant_sku"].fillna("none").astype(str)

    # --- CHECKPOINT 2: Google Sheets & GCS Downloads ---
    logging.info("--> Starting Google Sheets and GCS scope pull...")

    stlth_reporting_sku = download_tab_as_csv(
        STLTH_TRACKER_ID,
        "STLTH_Inventory_Summary",
        paths.input / f"{STLTH_TRACKER_ID}_STLTH_Inventory_Summary.csv",
        "A:E",
        f"{INPUT_PREFIX}/{STLTH_TRACKER_ID}_STLTH_Inventory_Summary.csv",
        dtype={"VALOR SKU": "str"},
    )
    juice_reporting_sku = download_tab_as_csv(
        JUICE_TRACKER_ID,
        "JUICE_Inventory_Summary",
        paths.input / f"{JUICE_TRACKER_ID}_JUICE_Inventory_Summary.csv",
        "A:G",
        f"{INPUT_PREFIX}/{JUICE_TRACKER_ID}_JUICE_Inventory_Summary.csv",
        dtype={"SKU": "str"},
    )
    salt_sku = read_csv_from_gcs(f"{INPUT_PREFIX}/JUICE TRACKER - STLTH SALT.csv", dtype={"SKU": "str"})

    disposable_df = inventory_df[
        (~inventory_df["product_title"].str.lower().str.contains("excise|variety carton|flavour menu|tester tips|variety stand|marketing", na=False))
        & (~inventory_df["variant_sku"].str.contains("-JPY|-STH|none", na=False))
        & (inventory_df["sku_status"] != "draft")
    ]
    all_targeted_skus = (
        set(stlth_reporting_sku["VALOR SKU"].dropna().tolist())
        .union(set(juice_reporting_sku["SKU"].dropna().tolist()))
        .union(set(salt_sku["SKU"].dropna().tolist()))
        .union(set(disposable_df["variant_sku"].dropna().tolist()))
    )

    targeted_item_ids = inventory_df[inventory_df["variant_sku"].isin(all_targeted_skus)]["inventory_item_id"].dropna().unique().tolist()
    logging.info("Fetching real-time inventory levels for %s targeted items", len(targeted_item_ids))

    levels_df = pd.DataFrame(get_inventory_levels(API_SHOP, API_VERSION, headers, targeted_item_ids))
    if not levels_df.empty:
        actual_inventory = levels_df.groupby("inventory_item_id")["available"].sum().reset_index()
        actual_inventory.rename(columns={"available": "inventory_quantity"}, inplace=True)
        inventory_df = pd.merge(inventory_df, actual_inventory, on="inventory_item_id", how="left")
        inventory_df["inventory_quantity"] = inventory_df["inventory_quantity"].fillna(inventory_df["legacy_inventory_quantity"]).astype(int)
    else:
        inventory_df.rename(columns={"legacy_inventory_quantity": "inventory_quantity"}, inplace=True)

    upload_csv(inventory_df, paths.output / "valor_all_sku_inventory.csv", f"{OUTPUT_PREFIX}/valor_all_sku_inventory.csv")
    sku_inventory = inventory_df[["variant_sku", "inventory_quantity"]]
    return inventory_df, sku_inventory, stlth_reporting_sku, juice_reporting_sku


def run_stlth(sku_inventory: pd.DataFrame, stlth_reporting_sku: pd.DataFrame, email_config: dict[str, str], paths: RuntimePaths, log_path: Path) -> None:
    today = now_local()
    today_str = today.strftime("%Y-%m-%d")
    am_pm = today.strftime("%p")
    recording_date = today_str + am_pm

    tracked_skus = set(stlth_reporting_sku["VALOR SKU"].dropna().tolist())
    stlth_sku_inv = sku_inventory[sku_inventory["variant_sku"].isin(tracked_skus)]
    upload_csv(stlth_sku_inv, paths.downloads / "stlth_sku_inv.csv", f"{DOWNLOAD_PREFIX}/stlth_sku_inv.csv")
    manage_google_sheet_data(STLTH_TRACKER_ID, "auto_test_upload", ["A", "B"], stlth_sku_inv, "overwrite", 3, "B1")

    active_skus = set(stlth_reporting_sku[stlth_reporting_sku["Is_Discontinue"] == "A"]["VALOR SKU"].dropna().tolist())
    oos_upload = sku_inventory[
        sku_inventory["variant_sku"].isin(active_skus) & (sku_inventory["inventory_quantity"] <= 0)
    ][["variant_sku"]].reset_index(drop=True)
    oos_upload["date"] = today_str
    oos_upload["AM_PM"] = am_pm
    manage_google_sheet_data(STLTH_TRACKER_ID, "auto_test_upload", ["E", "F", "G"], oos_upload, "append", 3, "F1")

    qced = get_sheet_data(STLTH_TRACKER_ID, "auto_test_upload", [36, 37, 38, 39], 2)
    qced_valid = qced[~qced["SKU"].isna()].reset_index(drop=True) if "SKU" in qced else qced
    upload_csv(qced_valid, paths.downloads / "stlth_QCed_but_not_upload_df_valid.csv", f"{DOWNLOAD_PREFIX}/stlth_QCed_but_not_upload_df_valid.csv")
    manage_google_sheet_data(STLTH_TRACKER_ID, "auto_test_upload", ["AF", "AG", "AH", "AI"], qced_valid, "overwrite", 3, "AG1")
    simple_str_add_to_sheet(STLTH_TRACKER_ID, "STLTH_Inventory_Summary", "O1", f"Updated at: {today:%Y-%m-%d %H:%M:%S}")

    summary = download_tab_as_csv(
        STLTH_TRACKER_ID,
        "STLTH_Inventory_Summary",
        paths.downloads / "STLTH_tracker_summary_daily.csv",
        "A:M",
        f"{DOWNLOAD_PREFIX}/STLTH_tracker_summary_daily.csv",
        dtype={"VALOR SKU": "str"},
    )
    low_stock = summary[
        (pd.to_numeric(summary["STOCK_IN_DAYS"], errors="coerce") <= 7)
        & (summary["Is_Discontinue"] == "A")
    ][["VALOR SKU", "Product Title", "MG", "QTY / BOX", "STOCK_IN_DAYS", "SHOPIFY_QTY_merged", "CATEGORY", "DATE_OUT_OF_STOCK", "#_OF_DAYS_0_STOCK"]].reset_index(drop=True)
    low_stock["Recording_date"] = recording_date
    low_stock = low_stock.sort_values(by=["STOCK_IN_DAYS", "Product Title"]).reset_index(drop=True)

    bq_rows = low_stock.rename(columns={"VALOR SKU": "VALOR_SKU", "Product Title": "Product_Title", "QTY / BOX": "QTY_BOX", "#_OF_DAYS_0_STOCK": "NUM_OF_DAYS_0_STOCK"})
    bq_rows = coerce_numeric(bq_rows, ["QTY_BOX", "STOCK_IN_DAYS", "SHOPIFY_QTY_merged", "NUM_OF_DAYS_0_STOCK"])
    replace_recording_date_rows(TABLE_STLTH, bq_rows, recording_date)

    daily_counts = get_stlth_historical_counts()

    static_cols = ["VALOR SKU", "Product Title", "MG", "QTY / BOX", "STOCK_IN_DAYS", "SHOPIFY_QTY_merged", "CATEGORY", "DATE_OUT_OF_STOCK", "#_OF_DAYS_0_STOCK"]
    eta_cols = [col for col in summary.columns if col.startswith("Last ETA")]
    email_cols = static_cols + ([eta_cols[0]] if am_pm == "PM" and eta_cols else [])
    detail = summary[
        (pd.to_numeric(summary["STOCK_IN_DAYS"], errors="coerce") <= 7)
        & (summary["Is_Discontinue"] == "A")
    ][email_cols].sort_values(by="STOCK_IN_DAYS").reset_index(drop=True)

    body = f"""
        <html><body>
        <p>Hi Team,</p>
        <p>STLTH tracker has been updated: <a href="https://docs.google.com/spreadsheets/d/{STLTH_TRACKER_ID}">STLTH Tracker</a></p>
        <p>OOS and Low Stock SKU count by date AM/ PM (including ready_to_ship_but_not_uploaded qty):</p>
        {daily_counts.to_html(index=False, escape=False)}
        <br><p>OOS and Low stock detail {today_str} {am_pm} (including ready_to_ship_but_not_uploaded qty):</p>
        {detail.to_html(index=False, escape=False)}
        <br><p>Best regards</p>
        </body></html>
        """
    send_email(email_config["sender_email"], email_config["sender_password"], EMAIL_RECIPIENTS_STLTH, f"test STLTH low stock {today_str} {am_pm}", body, [log_path])


def run_juice(sku_inventory: pd.DataFrame, juice_reporting_sku: pd.DataFrame, email_config: dict[str, str], paths: RuntimePaths, log_path: Path) -> None:
    today = now_local()
    today_str = today.strftime("%Y-%m-%d")
    am_pm = today.strftime("%p")
    recording_date = today_str + am_pm

    salt_sku = read_csv_from_gcs(f"{INPUT_PREFIX}/JUICE TRACKER - STLTH SALT.csv", dtype={"SKU": "str"})
    tracked_skus = set(juice_reporting_sku["SKU"].dropna().tolist()).union(set(salt_sku["SKU"].dropna().tolist()))
    juice_inv = sku_inventory[sku_inventory["variant_sku"].isin(tracked_skus)]
    upload_csv(juice_inv, paths.downloads / "juice_sku_inv.csv", f"{DOWNLOAD_PREFIX}/juice_sku_inv.csv")
    manage_google_sheet_data(JUICE_TRACKER_ID, "auto_test_upload", ["A", "B"], juice_inv, "overwrite", 3, "B1")

    active = set(juice_reporting_sku[juice_reporting_sku["Is_Discontinue"] == "A"]["SKU"].dropna().tolist())
    oos_upload = sku_inventory[sku_inventory["variant_sku"].isin(active) & (sku_inventory["inventory_quantity"] <= 0)][["variant_sku"]].reset_index(drop=True)
    oos_upload["date"] = today_str
    oos_upload["AM_PM"] = am_pm
    manage_google_sheet_data(JUICE_TRACKER_ID, "auto_test_upload", ["D", "E", "F"], oos_upload, "append", 3, "E1")

    transferred = get_sheet_data(JUICE_TRACKER_ID, "auto_test_upload", [19, 20, 21, 22, 23], 2)
    transferred_valid = transferred[~transferred["SKU"].isna()].reset_index(drop=True) if "SKU" in transferred else transferred
    upload_csv(transferred_valid, paths.downloads / "transferred_but_not_restocked_df_valid.csv", f"{DOWNLOAD_PREFIX}/transferred_but_not_restocked_df_valid.csv")
    manage_google_sheet_data(JUICE_TRACKER_ID, "auto_test_upload", ["O", "P", "Q", "R"], transferred_valid, "overwrite", 3, "P1")
    simple_str_add_to_sheet(JUICE_TRACKER_ID, "JUICE_Inventory_Summary", "P1", f"OOS updated at: {today:%Y-%m-%d %H:%M:%S}")

    summary = download_tab_as_csv(
        JUICE_TRACKER_ID,
        "JUICE_Inventory_Summary",
        paths.downloads / "Juice_tracker_summary_daily.csv",
        "A:L",
        f"{DOWNLOAD_PREFIX}/Juice_tracker_summary_daily.csv",
        dtype={"SKU": "str"},
    )
    oos = summary[
        (pd.to_numeric(summary["SHOPIFY_QTY_merged"], errors="coerce") <= 0)
        & (summary["Is_Discontinue"] == "A")
    ][["SKU", "Brand", "Flavor", "variation", "MG", "ML", "SHOPIFY_QTY_merged", "DATE_OUT_OF_STOCK", "#_OF_DAYS_0_STOCK", "Sales_Rank"]].reset_index(drop=True)
    oos["Recording_date"] = recording_date
    oos["#_OF_DAYS_0_STOCK"] = pd.to_numeric(oos["#_OF_DAYS_0_STOCK"], errors="coerce").fillna(0).astype(int)
    oos = oos.sort_values(by=["Sales_Rank", "Brand"]).reset_index(drop=True)

    bq_rows = oos.rename(columns={"#_OF_DAYS_0_STOCK": "NUM_OF_DAYS_0_STOCK"})
    bq_rows["ML"] = bq_rows["ML"].astype(str)
    bq_rows = coerce_numeric(bq_rows, ["SHOPIFY_QTY_merged", "NUM_OF_DAYS_0_STOCK"])
    replace_recording_date_rows(TABLE_JUICE, bq_rows, recording_date)

    counts = get_juice_historical_counts()

    body = f"""
        <html><body>
        <p>Hi Team,</p>
        <p>Juice tracker has been updated: <a href="https://docs.google.com/spreadsheets/d/{JUICE_TRACKER_ID}">Juice Tracker</a></p>
        <p>total shopify_qty_merged (including transferred_but_not_restocked qty) for the JUICE SKUs tracked in the sheet: {pd.to_numeric(summary["SHOPIFY_QTY_merged"], errors="coerce").sum()}</p>
        <p>OOS SKU count by date AM/ PM:</p>
        {counts.to_html(index=False, escape=False)}
        <br><p>OOS detail {today_str} {am_pm}:</p>
        {oos.to_html(index=False, escape=False)}
        <br><p>Best regards</p>
        </body></html>
        """
    send_email(email_config["sender_email"], email_config["sender_password"], EMAIL_RECIPIENTS_JUICE, f"test Juice out of stock {today_str} {am_pm} ", body, [log_path])


def run_disposable(inventory_df: pd.DataFrame, sku_inventory: pd.DataFrame, dear_headers: dict[str, str], email_config: dict[str, str], paths: RuntimePaths, log_path: Path) -> None:
    today = now_local()
    today_str = today.strftime("%Y-%m-%d")
    am_pm = today.strftime("%p")
    recording_date = today_str + am_pm

    inv_upload = inventory_df[
        (~inventory_df["product_title"].str.lower().str.contains("excise|variety carton|flavour menu|tester tips|variety stand|marketing", na=False))
        & (~inventory_df["variant_sku"].str.contains("-JPY|-STH|none", na=False))
        & (inventory_df["sku_status"] != "draft")
    ][["variant_sku", "inventory_quantity", "sku_status"]].reset_index(drop=True)
    manage_google_sheet_data(DISPOSABLE_TRACKER_ID, "test_upload_Valor_d", ["J", "K", "L"], inv_upload, "overwrite", 3, "K1")

    inventory_url = "https://inventory.dearsystems.com/ExternalApi/v2/ref/productavailability"
    categories = ["DISPOSABLE", "DISPOSABLE - CARTON", "DISPOSABLE - KIT", "DISPOSABLE - KIT PACKAGING", "DISPOSABLE - OTHER", "DISPOSABLE - STAMPED KIT", "DISPOSABLE - UNSTAMPED KIT", "DISPOSABLES"]
    dear_rows: list[dict[str, Any]] = []
    for category in categories:
        dear_rows.extend(get_list(inventory_url, dear_headers, "ProductAvailabilityList", {"Category": category}))
    dear_df = pd.DataFrame(dear_rows)
    locations = ["READY FOR PRODUCTION", "SC WAREHOUSE", "STLTH Stamping", "WAITING TO BE STAMPED", "WORK IN PROGRESS"]
    dear_inv = dear_df[(pd.to_numeric(dear_df["OnHand"], errors="coerce") > 0) & (dear_df["Location"].isin(locations))][["SKU", "Name", "Location", "OnHand"]]
    dear_pivot = dear_inv.pivot_table(index=["SKU", "Name"], columns="Location", values="OnHand", aggfunc="sum", fill_value=0).reset_index()
    upload_csv(dear_pivot, paths.downloads / "Dear_inv_df_pivot.csv", f"{DOWNLOAD_PREFIX}/Dear_inv_df_pivot.csv")
    manage_google_sheet_data(DISPOSABLE_TRACKER_ID, "test_upload_Valor_d", ["A", "B", "C", "D", "E", "F", "G"], dear_pivot, "overwrite", 2, "B1", "TRUE")

    open_order = get_sheet_data(DISPOSABLE_TRACKER_ID, "test_upload_Valor_d", [18, 19, 20, 21], 2)
    open_order_valid = open_order[~open_order["SKU"].isna()].reset_index(drop=True) if "SKU" in open_order else open_order
    upload_csv(open_order_valid, paths.downloads / "open_order_df.csv", f"{DOWNLOAD_PREFIX}/open_order_df.csv")
    manage_google_sheet_data(DISPOSABLE_TRACKER_ID, "test_upload_Valor_d", ["N", "O", "P", "Q"], open_order_valid, "overwrite", 3, "O1")

    summary = download_tab_as_csv(
        DISPOSABLE_TRACKER_ID,
        "test_Disposable_Inventory_Summary",
        paths.downloads / "Valor_Disposable_tracker_summary_daily.csv",
        "A:N",
        f"{DOWNLOAD_PREFIX}/Valor_Disposable_tracker_summary_daily.csv",
        dtype={"SKU": "str"},
    )
    summary["STATUS"] = summary["STATUS"].fillna("none")
    active_skus = set(summary[~summary["STATUS"].str.contains("DISCONTINUED", na=False)]["SKU"].dropna().tolist())
    oos_upload = sku_inventory[sku_inventory["variant_sku"].isin(active_skus) & (sku_inventory["inventory_quantity"] <= 0)][["variant_sku"]].reset_index(drop=True)
    oos_upload["date"] = today_str
    oos_upload["AM_PM"] = am_pm
    manage_google_sheet_data(DISPOSABLE_TRACKER_ID, "test_upload_Valor_d", ["AP", "AQ", "AR"], oos_upload, "append", 3, "AQ1")

    qced = get_sheet_data(DISPOSABLE_TRACKER_ID, "test_upload_Valor_d", [53, 54, 55, 56], 2)
    upload_csv(qced, paths.downloads / "transferred_but_not_restocked_df_check.csv", f"{DOWNLOAD_PREFIX}/transferred_but_not_restocked_df_check.csv")
    qced_valid = qced[~qced["SKU"].isna()].reset_index(drop=True) if "SKU" in qced else qced
    upload_csv(qced_valid, paths.downloads / "transferred_but_not_restocked_df_valid_dispo.csv", f"{DOWNLOAD_PREFIX}/transferred_but_not_restocked_df_valid_dispo.csv")
    manage_google_sheet_data(DISPOSABLE_TRACKER_ID, "test_upload_Valor_d", ["AW", "AX", "AY", "AZ"], qced_valid, "overwrite", 3, "AX1")
    simple_str_add_to_sheet(DISPOSABLE_TRACKER_ID, "test_Disposable_Inventory_Summary", "P1", f"Updated at: {today:%Y-%m-%d %H:%M:%S}")

    summary = download_tab_as_csv(
        DISPOSABLE_TRACKER_ID,
        "test_Disposable_Inventory_Summary",
        paths.downloads / "Valor_Disposable_tracker_summary_daily.csv",
        "A:N",
        f"{DOWNLOAD_PREFIX}/Valor_Disposable_tracker_summary_daily.csv",
        dtype={"SKU": "str"},
    )
    summary["STATUS"] = summary["STATUS"].fillna("none")
    summary["TYPE"] = summary["TYPE"].fillna("none")
    for col in ["KIT INVENTORY AT SC", "STOCK IN DAYS", "AVG DAILY SALES"]:
        summary[col] = pd.to_numeric(summary[col].astype(str).str.replace(",", "", regex=False).replace("", "0"), errors="coerce")
    low_stock = summary[
        ((summary["TYPE"] != "LK ONLY")
         & (~summary["STATUS"].str.contains("DISCONTINUED", na=False))
         & (summary["PUBLISHED"] == "active")
         & (summary["STOCK IN DAYS"] <= 7.0))
        | (summary["SKU"].str.contains("-LK$", na=False) & (summary["ORDER #"] != " - "))
    ].reset_index(drop=True)
    low_stock["Recording_date"] = recording_date
    low_stock = low_stock.sort_values(by=["STOCK IN DAYS", "Product Name"]).reset_index(drop=True)

    bq_rows = low_stock.rename(columns={
        "Product Name": "Product_Name",
        "AVG DAILY SALES": "AVG_DAILY_SALES",
        "STOCK IN DAYS": "STOCK_IN_DAYS",
        "IN-PRO VALOR": "IN_PRO_VALOR",
        "ORDER #": "ORDER",
        "ORDER DATE": "ORDER_DATE",
        "KIT INVENTORY AT SC": "KIT_INVENTORY_AT_SC",
        "#_OF_DAYS_0_STOCK": "NUM_OF_DAYS_0_STOCK",
    })
    bq_rows = coerce_numeric(bq_rows, ["AVG_DAILY_SALES", "SHOPIFY_STOCK_merged", "STOCK_IN_DAYS", "IN_PRO_VALOR", "KIT_INVENTORY_AT_SC", "NUM_OF_DAYS_0_STOCK"])
    replace_recording_date_rows(TABLE_DISPOSABLE, bq_rows, recording_date)

    daily_counts = get_disposable_historical_counts()
    daily_counts = daily_counts.tail(12).reset_index(drop=True)

    oos_open = low_stock[(low_stock["ORDER #"] != " - ") & (pd.to_numeric(low_stock["SHOPIFY_STOCK_merged"], errors="coerce") <= 0)][["SKU", "Product Name", "AVG DAILY SALES", "SHOPIFY_STOCK_merged", "DATE_OUT_OF_STOCK", "#_OF_DAYS_0_STOCK", "IN-PRO VALOR", "ORDER #", "ORDER DATE", "Recording_date"]]
    oos_no = low_stock[(low_stock["ORDER #"] == " - ") & (pd.to_numeric(low_stock["SHOPIFY_STOCK_merged"], errors="coerce") <= 0)][["SKU", "Product Name", "AVG DAILY SALES", "SHOPIFY_STOCK_merged", "DATE_OUT_OF_STOCK", "#_OF_DAYS_0_STOCK", "KIT INVENTORY AT SC", "Recording_date"]]
    low_open = low_stock[(low_stock["ORDER #"] != " - ") & (pd.to_numeric(low_stock["SHOPIFY_STOCK_merged"], errors="coerce") > 0)][["SKU", "Product Name", "AVG DAILY SALES", "SHOPIFY_STOCK_merged", "STOCK IN DAYS", "IN-PRO VALOR", "ORDER #", "ORDER DATE", "Recording_date"]]

    body = f"""
        <html><body>
        <p>Hi Team,</p>
        <p>Disposable tracker has been updated (including ready_to_ship_but_not_uploaded qty):
        <a href="https://docs.google.com/spreadsheets/d/{DISPOSABLE_TRACKER_ID}">Disposable Tracker</a></p>
        <p>OOS and Low Stock SKU count by date AM/ PM :</p>
        {daily_counts.to_html(index=False, escape=False)}
        <br><p>OOS(open orders) detail {today_str} {am_pm}:</p>
        {oos_open.to_html(index=False, escape=False)}
        <br><p>OOS(no order) detail {today_str} {am_pm}:</p>
        {oos_no.to_html(index=False, escape=False)}
        <br><p>Low stock(open orders) detail {today_str} {am_pm}:</p>
        {low_open.to_html(index=False, escape=False)}
        <br><p>Best regards</p>
        </body></html>
        """
    send_email(email_config["sender_email"], email_config["sender_password"], EMAIL_RECIPIENTS_DISPOSABLE, f"test Disposable low stock {today_str} {am_pm}", body, [log_path])


def main() -> None:
    paths = make_paths()
    log_path = paths.logs / "Valor_shopify_inventory.log"
    setup_logging(log_path)
    logging.info("Starting Valor Shopify inventory job")

    shopify_config, dear_headers, email_config = load_configs()
    inventory_df, sku_inventory, stlth_reporting_sku, juice_reporting_sku = build_inventory(shopify_config, paths)
    run_stlth(sku_inventory, stlth_reporting_sku, email_config, paths, log_path)
    run_juice(sku_inventory, juice_reporting_sku, email_config, paths, log_path)
    run_disposable(inventory_df, sku_inventory, dear_headers, email_config, paths, log_path)
    upload_file_to_gcs(log_path, f"logs/{log_path.name}")
    logging.info("Valor Shopify inventory job completed")


if __name__ == "__main__":
    main()
