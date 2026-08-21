#!/usr/bin/env bash
# Build and deploy the API to Cloud Run, then (re)point Cloud Scheduler at it.
set -euo pipefail

PROJECT="${PROJECT:-ff-python-api}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-espn-dashboard-api}"
DATASET="${DATASET:-espn_fantasy}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-espn-dashboard-api}"
SA_EMAIL="${SERVICE_ACCOUNT}@${PROJECT}.iam.gserviceaccount.com"
# The GitHub Pages origin(s) allowed to call this API.
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-https://nashstallings.github.io}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Deploying ${SERVICE} to ${REGION}"
gcloud run deploy "${SERVICE}" \
  --source "${ROOT}/backend" \
  --region "${REGION}" \
  --project "${PROJECT}" \
  --service-account "${SA_EMAIL}" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 5 \
  --memory 512Mi \
  --timeout 60s \
  --set-env-vars "CREDENTIAL_STORE=firestore,GCP_PROJECT=${PROJECT},BIGQUERY_DATASET=${DATASET},ALLOWED_ORIGINS=${ALLOWED_ORIGINS},ESPN_CACHE_TTL_SECONDS=60" \
  --set-secrets "CREDENTIAL_ENCRYPTION_KEY=espn-credential-key:latest,JWT_SECRET=espn-jwt-secret:latest,SYNC_TOKEN=espn-sync-token:latest"

URL="$(gcloud run services describe "${SERVICE}" --region "${REGION}" --project "${PROJECT}" --format 'value(status.url)')"
echo "==> Service URL: ${URL}"

echo "==> Cloud Scheduler daily snapshot"
SYNC_TOKEN="$(gcloud secrets versions access latest --secret espn-sync-token --project "${PROJECT}")"
JOB="${SERVICE}-daily-sync"
SCHEDULE_ARGS=(
  --schedule "0 9 * * *"          # 09:00 UTC — after Monday night games settle
  --time-zone "Etc/UTC"
  --uri "${URL}/internal/sync"
  --http-method POST
  --headers "X-Sync-Token=${SYNC_TOKEN}"
  --attempt-deadline 900s
  --location "${REGION}"
  --project "${PROJECT}"
)
if gcloud scheduler jobs describe "${JOB}" --location "${REGION}" --project "${PROJECT}" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "${JOB}" "${SCHEDULE_ARGS[@]}"
else
  gcloud scheduler jobs create http "${JOB}" "${SCHEDULE_ARGS[@]}"
fi

echo "==> Done."
echo "    Set API_BASE in frontend/config.js to ${URL}"
