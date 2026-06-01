import json
import os
from functools import lru_cache

from google.cloud import secretmanager
from google.oauth2 import service_account
from oauth2client.service_account import ServiceAccountCredentials


def _project_id() -> str:
    return os.getenv("PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or "valor-sales"


@lru_cache(maxsize=1)
def service_account_info() -> dict:
    if os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])

    secret_id = os.getenv("GOOGLE_PROJECT_SECRET_ID")
    if not secret_id:
        raise RuntimeError(
            "Set GOOGLE_PROJECT_SECRET_ID to the Secret Manager secret containing Google_project.json, "
            "or set GOOGLE_SERVICE_ACCOUNT_JSON."
        )

    name = secret_id if secret_id.startswith("projects/") else f"projects/{_project_id()}/secrets/{secret_id}/versions/latest"
    client = secretmanager.SecretManagerServiceClient()
    payload = client.access_secret_version(request={"name": name}).payload.data.decode("utf-8")
    return json.loads(payload)


def google_credentials(scopes):
    return service_account.Credentials.from_service_account_info(service_account_info(), scopes=scopes)


def oauth2client_credentials(scopes):
    return ServiceAccountCredentials.from_json_keyfile_dict(service_account_info(), scopes)
