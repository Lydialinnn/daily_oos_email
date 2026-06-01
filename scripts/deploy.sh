#!/usr/bin/env bash
set -e

# ---- Configuration ----
JOB_NAME="daily-inventory-email"
REGION="northamerica-northeast2"
SCHEDULER_LOCATION="northamerica-northeast1"
PROJECT_ID="valor-sales"
SERVICE_ACCOUNT="valor-scheduler-invoker@valor-sales.iam.gserviceaccount.com"

# SCHEDULER_NAME="valor-shopify-inventory-twice-daily"
TIME_ZONE="America/Toronto"
# SCHEDULE="30 10,18 * * *"

# Paste exact GCS bucket and Secret Manager secret names here.
BUCKET_NAME="daily-email-gsheet-download"
SHOPIFY_SECRET_ID="valor_shopify_cred"
DEAR_SECRET_ID="VALOR_DEAR_cred"
EMAIL_SECRET_ID="auto_email_cred"
GOOGLE_PROJECT_SECRET_ID="Google_project_cred"


# ---- Deploy Cloud Run Job ----
gcloud run jobs deploy "$JOB_NAME" \
  --source . \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --service-account="$SERVICE_ACCOUNT" \
  --memory=2Gi \
  --task-timeout=3600 \
  --max-retries=0 \
  --set-env-vars="PROJECT_ID=$PROJECT_ID,BQ_PROJECT_ID=valor-sales,BQ_DATASET=valor_inventory_logs,GCS_BUCKET=$BUCKET_NAME,TIME_ZONE=$TIME_ZONE,SHOPIFY_SECRET_ID=$SHOPIFY_SECRET_ID,DEAR_SECRET_ID=$DEAR_SECRET_ID,EMAIL_SECRET_ID=$EMAIL_SECRET_ID,GOOGLE_PROJECT_SECRET_ID=$GOOGLE_PROJECT_SECRET_ID"

# ---- Cloud Scheduler trigger ----
# Scheduler deployment is disabled for now. Uncomment this block later when you
# want to schedule the job again.
#
# JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"
#
# if gcloud scheduler jobs describe "$SCHEDULER_NAME" \
#   --location="$SCHEDULER_LOCATION" \
#   --project="$PROJECT_ID" >/dev/null 2>&1; then
#   gcloud scheduler jobs update http "$SCHEDULER_NAME" \
#     --location="$SCHEDULER_LOCATION" \
#     --project="$PROJECT_ID" \
#     --schedule="$SCHEDULE" \
#     --time-zone="$TIME_ZONE" \
#     --uri="$JOB_URI" \
#     --http-method=POST \
#     --oauth-service-account-email="$SERVICE_ACCOUNT"
# else
#   gcloud scheduler jobs create http "$SCHEDULER_NAME" \
#     --location="$SCHEDULER_LOCATION" \
#     --project="$PROJECT_ID" \
#     --schedule="$SCHEDULE" \
#     --time-zone="$TIME_ZONE" \
#     --uri="$JOB_URI" \
#     --http-method=POST \
#     --oauth-service-account-email="$SERVICE_ACCOUNT"
# fi

echo "Deploy complete!"
