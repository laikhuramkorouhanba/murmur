from murmur import personality
from murmur import settings
from murmur.app import MurmurApp


def test_smooth_is_the_default_mode() -> None:
    personality.set_mode(personality.Mode.SMOOTH)

    assert personality.get_mode() is personality.Mode.SMOOTH
    assert settings.get("personality") == "Smooth"


def test_personality_choice_is_loaded_from_saved_settings() -> None:
    settings.set("personality", "Focused")

    assert personality.get_mode() is personality.Mode.FOCUSED


def test_unknown_saved_personality_falls_back_to_smooth() -> None:
    settings.set("personality", "Loud")

    assert personality.get_mode() is personality.Mode.SMOOTH


def test_focused_mode_keeps_summary_plain() -> None:
    personality.set_mode(personality.Mode.FOCUSED)

    assert personality.style_summary("One test failed in file dot py, line 6.", failed=True) == (
        "One test failed in file dot py, line 6."
    )


def test_test_voice_sample_is_one_sentence_in_each_mode() -> None:
    personality.set_mode(personality.Mode.SMOOTH)
    assert personality.sample_sentence() == "Ooh, Murmur is ready to roll, cool cat."

    personality.set_mode(personality.Mode.FOCUSED)
    assert personality.sample_sentence() == "Murmur voice check is ready."


def test_smooth_mode_varies_its_openers_and_keeps_facts_intact() -> None:
    personality.set_mode(personality.Mode.SMOOTH)
    facts = "One test failed in failing check dot py, line 6: it expected 19.8 but got 18."

    first = personality.style_summary(facts, failed=True)
    second = personality.style_summary(facts, failed=True)

    assert first != second
    assert facts in first and facts in second
    assert any(first.endswith(closer) for closer in personality._PLAYFUL_CLOSERS)


def test_smooth_becomes_encouraging_after_three_failures() -> None:
    personality.set_mode(personality.Mode.SMOOTH)
    personality.record_result(False)
    for _ in range(3):
        personality.record_result(True)

    result = personality.style_summary("One test failed.", failed=True)

    assert result.startswith("We'll take it one step at a time.") or result.startswith(
        "You've got this; let's work through it."
    ) or result.startswith("Let's take a breath and handle one detail at a time.")
    assert "cool cat" not in result
    assert not any(closer in result for closer in personality._PLAYFUL_CLOSERS)
    assert any(result.endswith(closer) for closer in personality._ENCOURAGING_CLOSERS)


def test_menu_selects_and_marks_the_current_mode() -> None:
    personality.set_mode(personality.Mode.SMOOTH)
    app = MurmurApp()
    menu = app.menu["Personality"]

    assert menu["Smooth"].state == 1
    assert menu["Focused"].state == 0

    app.choose_focused(None)

    assert personality.get_mode() is personality.Mode.FOCUSED
    assert settings.get("personality") == "Focused"
    assert menu["Smooth"].state == 0
    assert menu["Focused"].state == 1

    app.choose_smooth(None)
    assert personality.get_mode() is personality.Mode.SMOOTH
    assert menu["Smooth"].state == 1
    assert menu["Focused"].state == 0


def _split(result: str, openers, closers) -> tuple[str, str]:
    opener = next(o for o in openers if result.startswith(o))
    closer = next(c for c in closers if result.endswith(c))
    return opener, closer


def test_smooth_never_repeats_an_opener_or_closer_twice_in_a_row() -> None:
    personality.set_mode(personality.Mode.SMOOTH)
    personality.record_result(False)
    seen_openers, seen_closers = [], []

    for _ in range(60):
        result = personality.style_summary("One test failed.", failed=True)
        opener, closer = _split(result, personality._PLAYFUL_OPENERS, personality._PLAYFUL_CLOSERS)
        seen_openers.append(opener)
        seen_closers.append(closer)
        personality.record_result(False)

    assert all(a != b for a, b in zip(seen_closers, seen_closers[1:]))
    assert all(a != b for a, b in zip(seen_openers, seen_openers[1:]))
    # Real variety, not just two lines taking turns.
    assert len(set(seen_closers)) == len(personality._PLAYFUL_CLOSERS)
    assert len(set(seen_openers)) == len(personality._PLAYFUL_OPENERS)


def test_successes_get_a_varied_closer_too() -> None:
    personality.set_mode(personality.Mode.SMOOTH)
    closers = []
    for _ in range(20):
        result = personality.style_summary("All 12 tests passed.", failed=False)
        _, closer = _split(result, personality._SUCCESS_OPENERS, personality._SUCCESS_CLOSERS)
        closers.append(closer)

    assert all(a != b for a, b in zip(closers, closers[1:]))


def test_long_summaries_skip_the_closer() -> None:
    personality.set_mode(personality.Mode.SMOOTH)
    personality.record_result(False)
    facts = "Two tests failed. The first is in app dot py, line 3."

    result = personality.style_summary(facts, failed=True)

    assert result.endswith(facts)


def test_done_line_in_each_mode() -> None:
    personality.set_mode(personality.Mode.FOCUSED)
    assert personality.done_line() == "Done, no errors."

    personality.set_mode(personality.Mode.SMOOTH)
    lines = [personality.done_line() for _ in range(20)]
    assert all(line in personality._SMOOTH_DONE_LINES for line in lines)
    assert all(a != b for a, b in zip(lines, lines[1:]))
    assert "All done, smooth as butter." in personality._SMOOTH_DONE_LINES
