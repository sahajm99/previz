# Magic Hour

**An AI filmmaking studio. Six tools that share one story bible, so nothing drifts.**

| Surface | What it does |
|---|---|
| **Bible** | The knowledge layer. Characters, scenes, locations, style. Everything reads from it. |
| **Script** | Screenplay editor with a writing partner that knows your story and your voice. |
| **Board** | Storyboards, shot by shot, with characters whose faces stay the same. |
| **Cast** | A 100 question interview that turns a name into someone who can speak. |
| **Scout** | Real locations for a scene, pinned to scene numbers. |
| **Muse** | Talk through the story. Not wired yet, the transport seam is specified. |

The thesis, and the thing to protect above everything else: **consistency is a
property of the knowledge layer, not of any single feature.** A character sounds
the same in scene 18 as in scene 3 because both calls read the same compiled Voice
Card, byte for byte. A face looks the same in shot 41 as in shot 2 because both
image calls condition on the same locked reference sheet. Then a referee measures
the output against a stored fingerprint and reports the number on screen.

**Drift is not caused by bad models. It is caused by re-deriving.** So identity is
compiled once, stored, reused verbatim, and recompiled only when canon changes.

Read [`docs/NOW.md`](docs/NOW.md) first. It is the current plan and it overrides
the 48 hour plans in `docs/superpowers/plans/`. The full architecture is
[`docs/superpowers/specs/2026-07-25-magic-hour-design.md`](docs/superpowers/specs/2026-07-25-magic-hour-design.md).

---

## Run it locally

```bash
./scripts/dev.sh          # macOS, Linux, Git Bash      http://127.0.0.1:8080
.\scripts\dev.ps1         # PowerShell
```

First run creates `backend/.venv` and installs requirements. After that it reloads
on save. Nothing else is needed: **the app opens on a seeded story with no network
call at all.** Two characters at 12 of 12 core answers with compiled Identity and
Voice Cards, three scenes, two real locations, a four frame board, 44 indexed
chunks.

To make the model calls work, once:

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project nyu-ai-builder26nyc-9338
```

**That second line is not optional.** Without a quota project, ADC bills against a
starvation tier bucket and every Vertex call returns 429 RESOURCE_EXHAUSTED, which
looks exactly like a broken app. It is the single most expensive gotcha here.

`GOOGLE_MAPS_API_KEY` in `backend/.env` turns on live Places search. Without it,
Scout returns the seeded locations and says so in the Trace tab.

## Deploy

```bash
./scripts/deploy.sh
```

One Cloud Run service, `min-instances 1` because cold starting a container that
imports the Vertex SDK is four to eight seconds and on a demo stage that reads as
broken. The script smoke tests `/healthz` on the new revision rather than trusting
that a URL got printed.

---

## Where things are

```
backend/app/
  consistency.py   Identity Cards, reference sheets, the face crop referee   BUILT
  voice.py         Voice Cards, per character sub-agents, the voice referee   BUILT
  store.py         entities and compiled cards, shaped like the Postgres schema
  bible.py         chunks, hybrid retrieval, the Continuity Pack
  questions.py     the 100 questions, 7 parts, 12 core
  seed.py          the seeded story, built from disk at startup
  sse.py           one event envelope for every stream
  images.py        frames to disk, served back over /cache
  api/
    bible.py       the bible, search, the Canon strip        Sampreeth
    cast.py        the interview and both compiled cards      kk
    board.py       shot lists, frames, the face referee       Sahaj
    script.py      dialogue and action, the voice referee     Sampreeth
    scout.py       locations                                  gaurav
  static/          the app shell: index.html, app.css, app.js
```

**Do not reimplement `consistency.py`, `voice.py`, `tools/locations.py` or
`data/seed/character_questions.json`.** They are built and verified by real calls.

Add a surface by adding a module under `app/api/` that exposes `router` and
including it in `app/api/__init__.py`. `main.py` mounts the API inside a try/except
so one half written tab cannot stop the app from booting.

## The two numbers

Everything else is in service of these, and both are on screen:

- **Face match.** Each generated frame's face is cropped, embedded with
  `multimodalembedding@001`, and compared by cosine to the character's reference
  sheet fingerprint. Badge on every frame. Threshold 0.35, calibrated.
- **Voice match.** Each generated line is embedded and compared to the centroid of
  that character's sample lines. Badge on every line. Threshold 0.62, calibrated
  against real generated lines rather than samples against their own centroid.

A score that could not be measured reads "no score" rather than defaulting to a
pass. A referee that silently passes everything is worse than no referee.

Cropping matters and was measured: whole frame embeddings **cannot** referee
identity. Two reference sheets of two different people score 0.48 because both are
grey three view sheets, so composition swamps identity. Crop the face first.

## API

`http://127.0.0.1:8080/docs` for the live schema. Every generating endpoint returns
Server Sent Events with one envelope, so the client has one parser:

```
run_start · thinking · tool_call · tool_result · context · partial
shot_ready · line_ready · proposal · violation · run_end · error
```

`context` carries the exact Continuity Pack the model received, slot by slot, which
is the difference between diagnosing a bad line in five seconds and in twenty
minutes.

## Facts measured on this project

- Project id is **`nyu-ai-builder26nyc-9338`**. The credential card omits the
  numeric suffix and the short form fails with a misleading permission error.
- **Never `gemini-flash-latest` or `gemini-pro-latest`.** AI Studio only aliases,
  they 404 on Vertex. Use `gemini-2.5-flash` and `gemini-2.5-pro`.
- **Gemini 3 returns 404 here.** No Nano Banana Pro.
- `gemini-2.5-pro` rejects `thinking_budget=0`. Use flash when you need thinking
  off, and raise `max_output_tokens`, or reasoning eats the budget and output
  comes back truncated mid word.
- The SDK's `embed_content` fails on `multimodalembedding@001` with "Empty
  instances". It is a predict style model, so call REST.
- **Image generation is about 30 seconds a frame** and the project is shared with
  every other team in the room. Cache every generated image to disk and commit it.
  Live generation on stage is a bonus that is allowed to fail.
- Cache the Vertex client. Constructing one per call gets it garbage collected mid
  flight and the next request dies with "client has been closed".
