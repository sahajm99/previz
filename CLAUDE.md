# Magic Hour · rules for every session working in this repo

Read [`docs/NOW.md`](docs/NOW.md) before anything else. It is the current plan and
it overrides the 48 hour plans in `docs/superpowers/plans/`.

## Commit small, commit often, push immediately

**This is the rule that matters most when several people are building at once.**

- **Push after every working change, not after every finished feature.** If it
  imports and the app boots, push it.
- **Aim for a push every 10 to 15 minutes.** If you have been working for 30
  minutes without pushing, stop and split what you have.
- **One change per commit.** "Add the cast endpoint" is a commit. "Build the
  character builder" is a week.
- **A half finished tab behind a disabled button is worth more than a perfect tab
  that never merges.** Ship the seam, then fill it in.
- **Never hold work locally to make it complete first.** Someone else is probably
  blocked on the half you already have, and a big first push is a big first
  conflict.
- **Rebase before every push:** `git fetch origin && git rebase origin/main && git push`

Why: the whole point is watching localhost change. A long branch means nobody sees
your work, nobody can build on it, and the merge lands as one painful lump at the
worst possible time.

## No test driven development. Build first, verify minimally.

Speed is the constraint. **Do not write tests before code. Do not write test
suites.**

- Write the feature. Run it once. Confirm the app still boots. Push.
- Verification means a single smoke check: does the endpoint return 200, does the
  page render, does the function return the right shape. One command, not a suite.
- Existing tests should keep passing, but do not add to them and do not chase
  coverage.
- The real verification is localhost. Click the thing. If it works, it works.

Do not spend time on: fixtures, mocks, parametrised cases, edge case tests,
calibration harnesses. If something is broken you will see it on screen faster
than a test would tell you.

## Never break the boot

The app must start even when your part is unfinished. Return a stub, return
`{"ready": false}`, return an empty list. `main.py` mounts the API inside a
try/except for exactly this reason. **A demo that will not start is worse than a
feature that is missing.**

## Do not edit these without saying so in chat first

`app/main.py` · `app/config.py` · `app/gemini_client.py` · `app/models.py` ·
`app/store.py` · `app/consistency.py` · `app/voice.py` · `requirements.txt`

Everyone needs them, so two people editing one is a conflict for no reason. Need a
setting or a dependency? Ask, and one person adds it.

## Do not reimplement these. They are built and verified.

| Module | What it already does |
|---|---|
| `app/consistency.py` | Identity Cards, reference sheets, shots conditioned on a locked reference, and the face-crop referee that scores drift |
| `app/voice.py` | Voice Cards, one dialogue sub-agent per character, and the voice referee |
| `app/tools/locations.py` | `places:searchText` with the correct field mask |
| `data/seed/character_questions.json` | The 100 questions, 7 parts, 12 flagged `is_core` |

## Facts measured on this project, so nobody rediscovers them

- **Project id is `nyu-ai-builder26nyc-9338`.** The credential card omits the
  numeric suffix, and the short form fails with a misleading permission error.
- **429 on every Vertex call means ADC has no quota project:**
  `gcloud auth application-default set-quota-project nyu-ai-builder26nyc-9338`
  Without it you are on a starvation-tier bucket and nothing works.
- **Never use `gemini-flash-latest` or `gemini-pro-latest`.** AI Studio only
  aliases, they 404 on Vertex. Use `gemini-2.5-flash` / `gemini-2.5-pro`.
- **Gemini 3 returns 404 on this project.** No Nano Banana Pro.
- **`gemini-2.5-pro` rejects `thinking_budget=0`.** Use `gemini-2.5-flash` when you
  need thinking off, and raise `max_output_tokens`, or reasoning tokens eat the
  whole budget and output comes back truncated mid-word.
- **The SDK's `embed_content` fails on `multimodalembedding@001`** with "Empty
  instances". It is a predict-style model, so call REST. See
  `consistency.embed_image`.
- **Image generation is about 30 seconds per frame** and the project is shared
  with every other team. **Cache every generated image to disk and commit it.**
  Live generation on stage is a bonus that is allowed to fail.
- **Cache the Vertex client.** Constructing one per call gets it garbage collected
  mid flight and the next request dies with "Cannot send a request, as the client
  has been closed."
- **Use `google.auth.default()`, never `gcloud auth print-access-token`.** gcloud
  does not exist in the Cloud Run container, so shelling out works locally and
  fails the instant you deploy.

## Consistency is the product

A character must look and sound the same in scene 18 as in scene 3. The mechanism
is: **compile identity once, store it, reuse it verbatim, recompile only when
canon changes, and measure every output against what is stored.**

Never re-derive a character's look or voice per call. Each summary differs slightly
from the last, nothing looks broken at any single step, and by scene 18 she is a
different person. Drift is not caused by bad models, it is caused by re-deriving.

The `knowledge horizon` is only as tight as the scene text around it. `scene` is
what a camera in the room could see. Anything one character knows and another does
not goes in their `knows` list, never in the shared scene brief.

## Writing style for anything a user reads

No em-dashes, en-dashes, or hyphens used as dashes. Use commas, periods, or "and".
Empty labels use a middot (·) or are hidden, never a dash.
