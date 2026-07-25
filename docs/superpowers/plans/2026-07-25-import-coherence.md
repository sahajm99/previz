# Import Coherence Plan

**Goal:** an imported script produces a coherent, face consistent storyboard,
not a generic one grounded in the wrong story.

**Owner:** Sahaj (Board). Reuses Cast and consistency, does not reimplement them.

## The problem

Import today makes scenes but not characters, not cast, and not a bible, and it
appends to the seeded story. So the consistency engine (Identity Cards, reference
sheets, the face referee) has nothing to attach to, and an imported scene's shot
planning retrieves context from "The Night Route" bible rather than from the
script that was imported. The result: shots for an imported script render as
generic, inconsistent people, and the context is the wrong story's.

## Decisions (locked)

- **Story scoping: replace the current story.** Import wipes the active story's
  scenes, cast, locations and bible, and rebuilds all of them from the script.
  One script, one story, one bible. The seeded story returns on restart.
- **Depth: minimal coherent slice first.** Story, characters, and an Identity
  Card plus a reference sheet for the leads. Voice Cards, the knowledge horizon,
  and a multi story switcher are deferred to a later pass.

## The coherence contract this enforces

1. One script is one story is one bible. Every retrieval pulls only from this
   script's knowledge.
2. The script's characters are registered, and the leads are cast, so the face
   locks across scenes.
3. Text and index never disagree: scenes and character facts are indexed the
   moment they are written.

The knowledge horizon (who knows what, by scene) is part of full coherence and is
deferred here.

## The flow after this change

1. **Import (fast, text only):** replace the story, name it from the script,
   create its scenes, register its characters, and compile a text Identity Card
   (descriptor, wardrobe, negative) for each from the script. Reindex.
2. **Cast the leads (streamed, images):** generate a reference sheet for the main
   character or two, so the referee has a face to lock. This is the only image
   cost in the import path, and it is explicit and streamed, matching the board's
   "decide in text before you pay for pixels" order.
3. **Plan or render any scene:** characters now resolve to real cast members with
   cards and sheets, so `_render_shots` conditions on them. No board change is
   needed here, it works once the characters exist and are cast.

## Tasks (minimal slice)

### Task 1 · Import replaces and names the story
`app/api/board.py` `import_script`:
- Derive `{title, logline, summary}` from the script in one `gemini-2.5-flash`
  call.
- Reset the active story: clear `scenes`, `characters`, `locations`, `proposals`,
  and set the derived title, logline and summary.
- Add the parsed scenes (existing logic).
Reuses: `store`, `screenplay`.

### Task 2 · Extract and register characters
- Collect distinct character names from the parsed lines: `screenplay.speaking`
  for cues, plus a light pass over action lines for recurring all caps names.
  Dedup case insensitively.
- `store.add_character(name)` for each, and set every scene's `characters` to the
  ids that speak or appear in it.
Reuses: `store.add_character`, `screenplay.speaking`.

### Task 3 · Text Identity Card per character, from the script
- New helper (Board's module): read each character's lines and mentions, one text
  call, return `{descriptor, wardrobe, negative}`, construct a
  `consistency.IdentityCard`, and store it on the `Character` at canon version 1.
- No image here. The descriptor alone gives textual consistency, because it is
  pasted into every image prompt verbatim.
Reuses: the `IdentityCard` shape, `store`.

### Task 4 · Cast the leads (reference sheets, streamed)
- Pick the leads: the top one or two characters by dialogue line count.
- Reuse the existing Cast sheet generation to produce a reference sheet per lead,
  store `sheet_url`, and register the card so the referee and the conditioning
  path pick it up.
- Stream it (SSE) with progress, like the existing Cast flow.
Reuses: the Cast surface sheet generation, the `consistency` referee.

### Task 5 · Reindex and guardrail
- Call `reindex_story` after import so the bible reflects the new script.
- Board guardrail: a shot whose character has no sheet shows "cast for face lock"
  (the `no_reference_sheet` violation already exists, surface it in the card).

## Ownership and coordination

- `app/api/board.py` (Board, Sahaj): the import rework, character extraction, and
  the text Identity Card derivation.
- Reuse without editing where possible: `consistency.py` (IdentityCard, referee,
  sheet generation), `bible.reindex_story`, `store`.
- If a compile from script helper is cleaner living inside `consistency.py`, ask
  in chat first, it is on the do not edit list. Otherwise keep the derivation in
  Board's module and construct the `IdentityCard` directly.

## Deferred to full coherence

- Voice Cards derived from each character's dialogue lines.
- `knows` and `story_edges`, so the knowledge horizon holds for imported scripts.
- A multi story switcher, so an import and the seeded story can coexist.
- Per user stores, which needs Cloud SQL. The store is a single global today.
