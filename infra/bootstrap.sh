#!/usr/bin/env bash
# One-time GCP setup: APIs, secrets, service account, Firestore, BigQuery tables.
# Safe to re-run — every step is idempotent.
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT to your GCP project id}"
REGION="${REGION:-us-central1}"
DATASET="${DATASET:-espn_fantasy}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-espn-dashboard-api}"
SA_EMAIL="${SERVICE_ACCOUNT}@${PROJECT}.iam.gserviceaccount.com"
SCHEMA_DIR="$(dirname "$0")/bigquery/schemas"

echo "==> Enabling APIs"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  firestore.googleapis.com \
  bigquery.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  --project "${PROJECT}"

echo "==> Service account"
gcloud iam service-accounts describe "${SA_EMAIL}" --project "${PROJECT}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${SERVICE_ACCOUNT}" \
    --display-name "ESPN Fantasy Dashboard API" --project "${PROJECT}"

for ROLE in roles/datastore.user roles/bigquery.dataEditor roles/bigquery.jobUser; do
  gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member "serviceAccount:${SA_EMAIL}" --role "${ROLE}" --condition=None >/dev/null
done

echo "==> Firestore (native mode) for encrypted credentials"
gcloud firestore databases describe --project "${PROJECT}" >/dev/null 2>&1 || \
  gcloud firestore databases create --location="${REGION}" --type=firestore-native \
    --project "${PROJECT}"

echo "==> Secrets"
create_secret () {
  local NAME="$1" VALUE="$2"
  if gcloud secrets describe "${NAME}" --project "${PROJECT}" >/dev/null 2>&1; then
    echo "    ${NAME} already exists, leaving it alone"
    return
  fi
  gcloud secrets create "${NAME}" --replication-policy=automatic --project "${PROJECT}"
  printf '%s' "${VALUE}" | gcloud secrets versions add "${NAME}" --data-file=- --project "${PROJECT}"
  gcloud secrets add-iam-policy-binding "${NAME}" \
    --member "serviceAccount:${SA_EMAIL}" --role roles/secretmanager.secretAccessor \
    --project "${PROJECT}" >/dev/null
}

create_secret espn-credential-key "$(python3 -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')"
create_secret espn-jwt-secret "$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
create_secret espn-sync-token "$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"

echo "==> BigQuery dataset and tables"
bq --project_id="${PROJECT}" show "${DATASET}" >/dev/null 2>&1 || \
  bq --project_id="${PROJECT}" --location="${REGION}" mk --dataset \
    --description "ESPN fantasy football league history" "${DATASET}"

for TABLE in leagues team_week matchups power_rankings; do
  if bq --project_id="${PROJECT}" show "${DATASET}.${TABLE}" >/dev/null 2>&1; then
    echo "    ${TABLE} exists; updating schema"
    bq --project_id="${PROJECT}" update "${DATASET}.${TABLE}" "${SCHEMA_DIR}/${TABLE}.json"
  else
    # Partitioned by snapshot_date and clustered by league so per-league history
    # scans stay cheap as seasons pile up.
    bq --project_id="${PROJECT}" mk --table \
      --time_partitioning_field snapshot_date \
      --time_partitioning_type DAY \
      --clustering_fields league_key \
      "${DATASET}.${TABLE}" "${SCHEMA_DIR}/${TABLE}.json"
  fi
done

echo "==> Done. Next: infra/deploy.sh"
