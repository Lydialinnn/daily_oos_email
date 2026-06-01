# Valor Shopify Inventory Cloud Run Job

Cloud Run Job conversion for `Valor_shopify_inventory.py`.

## What it does

- Runs the Shopify / Google Sheets / DEAR / email workflow twice daily.
- Stores every CSV that the old script wrote under `/Users/stlth/Downloads/*.csv` in GCS.
- Uses the latest GCS copy as a fallback when a Google Sheet download fails.
- Writes the three historical log datasets to BigQuery:
  - `valor-sales.valor_inventory_logs.STLTH_tracker_daily_low_stock_log`
  - `valor-sales.valor_inventory_logs.Juice_tracker_daily_low_stock_log`
  - `valor-sales.valor_inventory_logs.Valor_disposable_daily_low_stock_log`

## Required GCP setup

Use existing Secret Manager secrets, or create secrets containing the same JSON currently used locally:

- Shopify config JSON: keys `Store_Key`, `Shopify_store_password`
- DEAR config JSON: whatever headers your local `Valor_DEAR_config.json` contains
- Email config JSON: keys `sender_email`, `sender_password`
- Google service account JSON: your current `Google_project.json`

The Cloud Run service account needs:

- `roles/secretmanager.secretAccessor`
- `roles/storage.objectAdmin` on the bucket
- `roles/bigquery.dataEditor` on `valor_inventory_logs`
- `roles/bigquery.jobUser` on project `valor-sales`
- Google Sheets access by sharing the sheets with the service account email

## Deploy

Put your existing bucket name and Secret Manager secret names in `scripts/deploy.sh`, then run:

```bash
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```

You can still override values from the command line later, but you do not need to.

Optional helper scripts are included only if you ever want them:

- `scripts/create_or_update_secrets.sh` creates or updates secrets from local JSON files.
- `scripts/bootstrap_bucket_files.sh` uploads local CSVs into the bucket as initial fallback files.

The scheduler is created with:

```cron
30 10,18 * * *
```

in timezone `America/Toronto`.

## GitHub Actions deployment

This repo includes `.github/workflows/deploy-cloud-run-job.yml`.

Every push to `main` or `master` runs `scripts/deploy.sh`, which deploys the
Cloud Run Job from the current repo source.

Add this GitHub repository secret:

- `GCP_SA_KEY`: the full JSON key for a Google service account allowed to deploy
  the job.

That deploy service account needs permission to:

- deploy Cloud Run jobs in `valor-sales`
- act as `valor-scheduler-invoker@valor-sales.iam.gserviceaccount.com`
- start Cloud Build builds for `gcloud run jobs deploy --source .`
- write build images/artifacts used by Cloud Run source deployments
