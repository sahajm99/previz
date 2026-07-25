#!/usr/bin/env bash
# Deploy Magic Hour to Cloud Run. One command, one service.
#
#   ./scripts/deploy.sh
#
# The design has two services, web public and agents internal only. That split is
# correct and is cancelled for today (docs/NOW.md): with one process there is no
# ID token to verify and no second cold start to pay for.
#
# min-instances 1 because cold starting a container that imports the Vertex SDK is
# four to eight seconds, and on a demo stage that reads as broken.
set -euo pipefail

PROJECT="${GCP_PROJECT:-nyu-ai-builder26nyc-9338}"
REGION="${GCP_REGION:-us-central1}"
SERVICE="${SERVICE:-magic-hour}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "· project $PROJECT · region $REGION · service $SERVICE"
gcloud config set project "$PROJECT" >/dev/null

# sql-component alone is not enough for Cloud SQL, but we are not using it today.
# These three are what --source needs: Cloud Build to build, Artifact Registry to
# store, Cloud Run to serve.
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com >/dev/null

gcloud run deploy "$SERVICE" \
  --source "$ROOT/backend" \
  --region "$REGION" \
  --allow-unauthenticated \
  --min-instances 1 \
  --memory 2Gi \
  --cpu 2 \
  --timeout 600 \
  --port 8080 \
  --set-env-vars "GCP_PROJECT=$PROJECT,GCP_LOCATION=$REGION${GOOGLE_MAPS_API_KEY:+,GOOGLE_MAPS_API_KEY=$GOOGLE_MAPS_API_KEY}"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" \
        --format='value(status.url)')"
echo
echo "  $URL"
echo "  health · $URL/healthz"
echo

# Smoke test the revision rather than trusting that the deploy printed a URL. A
# service that deployed and does not answer is the failure worth catching here,
# not on stage.
if curl -fsS --max-time 30 "$URL/healthz" >/dev/null; then
  echo "  healthz answered"
else
  echo "  WARNING: healthz did not answer. Check: gcloud run services logs read $SERVICE --region $REGION"
  exit 1
fi
