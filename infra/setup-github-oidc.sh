#!/usr/bin/env bash
# One-time setup so GitHub Actions can deploy to Cloud Run without a key file.
#
# Workload Identity Federation lets GitHub's OIDC token be exchanged directly
# for short-lived Google credentials. Nothing long-lived is ever stored in the
# repository — the alternative, a service account JSON key in GitHub secrets, is
# a permanent credential that leaks the moment the repo or a workflow log does.
#
# Run this once, in Cloud Shell. Safe to re-run.
set -euo pipefail

PROJECT="${PROJECT:-ff-python-api}"
GITHUB_OWNER="${GITHUB_OWNER:-nashstallings}"
GITHUB_REPO="${GITHUB_REPO:-espn_fantasy_dashboard}"
POOL="${POOL:-github}"
PROVIDER="${PROVIDER:-github-actions}"
DEPLOYER="${DEPLOYER:-github-deployer}"
RUNTIME_SA="${RUNTIME_SA:-espn-dashboard-api}"

DEPLOYER_EMAIL="${DEPLOYER}@${PROJECT}.iam.gserviceaccount.com"
RUNTIME_EMAIL="${RUNTIME_SA}@${PROJECT}.iam.gserviceaccount.com"

echo "==> Enabling APIs"
gcloud services enable \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  artifactregistry.googleapis.com \
  --project "${PROJECT}"

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"

echo "==> Deploy service account"
gcloud iam service-accounts describe "${DEPLOYER_EMAIL}" --project "${PROJECT}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${DEPLOYER}" \
    --display-name "GitHub Actions deployer (ESPN dashboard)" --project "${PROJECT}"

# Deploy-time permissions only. Note this set deliberately excludes
# secretmanager.secretAccessor: the workflow runs deploy.sh with
# MANAGE_SCHEDULER=false, so CI never reads the sync token.
for ROLE in \
  roles/run.admin \
  roles/cloudbuild.builds.editor \
  roles/artifactregistry.writer \
  roles/storage.objectAdmin
do
  gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member "serviceAccount:${DEPLOYER_EMAIL}" --role "${ROLE}" --condition=None >/dev/null
done

# Deploying a service that *runs as* the runtime account requires impersonating
# it. Scoped to that one account rather than granted project-wide.
echo "==> Letting the deployer act as ${RUNTIME_SA}"
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_EMAIL}" \
  --member "serviceAccount:${DEPLOYER_EMAIL}" \
  --role roles/iam.serviceAccountUser \
  --project "${PROJECT}" >/dev/null

echo "==> Workload identity pool"
gcloud iam workload-identity-pools describe "${POOL}" \
  --location=global --project "${PROJECT}" >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "${POOL}" \
    --location=global --display-name "GitHub Actions" --project "${PROJECT}"

echo "==> OIDC provider"
# The attribute-condition is the security boundary. Without it, ANY GitHub
# repository on the internet could present a token and impersonate the deployer.
if ! gcloud iam workload-identity-pools providers describe "${PROVIDER}" \
      --workload-identity-pool="${POOL}" --location=global \
      --project "${PROJECT}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER}" \
    --workload-identity-pool="${POOL}" \
    --location=global \
    --display-name "GitHub Actions OIDC" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
    --attribute-condition="assertion.repository_owner == '${GITHUB_OWNER}'" \
    --project "${PROJECT}"
fi

echo "==> Binding the repository to the deploy account"
# Narrowed to this one repository — not the whole owner. Any other repo under
# the same owner still cannot impersonate this account.
gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_EMAIL}" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_OWNER}/${GITHUB_REPO}" \
  --project "${PROJECT}" >/dev/null

PROVIDER_PATH="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"

cat <<SUMMARY

════════════════════════════════════════════════════════════════════
Done. Add these two GitHub Actions *variables* (not secrets — neither
value is sensitive, and variables are visible in logs which helps when
debugging a failed deploy):

  https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/settings/variables/actions

  GCP_WORKLOAD_IDENTITY_PROVIDER
    ${PROVIDER_PATH}

  GCP_DEPLOY_SERVICE_ACCOUNT
    ${DEPLOYER_EMAIL}

The deploy workflow stays inert until both are set, so it will not fail
on pushes in the meantime.
════════════════════════════════════════════════════════════════════
SUMMARY
