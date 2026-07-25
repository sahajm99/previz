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

# Pick up secrets from backend/.env when they are not already exported.
#
# .env is gitignored, correctly, so it is not in the image and the container can
# only learn these at deploy time. Reading it here removes a real footgun: a
# deploy run without GOOGLE_OAUTH_CLIENT_ID exported used to be silently
# different from one with it, and the difference only showed up as a login page
# that would not log anyone in.
if [ -f "$ROOT/backend/.env" ]; then
  for VAR in GOOGLE_OAUTH_CLIENT_ID GOOGLE_MAPS_API_KEY; do
    if [ -z "${!VAR:-}" ]; then
      VAL="$(grep -m1 "^${VAR}=" "$ROOT/backend/.env" 2>/dev/null | cut -d= -f2- || true)"
      [ -n "$VAL" ] && export "$VAR=$VAL"
    fi
  done
fi

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
# --max-instances 1 is NOT a cost decision, it is a correctness one. Read this
# before raising it.
#
# The store is in process memory (docs/NOW.md cancelled Cloud SQL for today), and
# seed.build() generates fresh ids on every boot. So each instance holds a
# DIFFERENT story with different character and scene ids. With more than one
# instance you get: page loads on instance A, user clicks a character, request
# lands on instance B, 404. It presents as a random broken UI rather than as a
# scaling problem, and it gets worse the more people use it at once.
#
# This actually happened. The service ran at maxScale=10 and the logs filled with
# GET /api/characters/<id> 404 while several people had it open.
#
# --session-affinity pins a browser to one instance and is belt and braces on top.
#
# Raising max-instances is safe only once state lives outside the process.
gcloud run deploy "$SERVICE" \
  --image "$IMAGE:$TAG" \
  --region "$REGION" \
  --allow-unauthenticated \
  --min-instances 1 \
  --max-instances 1 \
  --session-affinity \
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

# Report the settings that break the app quietly when they are wrong, so a
# degraded deploy is visible here rather than discovered by a user mid demo.
SCALE="$(gcloud run services describe "$SERVICE" --region "$REGION" \
          --format='value(spec.template.metadata.annotations)' 2>/dev/null \
          | tr ',;' '\n' | grep maxScale | cut -d= -f2)"

echo
echo "  live   · $LIVE"
echo "  health · $LIVE/api/health"
echo "  commit · $TAG"
echo "  login  · $([ -n "${GOOGLE_OAUTH_CLIENT_ID:-}" ] && echo "on" || echo "OFF, everyone is one local user")"
echo "  maps   · $([ -n "${GOOGLE_MAPS_API_KEY:-}" ] && echo "on" || echo "OFF, Scout returns nothing")"

if [ "$SCALE" != "1" ]; then
  echo
  echo "  WARNING: max instances is '$SCALE', not 1."
  echo "  The store is in process memory, so every instance has its own story with"
  echo "  its own ids. Users will hit 404 on characters and scenes at random."
  echo "  Fix: gcloud run services update $SERVICE --region $REGION --max-instances=1"
fi
