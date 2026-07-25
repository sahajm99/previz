# NOW · the next 100 minutes

**Written 13:20 EDT 2026-07-25. Deadline 15:30. Stop coding at 15:00 and rehearse.**

This file overrides both plans in `docs/superpowers/plans/` for the duration of the
hackathon. Those plans are correct and they are for a 48 hour build. We have
about 100 minutes. Read this instead.

## Cancelled. Do not start any of these today.

Cloud SQL and pgvector · Terraform · CI/CD · Workload Identity · login and auth ·
the Next.js rewrite · the ADK migration · the MCP server · contract codegen ·
splitting into two Cloud Run services · moving `backend/` to `apps/agents/`.

Cloud SQL alone is eight minutes of provisioning before the first debugging
round trip. Every item above is real engineering with zero demo value inside 100
minutes.

## Keep. This is the whole build.

Everything runs in **one FastAPI process**, the one already at `backend/app/`.
State in memory plus a JSON seed file. Retrieval is cosine over roughly 50
chunks in numpy, which is instant and needs no infrastructure. One
`gcloud run deploy --source backend` at 15:00.

## What is already proven, by real calls

Measured against `nyu-ai-builder26nyc-9338` at 13:07 today:

- `gemini-2.5-flash-image` (Nano Banana) **holds a character's face** across a
  completely different composition and lighting setup when conditioned on a
  reference sheet. Verified by eye, not by metric. This is the demo.
- `gemini-2.5-pro`, `-flash`, `-flash-lite`, `gemini-embedding-001` and
  `multimodalembedding@001` all answer. Gemini 3 returns 404 on this project, so
  there is no Nano Banana Pro.
- `gemini-flash-latest` **404s on Vertex**. It is an AI Studio alias. This is
  currently in `gemini_client.py` and must change to `gemini-2.5-flash`.
- The SDK's `embed_content` **fails** on `multimodalembedding@001` with "Empty
  instances". That model needs a raw REST `:predict` call. Already handled in
  `consistency.py:embed_image`.

## What the spike also caught, so nobody rediscovers it

**Whole-frame embeddings cannot referee identity.** Numbers from the spike:

```
Maya's sheet vs Maya's 3 shots:   0.39  0.39  0.51
Ravi's sheet vs Maya's 3 shots:   0.20  0.28  0.31    <- control
Maya's sheet vs Ravi's sheet:     0.48    <- two DIFFERENT people, scored HIGH
```

`multimodalembedding@001` encodes the entire image, so composition and lighting
swamp identity. Two reference sheets of two different people score 0.48 because
both are grey three-view sheets. **Fix: crop the face first, then embed.**
`consistency.py:crop_face` does this with a Gemini bounding box call.

**Style drifts even when identity does not.** The spike's sheet was photoreal and
the first shot came back as a digital painting, because the prompt said
"storyboard frame" and nothing pinned the medium. `STYLE_PRESETS` in
`consistency.py` pins it, and every shot in a board must use the same preset
string verbatim.

## Who does what

| Who | Tab | Use this | Done when |
|---|---|---|---|
| **Sahaj** | Authentication + Imagen (storyboard) | **`backend/app/consistency.py` is yours now.** `compile_identity_card()` → `generate_reference_sheet()` → `fingerprint()` → `generate_shot_with_referee()`. That last one returns `(frame_bytes, {name: Verdict}, attempts)` and the Verdict carries `.score`. Show the score on each frame. | Shot 1 and shot 6 are visibly the same person, with a score badge per frame |
| **kk / kik728** | Character builder | **`data/seed/character_questions.json`.** 100 questions, 7 parts, and 12 flagged `is_core`. Ask the 12 core first, they gate a character as usable for dialogue. Emit a flat `{question_text: answer}` dict, which is exactly what `compile_identity_card()` and `compile_voice_card()` consume. | A character with 12 answers produces both cards |
| **gaurav / gp2610** | Location scouting | `backend/app/tools/locations.py` already does `places:searchText` correctly with the field mask. Add photos via Places Photos and **cache them to disk**, because Places photo URLs expire and the demo cannot depend on a live fetch. | Real places, real photos, real coordinates, attachable to a scene |
| **Sampreeth** | Knowledge base + Script builder + front and back end | `backend/app/voice.py`. Voice Cards, per-character dialogue sub-agents, the voice referee. | Three characters in a scene each speak in their own measurably distinct voice |

### Contract between tabs, so nobody blocks

Character builder produces `{question_text: answer}`. That dict is the only input
the Identity Card and Voice Card compilers need. Everything downstream, faces and
voices both, is derived from it. So kk's tab unblocks Sahaj's and mine, and
nothing else is coupled.

### The one gotcha that will cost you 15 minutes

If every Vertex call returns **429 RESOURCE_EXHAUSTED**, your ADC has no quota
project and you are being billed against a starvation-tier bucket:

    gcloud auth application-default set-quota-project nyu-ai-builder26nyc-9338

That single command was the difference between everything failing and everything
working. Image generation also runs about **30 seconds per frame** and the
project is shared with every other team in the room, so **cache every generated
image to disk and commit it.** Live generation on stage is a bonus that is
allowed to fail, not the demo.

## Demo spine, five minutes

1. Open on the seeded story. Story bible with 3 characters, scenes, locations.
2. Open a character. Show their Identity Card and Voice Card, both compiled from
   the interview answers. Point out that these are frozen and reused verbatim.
3. Generate a storyboard for one scene. Frames stream in. **Each frame shows its
   face match score.** Regenerate one and watch the score move.
4. Scroll back to shot 1 and forward to shot 6. Same person. That is the point.
5. Write one line of dialogue. Show that three character subagents each got their
   own Voice Card. Read the line. It sounds like her.
6. Location scouting on one scene, real places, real photos.

## Rules for the last hour

- **15:00 is a hard stop on features.** After that: rehearse, cache fallbacks,
  fix only crashes.
- **Cache every demo asset.** Generated sheets and frames go to disk and get
  committed. If the venue wifi dies or the lab project expires mid demo, the app
  still shows a full board.
- **The lab project expires today.** Do not put anything in it we cannot lose.
- Hard cap on image generation per story so a loop bug cannot eat the budget.

---

## Working in parallel without merge hell

Five people on five branches. The rules exist because a conflict at 15:20 is
unrecoverable.

### The one rule

**Never edit `backend/app/main.py`.** Routers in `backend/app/routers/` are
auto-discovered. Your tab is one file in that package and nothing else.

| Branch | Your file, yours alone | Build on this. Do not rewrite it. |
|---|---|---|
| `feat/board` · Sahaj | `app/routers/board.py` | `app.consistency`: `compile_identity_card` → `generate_reference_sheet` → `fingerprint` → `generate_shot_with_referee`. The last returns `(frame_bytes, {name: Verdict}, attempts)`; `Verdict.score` is the number to show on each frame. |
| `feat/characters` · kk | `app/routers/characters.py` | `data/seed/character_questions.json`. Ask the 12 `is_core` first. Emit `{question_text: answer}`, which is the only input both card compilers need. |
| `feat/locations` · gaurav | `app/routers/locations.py` | `app.tools.locations.find_locations` is already correct. Add Places Photos and **cache the images to disk**, because Places photo URLs expire. |
| `feat/story` · Sampreeth | `app/routers/story.py` | `app.voice`: `compile_voice_card`, `write_exchange`, `referee_line`. **These are built and verified. Wrap them, do not reimplement them.** |
| `feat/knowledge` · Sampreeth | `app/routers/knowledge.py` | Nothing exists yet. This owns the store that supplies `voice.write_exchange(knows=...)`. |
| `feat/frontend` | `app/static/` only | Call `/api/<tab>/…`. Never import from `app/` directly. |

### Shared files: change these only by asking in chat first

`app/main.py` · `app/config.py` · `app/gemini_client.py` · `app/models.py` ·
`requirements.txt` · `app/consistency.py` · `app/voice.py`

If you need a new setting or dependency, say so in chat and one person adds it.
Two people adding a line to `requirements.txt` is a conflict for no reason.

### Merge discipline

Rebase on `main` before you push, every time:

    git fetch origin && git rebase origin/main && git push

Merge early and often. Five branches merging at once in the last ten minutes is
how this goes wrong. A half finished tab on `main` behind a disabled button is
worth more than a perfect tab that never merges.

Your router returning `{"ready": false}` is fine. It means the app still boots and
everyone else is unblocked.
