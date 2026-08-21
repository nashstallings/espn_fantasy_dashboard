#!/usr/bin/env bash
# One-time GCP setup: APIs, secrets, service account, Firestore, BigQuery tables.
# Safe to re-run — every step is idempotent.
set -euo pipefail

PROJECT="${PROJECT:-ff-python-api}"
REGION="${REGION:-us-central1}"
DATASET="${DATASET:-espn_fantasy}"
# BigQuery dataset location. Kept separate from REGION and defaulted to the US
# multi-region so this dataset matches the others already in this project —
# BigQuery cannot join across locations, and a lone us-central1 dataset would be
# permanently unjoinable to them.
BQ_LOCATION="${BQ_LOCATION:-US}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-espn-dashboard-api}"
SA_EMAIL="${SERVICE_ACCOUNT}@${PROJECT}.iam.gserviceaccount.com"
SCHEMA_DIR="$(dirname "$0")/bigquery/schemas"

# A service account is not immediately visible to the IAM policy service after
# it is created — binding a role to it seconds later fails with
# "Service account ... does not exist". That is propagation lag, not a real
# error, and it is the single most common way these scripts fall over.
#
# Retry with backoff, swallowing output until the last attempt so a genuine
# failure still surfaces its real message.
retry_iam () {
  local attempt
  for attempt in 1 2 3 4 5; do
    if "$@" >/dev/null 2>&1; then
      return 0
    fi
    echo "    IAM not settled yet, retrying in $((attempt * 5))s..."
    sleep $((attempt * 5))
  done
  "$@"  # final attempt, unsuppressed, so the real error is visible
}

echo "==> Checking billing"
# Cloud Run, BigQuery, and Secret Manager all need an active billing account.
# Without this check the run dies partway through on a raw API error, leaving
# the project half-provisioned.
if ! gcloud billing projects describe "${PROJECT}" --format='value(billingEnabled)' 2>/dev/null | grep -qi true; then
  echo "ERROR: billing is not enabled on ${PROJECT}." >&2
  echo "  gcloud billing accounts list" >&2
  echo "  gcloud billing projects link ${PROJECT} --billing-account=XXXXXX-XXXXXX" >&2
  exit 1
fi

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
  retry_iam gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member "serviceAccount:${SA_EMAIL}" --role "${ROLE}" --condition=None
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
  retry_iam gcloud secrets add-iam-policy-binding "${NAME}" \
    --member "serviceAccount:${SA_EMAIL}" --role roles/secretmanager.secretAccessor \
    --project "${PROJECT}"
}

create_secret espn-credential-key "$(python3 -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')"
create_secret espn-jwt-secret "$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
create_secret espn-sync-token "$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"

echo "==> BigQuery dataset and tables"
bq --project_id="${PROJECT}" show "${DATASET}" >/dev/null 2>&1 || \
  bq --project_id="${PROJECT}" --location="${BQ_LOCATION}" mk --dataset \
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
