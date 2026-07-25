#!/usr/bin/env bash
# Smoke test one deployment. Used by scripts/deploy.sh and by CI, so both gate on
# exactly the same checks.
#
#   ./scripts/smoke.sh https://magic-hour-xxxx.us-central1.run.app
#   ./scripts/smoke.sh http://127.0.0.1:8080
#
# THE CHECK THAT MATTERS is /api/health returning 200.
#
# main.py mounts the API inside a try/except so a half written tab cannot stop the
# app from booting. The cost of that choice is a failure mode where the container
# starts, serves the entire UI, and 404s every /api route. `curl /` returns 200 and
# everything looks fine. It has already happened once here, when the image was
# built without data/seed and questions.py raised at import.
#
# /api/health lives INSIDE the api package, so if the API did not mount, this
# 404s and the deploy fails. That is the whole point: a check that lives outside
# the thing it is checking cannot detect the thing being gone.
set -euo pipefail

URL="${1:?usage: smoke.sh <base-url>}"
URL="${URL%/}"
FAIL=0

say()  { printf "  %-7s %s\n" "$1" "$2"; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 40 "$1" 2>/dev/null || echo 000; }

check() {          # check <expected> <path> <why>
  local want="$1" path="$2" why="$3" got
  got="$(code "$URL$path")"
  if [ "$got" = "$want" ]; then
    say "ok" "$path → $got"
  else
    say "FAIL" "$path → $got, expected $want ($why)"
    FAIL=1
  fi
}

echo "· smoke testing $URL"

# 1. The API actually mounted. Everything else is cosmetic next to this.
BODY="$(curl -s --max-time 40 "$URL/api/health" 2>/dev/null || echo '')"
if [ -z "$BODY" ] || ! printf '%s' "$BODY" | grep -q '"ok"'; then
  say "FAIL" "/api/health did not return a health document"
  say "" "the API did not mount, or the container is not answering"
  say "" "check: gcloud run services logs read magic-hour --region us-central1"
  exit 1
fi
say "ok" "/api/health mounted"

# 2. The seed built. A story id and a nonzero chunk count together mean the
#    knowledge layer came up, not just the process.
CHUNKS="$(printf '%s' "$BODY" | sed -n 's/.*"chunks":\([0-9]*\).*/\1/p')"
if [ "${CHUNKS:-0}" -gt 0 ]; then
  say "ok" "bible indexed, $CHUNKS chunks"
else
  say "FAIL" "0 chunks: the seed did not build"
  FAIL=1
fi

# 3. The UI and its assets. A deploy that serves the API and not app.js is a
#    blank page to every user.
check 200 "/"                 "the page itself"
check 200 "/static/app.css"   "styles"
check 200 "/static/app.js"    "the client"

# 4. Auth wiring, in whichever mode this deployment is in. Both modes are valid,
#    so the test reads the mode from health rather than assuming one.
check 200 "/api/auth/config"  "sign-in config must be reachable or nobody can log in"

if printf '%s' "$BODY" | grep -q '"auth_required":true'; then
  say "ok" "auth is ON, checking the guard holds"
  # A guarded route must refuse an anonymous caller. If this returns 200 the guard
  # is not applied and the API is open to the internet.
  check 401 "/api/story"      "guarded route must reject anonymous callers"
  # And the sign-in screen has to be able to render before anyone has a token.
  check 200 "/api/auth/whoami" "the gate needs this before sign-in"
else
  say "ok" "auth is OFF, story should be open"
  check 200 "/api/story"      "open mode must serve the story"
fi

# 5. Cached frames. The demo leans on these when generation is slow or quota is out.
check 200 "/cache/maya_shot1.png" "committed demo frames must be in the image"

echo
if [ "$FAIL" -eq 0 ]; then
  echo "  PASS"
else
  echo "  FAIL: one or more checks failed"
  exit 1
fi
