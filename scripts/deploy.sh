#!/usr/bin/env bash
# Deploy Magic Hour to Cloud Run.
#
#   ./scripts/deploy.sh
#   GOOGLE_OAUTH_CLIENT_ID=... ./scripts/deploy.sh
#
# The sequence is deploy with NO traffic, smoke test the new revision on its own
# URL, and only then move traffic. A broken revision therefore never serves a
# single user request: it is built, tested, found wanting, and left sitting there
# while the previous revision keeps working.
#
# That ordering is the difference between a deploy that can fail safely and one
# that takes the demo down while you read the logs.
#
# The design has two services, web public and agents internal only. That split is
# correct and is cancelled for today (docs/NOW.md): with one process there is no ID
# token to verify and no second cold start to pay for.
set -euo pipefail

PROJECT="${GCP_PROJECT:-nyu-ai-builder26nyc-9338}"
REGION="${GCP_REGION:-us-central1}"
SERVICE="${SERVICE:-magic-hour}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "· project $PROJECT · region $REGION · service $SERVICE"
gcloud config set project "$PROJECT" >/dev/null
gcloud services enable run.googleapis.com artifactregistry.googleapis.com >/dev/null

# Built locally and pushed, NOT via `gcloud run deploy --source`.
#
# --source hands the build to Cloud Build, whose default service account lacks
# roles/cloudbuild.builds.builder on this project, and granting it needs
# resourcemanager.projects.setIamPolicy which this account does not have. The
# failure is misleading too: it reports permission denied on the source bucket
# rather than on the build. Building here uses only permissions we already hold.
IMAGE="$REGION-docker.pkg.dev/$PROJECT/cloud-run-source-deploy/$SERVICE"
TAG="$(cd "$ROOT" && git rev-parse --short HEAD 2>/dev/null || echo manual)"
# Cloud Run tags allow lowercase alphanumerics and hyphens only, so the commit sha
# is stripped to be safe. Defined here, before the deploy that uses it.
TAGNAME="rev$(echo "$TAG" | tr -cd '[:alnum:]' | tail -c 8)"

echo "· building $IMAGE:$TAG"
gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet >/dev/null 2>&1

# Context is the repo root, not backend/, because the app reads
# data/seed/character_questions.json. Building from backend/ produces an image that
# boots, serves the UI, and 404s every /api route.
docker build -q -f "$ROOT/backend/Dockerfile" \
  -t "$IMAGE:$TAG" -t "$IMAGE:latest" "$ROOT" >/dev/null
docker push -q "$IMAGE:$TAG" >/dev/null
docker push -q "$IMAGE:latest" >/dev/null
echo "· pushed $TAG"

# --update-env-vars, never --set-env-vars. --set replaces the whole environment, so
# any deploy that did not happen to pass GOOGLE_OAUTH_CLIENT_ID would silently
# delete it and turn login off for everyone.
ENVS="GCP_PROJECT=$PROJECT,GCP_LOCATION=$REGION"
ENVS="$ENVS${GOOGLE_MAPS_API_KEY:+,GOOGLE_MAPS_API_KEY=$GOOGLE_MAPS_API_KEY}"
ENVS="$ENVS${GOOGLE_OAUTH_CLIENT_ID:+,GOOGLE_OAUTH_CLIENT_ID=$GOOGLE_OAUTH_CLIENT_ID}"

echo "· deploying with no traffic"
gcloud run deploy "$SERVICE" \
  --image "$IMAGE:$TAG" \
  --region "$REGION" \
  --allow-unauthenticated \
  --min-instances 1 \
  --memory 2Gi \
  --cpu 2 \
  --timeout 600 \
  --port 8080 \
  --no-traffic \
  --tag "$TAGNAME" \
  --update-env-vars "$ENVS" \
  --quiet

REV="$(gcloud run services describe "$SERVICE" --region "$REGION" \
        --format='value(status.latestCreatedRevisionName)')"
# The tagged URL addresses this revision specifically, so the smoke test measures
# the thing we just built and not whatever is currently serving traffic.
#
# Matched by grepping the tag rather than with --filter, because some gcloud
# builds reject --filter alongside --flatten and that combination silently
# produced an empty URL inside Cloud Build.
CAND="$(gcloud run services describe "$SERVICE" --region "$REGION" \
        --format='value(status.traffic[].url)' 2>/dev/null \
        | tr ';' '\n' | grep "$TAGNAME" | head -1)"
LIVE="$(gcloud run services describe "$SERVICE" --region "$REGION" \
        --format='value(status.url)')"

echo "· built revision $REV"

if [ -z "$CAND" ]; then
  # Refuse rather than cut over and test afterwards. Promoting first and checking
  # second is not a gate, it is an outage with a report attached.
  echo "  DEPLOY HELD. No tagged URL for $REV, so the new revision cannot be"
  echo "  tested in isolation. It is serving NO traffic and $LIVE is unchanged."
  exit 1
else
  if "$ROOT/scripts/smoke.sh" "$CAND"; then
    echo
    echo "· smoke passed, moving traffic to $REV"
    gcloud run services update-traffic "$SERVICE" --region "$REGION" \
      --to-revisions "$REV=100" --quiet >/dev/null
  else
    echo
    echo "  DEPLOY REJECTED. $REV failed its smoke test and is serving NO traffic."
    echo "  The previous revision is still live at $LIVE"
    echo "  Logs: gcloud run services logs read $SERVICE --region $REGION"
    exit 1
  fi
fi

echo
echo "  live   · $LIVE"
echo "  health · $LIVE/api/health"
echo "  commit · $TAG"
