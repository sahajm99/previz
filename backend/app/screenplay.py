"""The screenplay document model. Elements, the grammar, and the parser.

OWNER: Sampreeth. This is the engine under the Screenwriter canvas.

A screenplay is not rich text. It is a sequence of typed elements, and every
formatting decision follows from the type rather than from the writer choosing a
font. So the document model is a flat list of `Line(type, text)` and the editor
never stores styling at all: it stores a type per block and renders from `LAYOUT`.

Three things follow from that, and they are the reason this module exists rather
than the editor holding a string:

  * The margins are data, not CSS. `LAYOUT` is served to the browser, so the page
    on screen and the page written to disk cannot disagree about what a character
    cue looks like.
  * The keyboard is data too. `ON_ENTER` and `TAB_CYCLE` are the whole of Final
    Draft's muscle memory, and serving them means the editor and the agents share
    one definition of what follows a character cue.
  * Agents emit typed elements, not text. `dialogue_block()` turns a Voice Card
    line into exactly three elements, so an accepted proposal lands already
    formatted and there is no second parse to disagree with the first.

There is no network in this file, so it is the one part of the product that is
exactly testable. Keep it that way.
"""
from __future__ import annotations

import itertools
import re
import textwrap
from dataclasses import asdict, dataclass

ELEMENTS = ("scene_heading", "action", "character", "parenthetical", "dialogue",
            "transition", "shot")

# Industry margins in characters, for 12pt Courier at 10 characters an inch on a
# US Letter page with a 1.5 inch left margin. A page is 55 rows.
#
# These are the real numbers, not an approximation of the look: a character cue
# sits 3.7 inches from the paper edge, which is 22 characters in from a 1.5 inch
# margin. Getting them right is most of why a page reads as a screenplay.
ROWS_PER_PAGE = 55
LAYOUT: dict[str, dict] = {
    "scene_heading": {"indent": 0,  "width": 60, "case": "upper",
                      "space_before": 2, "label": "Scene", "key": "Ctrl+1"},
    "action":        {"indent": 0,  "width": 60, "case": "none",
                      "space_before": 1, "label": "Action", "key": "Ctrl+2"},
    "character":     {"indent": 22, "width": 38, "case": "upper",
                      "space_before": 1, "label": "Character", "key": "Ctrl+3"},
    "parenthetical": {"indent": 16, "width": 26, "case": "none",
                      "space_before": 0, "label": "Parenthetical", "key": "Ctrl+4"},
    "dialogue":      {"indent": 10, "width": 35, "case": "none",
                      "space_before": 0, "label": "Dialogue", "key": "Ctrl+5"},
    "transition":    {"indent": 0,  "width": 60, "case": "upper",
                      "space_before": 1, "label": "Transition", "key": "Ctrl+6",
                      "align": "right"},
    "shot":          {"indent": 0,  "width": 60, "case": "upper",
                      "space_before": 1, "label": "Shot", "key": "Ctrl+7"},
}

# What pressing Enter at the end of a block produces. This table is the entire
# reason writing in a screenplay editor feels different from writing in a text
# box: a cue is followed by speech, speech is followed by action, and a
# transition is followed by a new scene, without anyone choosing.
ON_ENTER: dict[str, str] = {
    "scene_heading": "action",
    "action": "action",
    "character": "dialogue",
    "parenthetical": "dialogue",
    "dialogue": "action",
    "transition": "scene_heading",
    "shot": "action",
}

# Tab cycles through types. Ordered by how often a writer actually reaches for
# the next one, not alphabetically.
TAB_CYCLE: tuple[str, ...] = ("action", "character", "dialogue", "parenthetical",
                              "scene_heading", "transition", "shot")

# Offered as autocomplete in the slugline builder, so a location and a time of day
# are picked rather than typed differently every time. Consistent sluglines are
# what let split_scenes() and the retrieval index agree about what a scene is.
INT_EXT = ("INT.", "EXT.", "INT./EXT.", "EST.")
TIMES = ("DAY", "NIGHT", "DAWN", "DUSK", "MORNING", "AFTERNOON", "EVENING",
         "CONTINUOUS", "LATER", "MOMENTS LATER", "4 AM")
TRANSITIONS = ("CUT TO:", "DISSOLVE TO:", "SMASH CUT TO:", "MATCH CUT TO:",
               "FADE IN:", "FADE OUT.", "FADE TO BLACK.", "INTERCUT WITH:")

_SCENE = re.compile(
    r"^(INT\./EXT\.?|EXT\./INT\.?|I/E\.?|INT\.?|EXT\.?|EST\.?)([ .\-]|$)", re.I)
_TRANS = re.compile(
    r"^(FADE (IN|OUT|TO BLACK)|CUT TO|DISSOLVE TO|SMASH CUT TO|MATCH CUT TO|"
    r"WIPE TO|IRIS (IN|OUT)|INTERCUT WITH|THE END)\b", re.I)
_TRANS_SUFFIX = re.compile(r"\bTO:$")
# Deliberately case sensitive, unlike the patterns above. A shot line is written
# with the cue upper case and the rest in normal case ("ANGLE ON the route
# sheet"), so we cannot require the whole line to be upper. That leaves the cue
# itself as the only signal, and with re.I this would read "Insert the key into
# the lock" as a shot rather than as action.
_SHOT = re.compile(
    r"^(ANGLE ON|CLOSE ON|CLOSER ON|WIDE ON|WIDER|POV|INSERT|BACK TO|"
    r"INTERCUT|MONTAGE|SERIES OF SHOTS|AERIAL|UNDERWATER|REVERSE ANGLE)\b")
# A cue may carry an extension: MAYA (V.O.), RAVI (CONT'D).
_CUE_EXT = re.compile(r"\s*\((V\.?O\.?|O\.?S\.?|O\.?C\.?|CONT'?D|OFF|"
                      r"PRE-?LAP|FILTERED|ON PHONE)\)\s*$", re.I)

_seq = itertools.count(1)


@dataclass
class Line:
    """One typed element. `id` is stable for the life of a request so the editor
    can address a block, and is deliberately not part of equality or round trip.
    """
    id: str
    type: str
    text: str

    def json(self) -> dict:
        return asdict(self)


def _line(type_: str, text: str) -> Line:
    return Line(id=f"l{next(_seq):05d}", type=type_, text=text)


# ------------------------------------------------------------------- inference

def infer(text: str, prev_type: str | None, after_blank: bool,
          next_text: str) -> tuple[str, str]:
    """Type one line. Returns the type and the text with any force marker gone.

    `next_text` is needed for one specific reason: a character cue is a cue
    because something is spoken under it. Without lookahead, an all caps action
    line like "BANG!" parses as a character named BANG, which is the single most
    common failure of a naive Fountain parser.
    """
    s = text.strip()
    if not s:
        return "action", ""

    # Fountain force markers. An explicit decision by the writer outranks every
    # heuristic below it.
    if s[0] == "@":
        return "character", s[1:].strip()
    if s[0] == "!":
        return "action", s[1:].strip()
    if s[0] == ">":
        return "transition", s[1:].strip()
    if s[0] == "." and not s.startswith(".."):
        return "scene_heading", s[1:].strip()

    upper = s == s.upper()

    if _SCENE.match(s):
        return "scene_heading", s
    if upper and (_TRANS.match(s) or _TRANS_SUFFIX.search(s)):
        return "transition", s
    if _SHOT.match(s):
        return "shot", s
    if s.startswith("(") and s.endswith(")"):
        return "parenthetical", s

    # A cue: upper case, short, opens a block, and has speech under it.
    bare = _CUE_EXT.sub("", s)
    if (upper and after_blank and next_text.strip()
            and len(s) <= 45 and not s.endswith(":")
            and re.search(r"[A-Z]", bare)):
        return "character", s

    if prev_type in ("character", "parenthetical", "dialogue"):
        return "dialogue", s
    return "action", s


def parse(text: str) -> list[Line]:
    """Text to typed elements.

    Blank lines are separators, not content: they close a dialogue block and they
    tell `infer` that the next line opens something. Spacing on the page comes
    from `LAYOUT.space_before` instead, so a document cannot carry two different
    ideas of how much air sits above a scene heading.
    """
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[Line] = []
    prev_type: str | None = None
    after_blank = True
    for i, line in enumerate(raw):
        if not line.strip():
            after_blank = True
            if prev_type in ("character", "parenthetical", "dialogue"):
                prev_type = None        # the dialogue block is closed
            continue
        nxt = raw[i + 1] if i + 1 < len(raw) else ""
        t, s = infer(line, prev_type, after_blank, nxt)
        if s:
            out.append(_line(t, s))
            prev_type = t
        after_blank = False
    return out


def to_text(lines: list[Line]) -> str:
    """Elements back to Fountain. Round trips through `parse` unchanged.

    The only subtlety is that a cue, its parenthetical and its speech are one
    block with no blank lines inside it. Put a blank line in there and the next
    parse reads the speech as action, which is how a document silently degrades
    every time it is saved.
    """
    parts: list[str] = []
    prev: str | None = None
    for l in lines:
        glued = (prev in ("character", "parenthetical")
                 and l.type in ("dialogue", "parenthetical")) or \
                (prev == "dialogue" and l.type == "dialogue")
        if prev is None:
            parts.append(l.text)
        elif glued:
            parts.append("\n" + l.text)
        else:
            parts.append("\n\n" + l.text)
        prev = l.type
    return "".join(parts)


# --------------------------------------------------------------------- grammar

def next_type(current: str) -> str:
    """What Enter produces after a block of this type."""
    return ON_ENTER.get(current, "action")


def cycle_type(current: str) -> str:
    """What Tab produces from this type. Wraps."""
    try:
        i = TAB_CYCLE.index(current)
    except ValueError:
        return TAB_CYCLE[0]
    return TAB_CYCLE[(i + 1) % len(TAB_CYCLE)]


def grammar() -> dict:
    """Everything the editor needs to behave like a screenplay editor.

    Served rather than duplicated in JavaScript. Two copies of this table would
    drift within a day, and the symptom would be an editor that disagrees with
    the parser about what the writer typed.
    """
    return {"elements": list(ELEMENTS), "layout": LAYOUT, "on_enter": ON_ENTER,
            "tab_cycle": list(TAB_CYCLE), "rows_per_page": ROWS_PER_PAGE,
            "int_ext": list(INT_EXT), "times": list(TIMES),
            "transitions": list(TRANSITIONS)}


# ------------------------------------------------------------------- sluglines

def parse_slugline(text: str) -> tuple[str, str, str]:
    """"INT. BUS DEPOT OFFICE - NIGHT" to ("INT", "BUS DEPOT OFFICE", "NIGHT").

    Splits the time of day on a spaced hyphen only, so "INT. DRIVE-THRU - DAY"
    keeps its location intact.
    """
    s = text.strip()
    m = _SCENE.match(s)
    int_ext = ""
    if m:
        int_ext = m.group(1).upper().rstrip(".")
        s = s[m.end(1):].lstrip(". -")
    bits = re.split(r"\s+[-–]\s+", s)
    if len(bits) > 1:
        return int_ext, bits[0].strip(), bits[-1].strip()
    return int_ext, s.strip(), ""


def build_slugline(int_ext: str, location: str, time_of_day: str = "") -> str:
    head = int_ext.upper().rstrip(".")
    out = f"{head}. {location.upper().strip()}"
    return f"{out} - {time_of_day.upper().strip()}" if time_of_day.strip() else out


# --------------------------------------------------------------------- scenes

def split_scenes(lines: list[Line]) -> list[dict]:
    """Split at scene headings. Text before the first heading is still a scene,
    because a writer starting mid page has not written a heading yet and losing
    what they typed is not an acceptable answer.
    """
    scenes: list[dict] = []

    def open_scene(slug: str) -> dict:
        ie, loc, tod = parse_slugline(slug) if slug else ("", "", "")
        sc = {"slugline": slug, "int_ext": ie, "location": loc,
              "time_of_day": tod, "lines": []}
        scenes.append(sc)
        return sc

    cur: dict | None = None
    for l in lines:
        if l.type == "scene_heading":
            cur = open_scene(l.text)
            continue
        if cur is None:
            cur = open_scene("")
        cur["lines"].append(l)
    for sc in scenes:
        sc["number"] = scenes.index(sc) + 1
        sc["elements"] = len(sc["lines"])
    return scenes


# ---------------------------------------------------------------------- stats

def _rows(l: Line) -> int:
    spec = LAYOUT.get(l.type, LAYOUT["action"])
    wrapped = textwrap.wrap(l.text, width=max(spec["width"], 10)) or [""]
    return len(wrapped) + spec.get("space_before", 1)


def speaking(lines: list[Line]) -> list[str]:
    """Who speaks, in order of first appearance, cue extensions removed."""
    out: list[str] = []
    for l in lines:
        if l.type != "character":
            continue
        name = _CUE_EXT.sub("", l.text).strip().upper()
        if name and name not in out:
            out.append(name)
    return out


def stats(lines: list[Line]) -> dict:
    """Page count and shape. One page is 55 rows, which is the only page count
    a screenplay has ever used and the reason a page is roughly a minute.
    """
    rows = sum(_rows(l) for l in lines)
    words = sum(len(l.text.split()) for l in lines
                if l.type in ("action", "dialogue", "parenthetical"))
    return {"elements": len(lines),
            "rows": rows,
            "pages": round(rows / ROWS_PER_PAGE, 2),
            "eighths": round(rows / ROWS_PER_PAGE * 8) if rows else 0,
            "scenes": sum(1 for l in lines if l.type == "scene_heading"),
            "speaking": speaking(lines),
            "words": words,
            "dialogue_rows": sum(_rows(l) for l in lines if l.type == "dialogue"),
            "action_rows": sum(_rows(l) for l in lines if l.type == "action")}


# ------------------------------------------------------------- agent output

def dialogue_block(name: str, line: str,
                   parenthetical: str = "") -> list[Line]:
    """One spoken line as the three elements it actually is.

    Agents return a name and a string. Turning that into typed elements here,
    once, is what lets an accepted proposal land already formatted instead of
    being re-parsed into whatever the parser guesses.

    `[says nothing]` is the sentinel `voice.speak` returns when silence is truer
    for the character. Silence is not an empty line of dialogue, it is the
    absence of one, so it produces no elements at all.
    """
    if not line or line.strip() == "[says nothing]":
        return []
    out = [_line("character", name.strip().upper())]
    p = parenthetical.strip()
    if p:
        if not p.startswith("("):
            p = f"({p})"
        out.append(_line("parenthetical", p))
    out.append(_line("dialogue", line.strip()))
    return out


def action_block(text: str) -> list[Line]:
    """Action, split on blank lines so two paragraphs stay two elements."""
    return [_line("action", p.strip()) for p in re.split(r"\n\s*\n", text.strip())
            if p.strip()]


def lines_json(lines: list[Line]) -> list[dict]:
    return [l.json() for l in lines]
