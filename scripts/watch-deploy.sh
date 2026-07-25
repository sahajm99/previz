#!/usr/bin/env bash
# Continuous deployment, for a project where CI cannot be granted permissions.
#
#   ./scripts/watch-deploy.sh              polls origin/main every 60s
#   INTERVAL=30 ./scripts/watch-deploy.sh
#
# Watches origin/main and redeploys whenever the SHA moves. Anyone pushing to main
# sees it live in about three minutes with nothing to run themselves.
#
# WHY THIS EXISTS INSTEAD OF GITHUB ACTIONS
#
# Every keyless CI path on this project is blocked, and all of them for the same
# reason: they need an IAM change and this account cannot make one.
#
#   gcloud run deploy --source       needs roles/cloudbuild.builds.builder on the
#                                    Cloud Build service account
#   Workload Identity Federation     iam.workloadIdentityPools.create → DENIED
#   project IAM binding              resourcemanager.projects.setIamPolicy → DENIED
#   a service account key            blocked, and banned by the repo's own rules
#
# `editor` does not include setIamPolicy, and the project grants no owner role. So
# CI has to run as a principal that already has credentials, which means here.
#
# .github/workflows/deploy.yml is committed and correct. The moment somebody with
# owner access runs the one grant in its header, delete this script and use that.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTERVAL="${INTERVAL:-60}"
BRANCH="${BRANCH:-main}"
MAX_FAILS="${MAX_FAILS:-3}"
LAST=""
FAILS=0

# Watch the TEAM repo, not the fork. `upstream` is sahajm99/previz, which is where
# teammates actually merge; `origin` is a personal fork and watching it would mean
# a teammate's merge never reaches production while everything looks fine.
# Falls back to origin when there is no upstream, so a clone of the team repo
# itself still works unchanged.
if [ -z "${REMOTE:-}" ]; then
  if git remote get-url upstream >/dev/null 2>&1; then REMOTE=upstream; else REMOTE=origin; fi
fi

cd "$ROOT"
echo "· watching $REMOTE/$BRANCH ($(git remote get-url $REMOTE)) every ${INTERVAL}s"

while true; do
  git fetch "$REMOTE" "$BRANCH" --quiet 2>/dev/null || {
    echo "  fetch failed, retrying in ${INTERVAL}s"; sleep "$INTERVAL"; continue; }
  SHA="$(git rev-parse "$REMOTE/$BRANCH")"

  if [ "$SHA" != "$LAST" ]; then
    if [ -n "$LAST" ]; then
      echo
      echo "· origin/$BRANCH moved to ${SHA:0:8}: $(git log -1 --format=%s "$SHA" | head -c 72)"
    else
      echo "· starting at ${SHA:0:8}, deploying once to make the cloud match"
    fi

    # Deploy from a detached copy of the remote SHA, never from the working tree.
    # This matters: the first image shipped today was built from a dirty working
    # tree, so the running code matched no commit and there was nothing to bisect.
    WORK="$(mktemp -d)"
    if git worktree add --detach "$WORK" "$SHA" --quiet 2>/dev/null; then
      if (cd "$WORK" && SERVICE="${SERVICE:-magic-hour}" ./scripts/deploy.sh); then
        echo "· deployed ${SHA:0:8}"
        LAST="$SHA"
      else
        # Retry a few times, because the usual cause is a transient build or the
        # shared image quota. Then give up ON THIS SHA and wait for a new commit.
        # Retrying forever is worse than stopping: it burns build quota on code
        # that is not going to start working by itself, and it buries the first
        # real error under a hundred identical ones.
        FAILS=$((FAILS + 1))
        echo "· DEPLOY FAILED for ${SHA:0:8} (attempt $FAILS of $MAX_FAILS)"
        if [ "$FAILS" -ge "$MAX_FAILS" ]; then
          echo "· giving up on ${SHA:0:8}. Waiting for a new commit on $BRANCH."
          LAST="$SHA"
          FAILS=0
        fi
      fi
      git worktree remove --force "$WORK" 2>/dev/null || rm -rf "$WORK"
    else
      echo "· could not check out ${SHA:0:8}"
      rm -rf "$WORK"
    fi
  fi
  sleep "$INTERVAL"
done
