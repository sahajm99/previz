"""The screenplay model is the one piece of this product with no network in it,
so it is the one piece that can be tested exactly. Exact-match assertions, in the
style of tests/test_tools_storyboard.py.
"""
from app.screenplay import (LAYOUT, ON_ENTER, TAB_CYCLE, dialogue_block, infer,
                            next_type, parse, parse_slugline, split_scenes,
                            stats, to_text, cycle_type)

SCENE = """INT. BUS DEPOT OFFICE - NIGHT

Maya works the dispatch board alone. Rain on the glass.

MAYA
(not looking up)
You're early. That's new.

RAVI
Arre, boss, the bus does not wait for me.

CUT TO:

EXT. DEPOT YARD - NIGHT

Ravi does his walk-around.
"""


# ------------------------------------------------------------------ inference

def test_scene_heading_forms():
    for t in ("INT. OFFICE - NIGHT", "EXT. YARD - DAY", "INT./EXT. CAR - DUSK",
              "EST. THE DEPOT - DAWN", "I/E. BUS - NIGHT"):
        assert infer(t, None, True, "text") == ("scene_heading", t)


def test_transition_forms():
    assert infer("CUT TO:", None, True, "")[0] == "transition"
    assert infer("DISSOLVE TO:", None, True, "")[0] == "transition"
    assert infer("FADE IN:", None, True, "")[0] == "transition"
    assert infer("SMASH CUT TO:", None, True, "")[0] == "transition"


def test_character_cue_needs_dialogue_under_it():
    # A cue is a cue because something is spoken under it.
    assert infer("MAYA", None, True, "You're early.")[0] == "character"
    # All caps with nothing under it is action, not a cue. This is the case that
    # makes a naive all-caps rule produce a character called BANG.
    assert infer("BANG!", None, True, "")[0] == "action"


def test_character_cue_extensions():
    assert infer("MAYA (V.O.)", None, True, "Hello.")[0] == "character"
    assert infer("RAVI (CONT'D)", None, True, "As I said.")[0] == "character"


def test_dialogue_and_parenthetical_follow_a_cue():
    assert infer("(not looking up)", "character", False, "")[0] == "parenthetical"
    assert infer("You're early.", "character", False, "")[0] == "dialogue"
    assert infer("You're early.", "parenthetical", False, "")[0] == "dialogue"


def test_shot_lines():
    assert infer("ANGLE ON the route sheet", None, True, "")[0] == "shot"
    assert infer("INSERT - THE NOTICE", None, True, "")[0] == "shot"


def test_forced_elements():
    assert infer("@McMAYA", None, True, "hi") == ("character", "McMAYA")
    assert infer("!BANG", None, True, "") == ("action", "BANG")
    assert infer(">CUT TO:", None, True, "") == ("transition", "CUT TO:")
    assert infer(".INSIDE THE BUS", None, True, "x") == ("scene_heading",
                                                         "INSIDE THE BUS")


# --------------------------------------------------------------------- parsing

def test_parse_types_in_order():
    got = [l.type for l in parse(SCENE)]
    assert got == ["scene_heading", "action", "character", "parenthetical",
                   "dialogue", "character", "dialogue", "transition",
                   "scene_heading", "action"]


def test_parse_assigns_unique_ids():
    ids = [l.id for l in parse(SCENE)]
    assert len(ids) == len(set(ids))


def test_round_trip_is_stable():
    once = to_text(parse(SCENE))
    assert to_text(parse(once)) == once


def test_round_trip_preserves_types():
    first = parse(SCENE)
    again = parse(to_text(first))
    assert [l.type for l in again] == [l.type for l in first]


def test_dialogue_stays_attached_to_its_cue():
    # No blank line between a cue and what is spoken under it, or the round trip
    # reads the dialogue as action next time.
    text = to_text(parse("MAYA\nYou're early.\n"))
    assert text == "MAYA\nYou're early."


def test_blank_line_closes_a_dialogue_block():
    lines = parse("MAYA\nYou're early.\n\nShe turns away.\n")
    assert [l.type for l in lines] == ["character", "dialogue", "action"]


# --------------------------------------------------------------------- grammar

def test_enter_after_a_cue_gives_dialogue():
    assert next_type("character") == "dialogue"
    assert next_type("parenthetical") == "dialogue"


def test_enter_after_dialogue_leaves_the_block():
    assert next_type("dialogue") == "action"


def test_enter_after_a_heading_gives_action():
    assert next_type("scene_heading") == "action"
    assert next_type("transition") == "scene_heading"


def test_tab_cycles_and_wraps():
    assert cycle_type(TAB_CYCLE[-1]) == TAB_CYCLE[0]
    assert cycle_type("action") == TAB_CYCLE[TAB_CYCLE.index("action") + 1]


def test_every_element_has_layout_and_enter_rule():
    for el in LAYOUT:
        assert el in ON_ENTER
    assert set(LAYOUT) == set(ON_ENTER)


# --------------------------------------------------------------------- sluglines

def test_parse_slugline_splits_three_ways():
    assert parse_slugline("INT. BUS DEPOT OFFICE - NIGHT") == (
        "INT", "BUS DEPOT OFFICE", "NIGHT")


def test_parse_slugline_handles_no_time():
    assert parse_slugline("EXT. DEPOT YARD") == ("EXT", "DEPOT YARD", "")


def test_parse_slugline_keeps_hyphens_inside_a_location():
    assert parse_slugline("INT. DRIVE-THRU - DAY") == ("INT", "DRIVE-THRU", "DAY")


# ----------------------------------------------------------------------- scenes

def test_split_scenes_finds_both():
    scenes = split_scenes(parse(SCENE))
    assert [s["slugline"] for s in scenes] == ["INT. BUS DEPOT OFFICE - NIGHT",
                                               "EXT. DEPOT YARD - NIGHT"]
    assert scenes[0]["int_ext"] == "INT"
    assert scenes[0]["time_of_day"] == "NIGHT"


def test_split_scenes_keeps_leading_text_without_a_heading():
    scenes = split_scenes(parse("Two people wait.\n"))
    assert len(scenes) == 1
    assert scenes[0]["slugline"] == ""


# ------------------------------------------------------------------------ stats

def test_stats_counts_what_is_there():
    s = stats(parse(SCENE))
    assert s["scenes"] == 2
    assert s["speaking"] == ["MAYA", "RAVI"]
    assert s["elements"] == 10


def test_stats_pages_are_never_zero_for_a_written_page():
    assert stats(parse(SCENE))["pages"] >= 0.1


def test_stats_on_nothing():
    s = stats([])
    assert s["scenes"] == 0 and s["pages"] == 0.0 and s["speaking"] == []


# -------------------------------------------------------------- dialogue blocks

def test_dialogue_block_shape():
    got = [(l.type, l.text) for l in
           dialogue_block("Maya Raghavan", "You're early.", "not looking up")]
    assert got == [("character", "MAYA RAGHAVAN"),
                   ("parenthetical", "(not looking up)"),
                   ("dialogue", "You're early.")]


def test_dialogue_block_without_a_parenthetical():
    got = [l.type for l in dialogue_block("Ravi", "Arre, boss.")]
    assert got == ["character", "dialogue"]


def test_dialogue_block_drops_silence():
    assert dialogue_block("Maya", "[says nothing]") == []
